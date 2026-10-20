#!/usr/bin/env python3
"""Rotate through registered channel archives; verified uploads are never pending again.

This is a local Mac runner, not a server deployment. --sync-folder explicitly
opts into Review-only queue imports. There is no scheduling or publication.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import archive_worker as worker
from scripts.local_worker import api_get,api_post


def sync_review(server,token,folder):
    receipt=api_post(server,token,'/api/drive/sync',{
        'folder_id':folder,'pipeline':'Movie Clips','auto_approve':False})
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        job=api_get(server,token,'/api/jobs/'+receipt['job_id'])
        if job['status'] not in ('running','queued','stopping'):
            if job['status']!='done':raise RuntimeError('Review-only Drive sync failed: '+str(job.get('message')))
            return job
        time.sleep(2)
    raise RuntimeError('Drive sync is still running; inspect the recorded job before retrying')


def run(server,token,rows,workdir,state_dir,*,batch_size=25,start_after=None,sync_folder=None,browser_profile=None):
    parsed=urlsplit(server)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or
        (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1')))):
        raise ValueError('Use HTTPS or isolated localhost HTTP')
    if not isinstance(batch_size,int) or isinstance(batch_size,bool) or batch_size<1:
        raise ValueError('Use a positive batch size')
    if browser_profile is not None:worker.source.browser_cookie_args(browser_profile)
    rows=list(rows);ids=[r['channel_id'] for r in rows]
    if not ids or len(set(ids))!=len(ids) or any(not worker.source.CHANNEL_ID.fullmatch(cid) for cid in ids):
        raise ValueError('Catalog must contain unique canonical channel IDs')
    if start_after is not None:
        index=ids.index(start_after)+1;rows=rows[index:]+rows[:index]
    state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
    state={'pid':os.getpid(),'status':'running','batch_size':batch_size,'round':0,
           'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'results':[],
           'inventory_unavailable':{},'channel_errors':{},'syncs':[],
           'authentication':'owner_selected_local_brave' if browser_profile is not None else 'public'}

    def save():
        state['updated_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        tmp=state_dir/'batch-state.tmp';tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        tmp.replace(state_dir/'batch-state.json')

    def snapshot():
        channels=[c for c in api_get(server,token,'/api/channel-archives')['channels'] if c['channel_id'] in ids]
        state['channels']=channels
        state['totals']={k:sum(c['counts'][k] for c in channels)
                         for k in ('discovered','downloaded','uploaded','skipped','failed','remaining')}
        state['uploaded_bytes']=sum(c.get('uploaded_bytes',0) for c in channels)
        state['all_channels_complete']=(len(channels)==len(ids) and
            all(c['inventory']['complete'] and c['counts']['remaining']==0 for c in channels))
        save()

    save()
    try:
        while True:
            state['round']+=1;progress=0
            for row in rows:
                cid=row['channel_id']
                current=api_get(server,token,f'/api/channel-archives/{cid}?limit=1')
                if not current['inventory']['complete']:
                    state['inventory_unavailable'][cid]=row.get('handle',cid);continue
                if current['counts']['remaining']==0 or cid in state['channel_errors']:continue
                state['current_channel']=row.get('handle',cid);save()
                print(f"Round {state['round']}: {state['current_channel']}, up to {batch_size} verified Shorts",flush=True)
                result={'round':state['round'],'channel_id':cid,'handle':state['current_channel']}
                try:
                    options={'browser_profile':browser_profile} if browser_profile is not None else {}
                    result['verified_this_batch']=worker.drain(server,token,cid,workdir,max_videos=batch_size,**options)
                    result['status']='batch_finished'
                except Exception as exc:
                    result.update(status='stopped',error_type=type(exc).__name__,error=str(exc)[-2000:])
                    state['channel_errors'][cid]=result['error']
                after=api_get(server,token,f'/api/channel-archives/{cid}?limit=1')
                result['counts']=after['counts'];state['results'].append(result)
                progress+=max(0,current['counts']['remaining']-after['counts']['remaining'])
                snapshot();print(json.dumps(result,ensure_ascii=False),flush=True)
                error=result.get('error','').lower()
                if any(x in error for x in ('lease lost','http error 409','claim no longer','stopped by owner')):
                    state['status']='stopped';return state
                if any(x in error for x in ('not a bot','captcha','sign in','http error 403',
                                            'http error 429','quota','storage reserve','http error 507')):
                    state['status']='blocked';return state
                if sync_folder and result.get('verified_this_batch',0):
                    state['syncs'].append(sync_review(server,token,sync_folder));save()
            snapshot()
            if state['all_channels_complete']:
                state['status']='complete';return state
            if not progress:
                state['status']='partial';return state
    except BaseException as exc:
        state['status']='interrupted' if isinstance(exc,KeyboardInterrupt) else 'error'
        state['error']=str(exc)[-2000:];raise
    finally:
        try:snapshot()
        except Exception as exc:state['snapshot_error']=str(exc)[-1000:]
        state['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();save()
        print('Batch runner ended: '+json.dumps({k:state.get(k) for k in ('status','totals','all_channels_complete')}),flush=True)


def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--server',required=True)
    p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--catalog',type=Path,default=root/'docs/implementation/ARCHIVE_CHANNELS.json')
    p.add_argument('--workdir',type=Path,default=root/'data/archive-worker')
    p.add_argument('--state-dir',type=Path,required=True)
    p.add_argument('--batch-size',type=int,default=25)
    p.add_argument('--start-after',help='Canonical channel already served; begin with the next catalog channel')
    p.add_argument('--sync-folder',help='Explicitly import verified Drive videos as Review only after each batch')
    p.add_argument('--brave-profile',help='Owner-selected Brave Profile Path; session use stays on this Mac')
    a=p.parse_args()
    result=run(a.server,a.token_file.read_text().strip(),json.loads(a.catalog.read_text())['channels'],
               a.workdir,a.state_dir,batch_size=a.batch_size,start_after=a.start_after,sync_folder=a.sync_folder,browser_profile=a.brave_profile)
    return 0 if result['status']=='complete' else 2


if __name__=='__main__':sys.exit(main())
