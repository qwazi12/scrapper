"""Cross-worker media claims, deletion references and verified-copy contracts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

from fastapi import HTTPException,UploadFile
from fastapi.testclient import TestClient
import pytest

from backend.app import cleanup,control,file_use,uploads
from backend.app.config import settings
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import Clip,Compilation,QueueItem,Status,StudioProject,WorkerLease
from backend.app.studio import archive,drive_store,runner


def other(fn):
    # A distinct HTTP request/process does not inherit the worker's ContextVars.
    with ThreadPoolExecutor(1) as pool:return pool.submit(fn).result(timeout=10)


def test_shared_readers_block_ancestor_mutation_across_python_processes(tmp_path):
    file=tmp_path/'project'/'final.mp4';file.parent.mkdir();file.write_bytes(b'original media')
    with file_use.pin([file]):
        with file_use.pin([file.parent]):pass  # Own nested reader is allowed.
        def mutate():
            with pytest.raises(file_use.FileBusy):
                with file_use.pin([tmp_path],exclusive=True):pytest.fail('Unsafe deletion admitted')
        other(mutate)
        code='''from backend.app.file_use import pin,FileBusy
import sys
try:
 with pin([sys.argv[1]],exclusive=True): pass
except FileBusy:sys.exit(3)
'''
        result=subprocess.run([sys.executable,'-c',code,str(file.parent)],capture_output=True,text=True,timeout=15)
        assert result.returncode==3,result.stderr
    with file_use.pin([tmp_path],exclusive=True):file.unlink()


def test_expired_reader_is_fenced_before_old_worker_can_continue(tmp_path):
    with file_use.pin([tmp_path]) as handle:
        with SessionLocal() as s:
            s.get(WorkerLease,handle.resource).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);s.commit()
        def replace():
            with file_use.pin([tmp_path],exclusive=True):(tmp_path/'fresh').write_text('new worker')
        other(replace)
        with pytest.raises(__import__("backend.app.worker_leases",fromlist=["LeaseLost"]).LeaseLost,match='expired or superseded'):control.check()
    assert (tmp_path/'fresh').read_text()=='new worker'


def test_cache_eviction_skips_in_use_files_and_recovers_after_release(tmp_path):
    a=tmp_path/'old';a.write_bytes(b'a'*100)
    b=tmp_path/'new';b.write_bytes(b'b'*100)
    with file_use.pin([a]):
        result=other(lambda:cleanup.prune_lru_cache(tmp_path,0))
        assert a.exists() and not b.exists() and result['remaining_bytes']==100
    assert cleanup.prune_lru_cache(tmp_path,0)['remaining_bytes']==0


def test_retention_and_owner_delete_preserve_compilation_and_studio_inputs(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'retention_days',1)
    file=tmp_path/'source.mp4';file.write_bytes(b'synthetic source')
    with SessionLocal() as s:
        referenced=[cid for comp in s.query(Compilation) for cid in comp.clip_ids or []]
        existing=[cid for (cid,) in s.query(Clip.id)]
        next_id=max([0,*referenced,*existing])+1
        clip=Clip(id=next_id,source_url='https://example.invalid/fixture',file_path=str(file),status=Status.done,
                  created_at=datetime.now(timezone.utc)-timedelta(days=5))
        s.add(clip);s.flush();cid=clip.id
        comp=Compilation(clip_ids=[cid],status=Status.done);s.add(comp);s.commit();compid=comp.id
        assert cleanup.purge_expired(s)['clips']==0
    client=TestClient(app,headers={'x-access-token':'test-token'})
    assert client.delete(f'/api/clips/{cid}').status_code==409 and file.exists()
    with SessionLocal() as s:
        s.delete(s.get(Compilation,compid));s.commit()
        project=StudioProject(tmdb_id=99999,title='Synthetic input reference',trailer={'file':str(file)})
        s.add(project);s.commit();pid=project.id
    assert client.delete(f'/api/clips/{cid}').status_code==409 and file.exists()
    with SessionLocal() as s:s.delete(s.get(StudioProject,pid));s.commit()
    with file_use.pin([file]):
        result=other(lambda:client.delete(f'/api/clips/{cid}'))
        assert result.status_code==409 and file.exists()
    response=client.delete(f'/api/clips/{cid}')
    assert response.status_code==200,response.text
    assert not file.exists()


def test_studio_edit_and_archive_refuse_an_active_transfer_reader():
    with SessionLocal() as s:
        project=StudioProject(tmdb_id=99991,title='Synthetic transfer');s.add(project);s.commit();pid=project.id
    root=runner.project_dir(pid)
    client=TestClient(app,headers={'x-access-token':'test-token'})
    with file_use.pin([root/'render'/'final.mp4']):
        result=other(lambda:client.patch(f'/api/studio/projects/{pid}',json={'target_minutes':3}))
        assert result.status_code==409
        with pytest.raises(file_use.FileBusy):other(lambda:archive.archive_project(pid))
    assert client.patch(f'/api/studio/projects/{pid}',json={'target_minutes':3}).status_code==200


@pytest.mark.parametrize('bad',[b'',b'{}',b'bad\x00cookie',b'x'*(1024*1024+1),b'# Netscape HTTP Cookie File\n'])
def test_invalid_cookie_exports_preserve_existing_secret(tmp_path,bad):
    dest=tmp_path/'cookies.txt';dest.write_bytes(b'previous synthetic secret')
    with pytest.raises(HTTPException):
        asyncio.run(uploads.save_cookies(UploadFile(file=io.BytesIO(bad),filename='cookies.txt'),dest))
    assert dest.read_bytes()==b'previous synthetic secret'
    assert not list(tmp_path.glob('.cookies-*'))


def test_cookie_export_is_atomic_private_and_never_logged(tmp_path):
    payload=b'# Netscape HTTP Cookie File\n#HttpOnly_.example.invalid\tTRUE\t/\tTRUE\t0\tfixture\tsynthetic-value\n'
    dest=tmp_path/'cookies.txt'
    assert asyncio.run(uploads.save_cookies(UploadFile(file=io.BytesIO(payload)),dest))==len(payload)
    assert dest.read_bytes()==payload and dest.stat().st_mode&0o777==0o600


def test_drive_remote_hash_is_independent_and_mismatch_preserves_local_file(tmp_path):
    file=tmp_path/'video.mp4';file.write_bytes(b'original synthetic transfer data')
    class Service:
        remote=b'original synthetic transfer data'
        def files(self):return self
        def create(self,**kwargs):return self
        def next_chunk(self):return None,{'id':'fixture-remote','webViewLink':'https://example.invalid/fixture'}
        def get(self,**kwargs):
            assert kwargs['fileId']=='fixture-remote';return self
        def execute(self):return {'id':'fixture-remote','size':str(len(self.remote)),'md5Checksum':hashlib.md5(self.remote).hexdigest()}
    service=Service()
    with file_use.pin([file]):
        result=drive_store._upload(service,file,'fixture-folder','synthetic.mp4')
    assert result['verification']['sha256']==hashlib.sha256(file.read_bytes()).hexdigest()
    service.remote=b'corrupt remote copy'
    with pytest.raises(drive_store.DriveStoreError,match='remote checksum'):
        with file_use.pin([file]):drive_store._upload(service,file,'fixture-folder','synthetic.mp4')
    assert file.read_bytes()==b'original synthetic transfer data'


def test_saved_drive_label_does_not_authorize_deletion_without_matching_proof(tmp_path):
    file=tmp_path/'final.mp4';file.write_bytes(b'original fixture')
    row=StudioProject(tmdb_id=99992,title='Hash fixture',render={'file':'final.mp4'},drive={'status':'saved','file_id':'fixture'})
    assert not archive.verified_copy(row,tmp_path)
    row.drive={**row.drive,'verification':{'remote_file_id':'fixture','remote_md5':hashlib.md5(file.read_bytes()).hexdigest(),
        'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'bytes':file.stat().st_size}}
    assert archive.verified_copy(row,tmp_path)
    file.write_bytes(b'modified fixture')
    assert not archive.verified_copy(row,tmp_path)


def test_full_decode_rejects_corruption_even_when_container_probes(tmp_path):
    from backend.app.studio import media
    source=tmp_path/'source.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=1','-c:v','libx264','-movflags','+faststart',str(source)])
    payload=bytearray(source.read_bytes());mdat=payload.index(b'mdat')+4
    payload[mdat+100:mdat+1500]=b'\xff'*1400
    corrupt=tmp_path/'corrupt.mp4';corrupt.write_bytes(payload)
    probe=json.loads(media.run(['ffprobe','-v','error','-show_streams','-of','json',str(corrupt)]).stdout)
    assert probe['streams'][0]['width']==320
    dest=tmp_path/'trailer.mp4';dest.write_bytes(b'previous approved upload')
    with pytest.raises(HTTPException):asyncio.run(uploads.save_video(UploadFile(file=io.BytesIO(payload)),dest))
    assert dest.read_bytes()==b'previous approved upload'
    assert not list(tmp_path.glob('.upload-*'))


def test_file_ownership_fences_database_writes_after_expiry(tmp_path):
    from backend.app.worker_leases import LeaseLost
    with SessionLocal() as s:
        row=StudioProject(tmdb_id=99993,title='Original title');s.add(row);s.commit();pid=row.id
    with file_use.pin([tmp_path]) as handle:
        with SessionLocal() as s:
            s.get(WorkerLease,handle.resource).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);s.commit()
        with SessionLocal() as s:
            s.get(StudioProject,pid).title='Stale mutation'
            with pytest.raises(LeaseLost):s.commit()
            s.rollback()
    with SessionLocal() as s:assert s.get(StudioProject,pid).title=='Original title'


def test_restore_rejects_changed_source_and_preserves_prior_plan(tmp_path,monkeypatch):
    from backend.app.studio import media,imdb
    new=tmp_path/'different.mp4'
    media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:d=0.2','-c:v','libx264',str(new)])
    with SessionLocal() as s:
        p=StudioProject(tmdb_id=99994,title='Restore mismatch',plan=[{'prior':'unchanged'}],archive={'archived_at':'fixture'})
        s.add(p);s.commit();pid=p.id
        dest=runner.project_dir(pid)/'footage'/'original.mp4'
        p.trailer={'sources':[{'id':'fixture','origin':'imdb','file':str(dest),'sha256':hashlib.sha256(b'old source').hexdigest()}]};s.commit()
    monkeypatch.setattr(imdb,'download',lambda ident,path:Path(path).write_bytes(new.read_bytes()))
    with pytest.raises(RuntimeError,match='source content changed'):archive.restore(pid)
    assert not dest.exists() and not list(dest.parent.glob('.restore-*'))
    with SessionLocal() as s:
        p=s.get(StudioProject,pid)
        assert p.plan==[{'prior':'unchanged'}] and p.archive['archived_at']=='fixture'


def test_operational_log_retention_does_not_delete_financial_evidence(monkeypatch):
    from backend.app import logbus
    from backend.app.models import LogEntry,FinancialResolution
    from uuid import uuid4
    monkeypatch.setattr(settings,'log_retention_days',30);monkeypatch.setattr(settings,'log_max_rows',3)
    fid=str(uuid4())
    with SessionLocal() as s:
        s.query(LogEntry).delete()
        s.add(LogEntry(message='old',created_at=datetime.now(timezone.utc)-timedelta(days=31)))
        for n in range(5):s.add(LogEntry(message=f'new-{n}'))
        s.add(FinancialResolution(id=fid,kind='usd',reservation_id=str(uuid4()),amount=0,receipt='synthetic receipt',rationale='Synthetic financial evidence must remain available.'))
        s.commit()
    assert logbus.prune()==3
    with SessionLocal() as s:
        assert [r.message for r in s.query(LogEntry).order_by(LogEntry.id)]==['new-2','new-3','new-4']
        assert s.get(FinancialResolution,fid)
