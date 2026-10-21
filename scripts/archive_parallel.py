#!/usr/bin/env python3
"""Two disjoint channel lanes, shared stop and serialized Review-only sync.

The priority channel has one lane; other channels rotate in the second. Each
lane retains the existing server claim, pending ledger and verified receipt
protocol. A lane error stops both; no automatic retries or extra processes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import archive_batch as batch


def run(server,token,rows,workdir,state_dir,*,priority,browser_profile=None,sync_folder=None):
    rows=list(rows)
    ids=[r['channel_id'] for r in rows]
    if len(set(ids))!=len(ids) or priority not in ids:
        raise ValueError('Unique catalog IDs and a catalog priority channel are required')
    lanes={'priority':[r for r in rows if r['channel_id']==priority],
           'rotation':[r for r in rows if r['channel_id']!=priority]}
    if not lanes['rotation']:raise ValueError('Two lanes require at least two channels')
    workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=True)
    # Bound each lane to 256MiB per video on this low-space Mac. The worker
    # also uses the smaller of this bound and the actual server claim limit.
    # Reserve both lanes' input/merge budgets before either lane starts.
    video_bound=256*1024*1024
    required=2*2*video_bound+256*1024*1024
    if shutil.disk_usage(workdir).free<required:
        raise RuntimeError('Mac temporary storage reserve for two workers is insufficient')
    state_dir=Path(state_dir);state_dir.mkdir(parents=True,exist_ok=True)
    stop=threading.Event();sync_lock=threading.Lock()
    state={'pid':os.getpid(),'status':'running','concurrency_limit':2,
           'max_video_bytes':video_bound,'priority_channel':priority,'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),
           'lanes':{name:{'channels':[r['channel_id'] for r in catalog],'status':'running'}
                    for name,catalog in lanes.items()}}

    def save():
        state['updated_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        temporary=state_dir/'parallel-state.tmp'
        temporary.write_text(json.dumps(state,indent=2))
        temporary.replace(state_dir/'parallel-state.json')

    def lane(name,catalog):
        try:
            result=batch.run(server,token,catalog,workdir,state_dir/name,batch_size=25,
                             browser_profile=browser_profile,sync_folder=sync_folder,
                             cancelled=stop,sync_lock=sync_lock,storage_slots=2,max_video_bytes=video_bound)
            if result['status'] not in ('complete','partial') or result['channel_errors']:
                stop.set()
            return result
        except BaseException:
            stop.set()
            raise

    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='archive-lane')
    save()
    futures={pool.submit(lane,name,catalog):name for name,catalog in lanes.items()}
    try:
        pending=set(futures)
        while pending:
            done,pending=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
            for future in done:
                name=futures[future]
                try:
                    result=future.result()
                    state['lanes'][name].update(status=result['status'],totals=result.get('totals'),
                                               errors=result.get('channel_errors',{}))
                except BaseException as exc:
                    state['lanes'][name].update(status='error',error=str(exc)[-2000:])
            if stop.is_set():state['status']='stopping'
            save()
        statuses=[v['status'] for v in state['lanes'].values()]
        state['status']='blocked' if stop.is_set() else ('complete' if all(x=='complete' for x in statuses) else 'partial')
        return state
    except BaseException:
        stop.set();state['status']='interrupted';raise
    finally:
        stop.set()
        # In-flight uploads may finish and reconcile. No subsequent video starts.
        # Do not abandon the lease heartbeat or delete unverified local files.
        pool.shutdown(wait=True,cancel_futures=True)
        state['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();save()


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',required=True)
    parser.add_argument('--token-file',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,default=root/'docs/implementation/ARCHIVE_CHANNELS.json')
    parser.add_argument('--workdir',type=Path,required=True)
    parser.add_argument('--state-dir',type=Path,required=True)
    parser.add_argument('--priority',required=True)
    parser.add_argument('--brave-profile')
    parser.add_argument('--sync-folder')
    args=parser.parse_args()
    state=run(args.server,args.token_file.read_text().strip(),json.loads(args.catalog.read_text())['channels'],
              args.workdir,args.state_dir,priority=args.priority,browser_profile=args.brave_profile,
              sync_folder=args.sync_folder)
    return 0 if state['status']=='complete' else 2


if __name__=='__main__':sys.exit(main())
