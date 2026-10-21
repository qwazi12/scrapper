"""Actual thread coordination; external channel/media/cloud calls are simulated."""
import copy
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import archive_batch as batch, archive_parallel as parallel, archive_worker as worker


def catalog(monkeypatch):
    rows=[{'channel_id':'UC'+letter*22} for letter in 'abc']
    channels={r['channel_id']:{'channel_id':r['channel_id'],'inventory':{'complete':True},
              'uploaded_bytes':0,'counts':{'discovered':26,'uploaded':0,'downloaded':0,
              'remaining':26,'failed':0,'skipped':0}} for r in rows}
    lock=threading.Lock()
    def get(server,token,path):
        with lock:
            return copy.deepcopy({'channels':list(channels.values())} if path=='/api/channel-archives'
                                 else channels[path.split('/')[-1].split('?')[0]])
    monkeypatch.setattr(batch,'api_get',get)
    monkeypatch.setattr(parallel.shutil,'disk_usage',lambda p:SimpleNamespace(free=10**10))
    return rows,channels,lock


def test_two_lanes_overlap_but_never_same_channel_and_resume_25_plus_1(tmp_path,monkeypatch):
    rows,channels,lock=catalog(monkeypatch)
    barrier=threading.Barrier(2);active=set();peak=0;calls=[]
    def drain(server,token,cid,workdir,*,max_videos,cancelled,storage_slots,max_video_bytes):
        nonlocal peak
        assert storage_slots==2 and max_video_bytes==256*1024*1024
        with lock:
            assert cid not in active
            active.add(cid);peak=max(peak,len(active))
            first=not any(c==cid for c,n in calls)
            n=min(max_videos,channels[cid]['counts']['remaining']);calls.append((cid,n))
        if first and cid in [rows[0]['channel_id'],rows[1]['channel_id']]:barrier.wait(timeout=5)
        with lock:
            channels[cid]['counts']['remaining']-=n
            channels[cid]['counts']['uploaded']+=n
            channels[cid]['counts']['downloaded']+=n
            active.remove(cid)
        return n
    monkeypatch.setattr(worker,'drain',drain)
    sync_active=0;sync_peak=0;syncs=[]
    def sync(server,token,folder):
        nonlocal sync_active,sync_peak
        with lock:
            sync_active+=1;sync_peak=max(sync_peak,sync_active);syncs.append(folder)
        time.sleep(.01)
        with lock:sync_active-=1
        return {'status':'done'}
    monkeypatch.setattr(batch,'sync_review',sync)
    result=parallel.run('http://localhost','test',rows,tmp_path/'media',tmp_path/'state',priority=rows[0]['channel_id'],sync_folder='explicit-folder')
    assert result['status']=='complete' and peak==2
    assert sync_peak==1 and syncs==['explicit-folder']*6
    for row in rows:
        assert [n for cid,n in calls if cid==row['channel_id']]==[25,1]


def test_error_stops_other_lane_before_any_further_video(tmp_path,monkeypatch):
    rows,channels,lock=catalog(monkeypatch);both=threading.Barrier(2);calls=[]
    def drain(server,token,cid,workdir,*,max_videos,cancelled,storage_slots,max_video_bytes):
        calls.append(cid);both.wait(timeout=5)
        if cid==rows[0]['channel_id']:raise RuntimeError('HTTP Error 429: Too Many Requests')
        assert cancelled.wait(5)
        raise RuntimeError('Archive lease lost; local files retained')
    monkeypatch.setattr(worker,'drain',drain)
    result=parallel.run('http://localhost','test',rows,tmp_path/'media',tmp_path/'state',priority=rows[0]['channel_id'])
    assert result['status']=='blocked' and set(calls)=={rows[0]['channel_id'],rows[1]['channel_id']}


def test_parallel_refuses_insufficient_space_and_duplicate_lanes(tmp_path,monkeypatch):
    rows,channels,lock=catalog(monkeypatch)
    monkeypatch.setattr(parallel.shutil,'disk_usage',lambda p:SimpleNamespace(free=10**8))
    monkeypatch.setattr(worker,'drain',lambda *a,**k:pytest.fail('Must fail before claiming/downloading'))
    with pytest.raises(RuntimeError,match='storage reserve'):
        parallel.run('http://localhost','test',rows,tmp_path/'media',tmp_path/'state',priority=rows[0]['channel_id'])
    with pytest.raises(ValueError,match='Unique catalog'):
        parallel.run('http://localhost','test',[rows[0],rows[0]],tmp_path/'media',tmp_path/'state',priority=rows[0]['channel_id'])


def test_coordinator_stop_prevents_claim(monkeypatch,tmp_path):
    stop=threading.Event();stop.set()
    monkeypatch.setattr(worker,'api_get',lambda *a:pytest.fail('Cancelled worker must not contact server'))
    with pytest.raises(RuntimeError,match='stopped by coordinator'):
        worker.drain('http://localhost','test','UC'+'a'*22,tmp_path,cancelled=stop)
