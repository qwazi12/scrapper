"""Real local media and HTTP handlers; no remote downloads or accounts."""
import concurrent.futures
from datetime import datetime,timedelta,timezone
import hashlib
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import worker, worker_leases as leases
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import Clip,Compilation,IngestJob,Status,StudioProject,WorkerLease,WorkerTransfer
from backend.app.studio import media,stage_trailer


@pytest.fixture(autouse=True)
def isolated_jobs():
    with SessionLocal() as s:
        for model in (WorkerTransfer,Clip,Compilation,IngestJob):s.query(model).delete()
        s.commit()


@pytest.fixture
def client(): return TestClient(app,headers={'x-access-token':'test-token'})


@pytest.fixture
def video(tmp_path):
    path=tmp_path/'original.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=12:d=0.5',
               '-f','lavfi','-i','sine=f=400:d=0.5','-c:v','libx264','-c:a','aac','-pix_fmt','yuv420p',str(path)])
    return path


def pending():
    with SessionLocal() as s:
        clip=Clip(source_url='https://www.youtube.com/watch?v=synthetic123',platform='youtube',status=Status.failed,
                  error='Transferred to Mac worker (residential download)')
        s.add(clip);s.commit();return clip.id


def expire(key):
    with SessionLocal() as s:
        s.get(WorkerLease,key).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);s.commit()


def claim(client,cid,worker_id='fixture-worker'):
    response=client.post(f'/api/clips/{cid}/worker-claim',json={'worker_id':worker_id})
    assert response.status_code==200,response.text
    return response.json()


def upload(client,cid,claim,video,**extra):
    return client.post(f'/api/clips/{cid}/upload',data={'worker_token':claim['token'],'worker_generation':claim['generation'],
        'worker_id':'fixture-worker','meta':json.dumps(extra)},files={'file':('synthetic.mp4',video.read_bytes(),'video/mp4')})


def test_single_worker_owns_queued_job_and_completed_scan_is_not_replayed(monkeypatch):
    with SessionLocal() as s:
        row=IngestJob(urls=['https://example.org/original']);s.add(row);s.commit();ident=row.id
    entered,release=threading.Event(),threading.Event();calls=[]
    def fake(i):
        calls.append(i);entered.set();assert release.wait(5)
        with SessionLocal() as s: s.get(IngestJob,i).status=Status.done;s.commit()
    monkeypatch.setattr(worker,'_ingest_owned',fake)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(worker._process_ingest,ident);assert entered.wait(3)
        assert worker._process_ingest(ident) is False
        release.set();assert first.result()
    assert worker._process_ingest(ident) is False and calls==[ident]


def test_recovery_does_not_touch_live_outputs_or_promote_partial_files(tmp_path):
    partial=tmp_path/'partial.mp4';partial.write_bytes(b'not playable')
    with SessionLocal() as s:
        job=IngestJob(urls=['https://example.org/original'],status=Status.running)
        s.add(job);s.flush();clip=Clip(job_id=job.id,source_url="https://example.org/original",status=Status.running,file_path=str(partial))
        comp=Compilation(clip_ids=[],status=Status.running,output_path=str(partial));s.add_all([clip,comp]);s.commit();jid,cid=job.id,comp.id
    with leases.scope(f'ingest:{jid}'),leases.scope(f'compile:{cid}'):
        with SessionLocal() as s: assert worker.recover_interrupted_worker(s)==dict(ingest=0,compilations=0,clips=0)
    with SessionLocal() as s:
        result=worker.recover_interrupted_worker(s)
        assert result==dict(ingest=1,compilations=1,clips=1)
        assert s.get(Compilation,cid).status==Status.queued and partial.read_bytes()==b'not playable'


def test_expired_claim_cannot_upload_and_duplicate_completion_returns_same_receipt(client,video):
    cid=pending();old=claim(client,cid)
    assert client.post(f'/api/clips/{cid}/worker-claim',json={'worker_id':'other-worker'}).status_code==409
    expire(f'clip:{cid}');new=claim(client,cid)
    assert new['generation']>old['generation']
    assert upload(client,cid,old,video).status_code==409
    response=upload(client,cid,new,video,title='Synthetic original',duration=99999,width=99999)
    assert response.status_code==200,response.text
    receipt=response.json();assert receipt['sha256']==hashlib.sha256(video.read_bytes()).hexdigest()
    assert upload(client,cid,new,video).json()==receipt
    assert client.get(f"/api/worker-transfers/{new['token']}").json()['receipt']==receipt
    with SessionLocal() as s:
        clip=s.get(Clip,cid);assert clip.width==320 and clip.duration<1 and clip.status==Status.done
        assert s.get(WorkerTransfer,old['token']).status=='expired'
    trailer,msg=stage_trailer._from_worker(1,{},dict(pending_clip_id=cid,page='https://youtu.be/synthetic123'))
    assert trailer['file'] and 'received' in msg
    with pytest.raises(RuntimeError,match='identity changed'):
        stage_trailer._from_worker(1,{},dict(pending_clip_id=cid,page='https://youtu.be/different'))


def test_upload_identity_invalid_media_and_missing_claim_fail_closed(client,video):
    cid=pending();token=claim(client,cid)
    assert client.post(f'/api/clips/{cid}/upload',files={'file':('x.mp4',video.read_bytes())}).status_code==409
    assert upload(client,cid,token,video,webpage_url='https://youtu.be/wrong').status_code==422
    with SessionLocal() as s: assert s.get(Clip,cid).file_path is None
    bad=client.post(f'/api/clips/{cid}/upload',data={'worker_token':token['token'],'worker_id':'fixture-worker',
        'worker_generation':token['generation']},files={'file':('x.mp4',b'not media')})
    assert bad.status_code==400
    with SessionLocal() as s: assert s.get(Clip,cid).status==Status.failed


def test_owner_stop_revokes_distributed_worker(client):
    with SessionLocal() as s:
        row=Compilation(clip_ids=[],status=Status.running);s.add(row);s.commit();cid=row.id
    with SessionLocal() as s:
        h=leases.acquire(s,f"compile:{cid}","fixture-server");s.commit()
    assert client.post(f'/api/jobs/compile:{cid}/stop').status_code==200
    with pytest.raises(leases.LeaseLost): leases.guard(handle=h)


def test_local_client_recovers_lost_completion_response_without_reupload(client,video,monkeypatch):
    import io
    import shutil
    import urllib.error
    from urllib.parse import urlsplit
    from scripts import local_worker as local
    cid=pending();uploads=[]
    def relay(request,**kwargs):
        path=urlsplit(request.full_url).path
        body=request.data
        if body is not None and not isinstance(body,bytes): body=b''.join(body)
        result=client.request(request.get_method(),path,content=body,headers=dict(request.header_items()))
        if path=='/api/clips/blocked': return io.BytesIO(json.dumps([r for r in result.json() if r['id']==cid]).encode())
        if path.endswith('/upload'):
            uploads.append(path)
            assert result.status_code==200,result.text
            raise urllib.error.URLError('synthetic accepted response lost')
        if result.status_code>=400: raise urllib.error.HTTPError(request.full_url,result.status_code,'fixture',{},io.BytesIO(result.content))
        return io.BytesIO(result.content)
    monkeypatch.setattr(local.urllib.request,'urlopen',relay)
    def download(url,root,browser,cancelled):
        path=root/'original.mp4';shutil.copyfile(video,path);return path,{'title':'Original fixture','webpage_url':url}
    monkeypatch.setattr(local,'download',download)
    assert local.drain('http://localhost','test-token',None)==1
    assert len(uploads)==1
    with SessionLocal() as s: assert s.get(Clip,cid).status==Status.done


def test_lease_expiring_during_upload_never_promotes_stale_file(client,video,monkeypatch):
    from backend.app import uploads
    cid=pending();token=claim(client,cid)
    original=uploads.save_video
    async def expire_before_promotion(file,dest):
        result=await original(file,dest);expire(f'clip:{cid}');return result
    monkeypatch.setattr(uploads,'save_video',expire_before_promotion)
    response=upload(client,cid,token,video)
    assert response.status_code==409,response.text
    with SessionLocal() as s: assert s.get(Clip,cid).file_path is None
    from backend.app.config import settings
    assert not list((settings.downloads_path/'youtube'/'_transfers'/token['token']).glob('*.mp4'))


def test_actual_compile_uses_own_attempt_and_preserves_other_exports(video):
    with SessionLocal() as s:
        clip=Clip(source_url='https://example.org/original',file_path=str(video),status=Status.done)
        s.add(clip);s.flush();comp=Compilation(clip_ids=[clip.id],orientation='landscape');s.add(comp);s.commit();cid=comp.id
    assert worker._process_compile(cid)
    with SessionLocal() as s:
        comp=s.get(Compilation,cid)
        assert comp.status==Status.done and '_attempts' in comp.output_path
        from pathlib import Path
        assert Path(comp.output_path).is_file()
    assert not worker._process_compile(cid)
