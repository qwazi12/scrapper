#!/usr/bin/env python3
"""Archive public authorized Shorts on the Mac; keep bytes until verified Drive receipt.

No cookies, proxy, bot-challenge workarounds or Drive credential transfer. Uses
existing authenticated streaming transport. Server/token come from deploy.env or
environment; never print the token. --inventory registers a complete local public
inventory when server-side metadata retrieval is unavailable.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import uuid
from urllib.parse import urlencode,urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.local_worker import api_get,api_post,load_env,multipart_video,log_msg
from backend.app import channel_sources as source

WORKER=uuid.uuid4().hex


@contextlib.contextmanager
def claimed(server,token,claim):
    stopped,lost=threading.Event(),threading.Event()
    def beat():
        while not stopped.wait(claim['renew_after_seconds']):
            try:api_post(server,token,f"/api/archive-workers/{claim['token']}/renew",{'worker_id':WORKER,'generation':claim['generation']})
            except Exception:lost.set();return
    thread=threading.Thread(target=beat,daemon=True,name='archive-worker-renew');thread.start()
    try:yield lost
    finally:stopped.set();thread.join(timeout=1)


def download(cid,vid,directory,bound,cancelled):
    info_file=directory/(vid+'.info.json')
    videos=[p for p in directory.glob(vid+'.*') if p.suffix in ('.mp4','.mkv','.webm','.mov')]
    if info_file.is_file() and len(videos)==1 and videos[0].stat().st_size<=bound:
        info=json.loads(info_file.read_text())
        if info.get('id')==vid and info.get('channel_id')==cid:return videos[0],info
    return source.download(cid,vid,directory,bound,cancelled=cancelled)


def drain(server,token,cid,workdir):
    u=urlsplit(server)
    if u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost')):
        raise ValueError('Use HTTPS, or isolated localhost HTTP')
    if not source.CHANNEL_ID.fullmatch(cid):raise ValueError('Use a canonical channel ID')
    cap=api_get(server,token,f'/api/channel-archives/{cid}/capacity')
    log_msg('Capacity: '+json.dumps(cap))
    claim=api_post(server,token,f'/api/archive-workers/channels/{cid}/claim',{'worker_id':WORKER})
    auth={'worker_id':WORKER,'generation':claim['generation']}
    base=f"/api/archive-workers/{claim['token']}";completed=0
    try:
        with claimed(server,token,claim) as lost:
            while True:
                if lost.is_set():raise RuntimeError('Archive lease lost; local files retained')
                items=api_get(server,token,base+'/pending?'+urlencode(auth))['items']
                if not items:break
                item=items[0];vid=item['video_id'];directory=Path(workdir).resolve()/cid/vid
                directory.mkdir(parents=True,exist_ok=True)
                if shutil.disk_usage(directory).free<2*claim['max_bytes']+256*1024*1024:raise RuntimeError('Mac temporary storage reserve reached')
                try:
                    video,info=download(cid,vid,directory,claim['max_bytes'],lost)
                except source.AccessRestricted as exc:
                    reason=str(exc)[-2000:];halt=any(x in reason.lower() for x in ('not a bot','captcha','http error 403','http error 429'))
                    api_post(server,token,base+f'/videos/{vid}/failure',{**auth,'reason':reason,'restricted':True,'halt':halt})
                    if halt:raise
                    continue
                except Exception as exc:
                    api_post(server,token,base+f'/videos/{vid}/failure',{**auth,'reason':str(exc)[-2000:],'restricted':False})
                    raise
                if info.get('id')!=vid or info.get('channel_id')!=cid:raise RuntimeError('Downloaded identity differs from the ledger')
                with video.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
                if lost.is_set():raise RuntimeError('Archive lease lost; upload skipped')
                fields={**auth,'metadata':json.dumps({k:info.get(k) for k in ('id','channel_id','duration','upload_date','is_live','live_status')})}
                try:
                    response=multipart_video(server,token,base+f'/videos/{vid}',video,fields,max_bytes=claim['max_bytes'])
                except Exception:
                    # Reconcile the persisted receipt before sending another byte.
                    manifest=api_get(server,token,f'/api/channel-archives/{cid}/manifest')
                    response=next((r for r in manifest['items'] if r['video_id']==vid and r['status']=='uploaded'),{})
                    response['verified']=response.get('status')=='uploaded'
                    if not response['verified']:raise
                if not response.get('verified') or response.get('sha256')!=sha or not response.get('drive_file_id'):
                    raise RuntimeError('No matching verified Drive receipt; local file retained')
                (directory/'verified-receipt.json').write_text(json.dumps(response,indent=2))
                video.unlink()  # only after actual remote checksum/size verification receipt
                completed+=1;log_msg(f'Verified in Drive: {cid}/{vid}')
            api_post(server,token,base+'/finish',auth)
        return completed
    except BaseException:
        try:api_post(server,token,base+'/release',auth)
        except Exception:pass
        raise


def main():
    env=load_env();parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default=os.environ.get('SERVER_URL') or env.get('SERVER_URL'))
    parser.add_argument('--channel',help='Canonical UC channel ID')
    parser.add_argument('--inventory',help='Public channel URL; inventory only, no media download')
    parser.add_argument('--parent-folder',default='1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo')
    parser.add_argument('--workdir',default=str(Path(__file__).resolve().parents[1]/'data'/'archive-worker'))
    args=parser.parse_args();token=os.environ.get('ACCESS_TOKEN') or env.get('ACCESS_TOKEN')
    if not args.server or not token:parser.error('Set SERVER_URL and ACCESS_TOKEN, or data/deploy.env')
    parsed=urlsplit(args.server)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or (parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1'))):parser.error('Use HTTPS or isolated localhost HTTP')
    if args.inventory:
        data=source.inventory(args.inventory,['shorts'])
        response=api_post(args.server,token,'/api/archive-workers/inventory',{**data,'parent_folder_id':args.parent_folder,'formats':['shorts']})
        print(json.dumps(response,indent=2));return
    if not args.channel:parser.error('Choose --inventory URL or --channel UC_ID')
    print('Verified files:',drain(args.server,token,args.channel,args.workdir))


if __name__=='__main__':main()
