"""Countdown transfer/cleanup contracts with local files and fake Drive SDK I/O."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.app import file_use
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import CountdownProject, QueueItem, StudioProject
from backend.app.studio import countdown, drive_store, runner


def other(fn):
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(fn).result(timeout=10)


@pytest.fixture
def campaign(session):
    c = CountdownProject(topic='Original synthetic lifecycle test', fmt='top5',
                         script={'title':'Synthetic countdown','intro':'Original hook','outro':'Original question'})
    session.add(c); session.commit()
    from backend.app.studio.compilations.stitch import compilations_dir
    root = compilations_dir()/uuid4().hex
    (root/'thumbnails').mkdir(parents=True)
    video = root/'final_compilation.mp4'; video.write_bytes(b'original synthetic export')
    thumb = root/'thumbnails'/'thumbnail.jpg'; thumb.write_bytes(b'original thumbnail')
    (thumb.parent/'alternative.jpg').write_bytes(b'new thumbnail option')
    c.render = {'file':str(video),'thumbnail':str(thumb),'compilation_id':root.name,
                'rendered_at':'synthetic-render-1','thumbnails':{'poster':'alternative.jpg'}}
    session.commit()
    cid=c.id
    yield cid, root
    session.rollback()
    row = session.get(CountdownProject,cid)
    if row: session.delete(row); session.commit()


def proof(video, remote='synthetic-remote'):
    return {'remote_file_id':remote,'remote_md5':hashlib.md5(video.read_bytes()).hexdigest(),
            'sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'bytes':video.stat().st_size,
            'checked_at':'synthetic-verification'}


def saved(session, cid, root, *, valid=True, status='archived', link=True):
    video = root/'final_compilation.mp4'
    q = QueueItem(title='Synthetic delivery',status=status,video_path=str(video),
                  drive_link='https://example.invalid/synthetic-remote' if link else None)
    session.add(q); session.flush()
    c = session.get(CountdownProject,cid)
    c.queue_item_id = q.id
    c.review = {'posted_at':(datetime.now(timezone.utc)-timedelta(days=20)).isoformat()}
    drive = {'status':'saved','file_id':'synthetic-remote','rendered_at':c.render['rendered_at'],
             'link':'https://example.invalid/synthetic-remote'}
    if valid: drive['verification'] = proof(video)
    c.render = {**c.render,'drive':drive}
    session.commit()
    return q.id


@pytest.mark.parametrize('mode',['legacy-label','changed-bytes','active-queue','missing-queue-backup','different-queue-backup','other-reference'])
def test_sweep_preserves_unverified_or_referenced_media(session,campaign,mode):
    cid,root = campaign
    saved(session,cid,root,valid=mode!='legacy-label',status='ready' if mode=='active-queue' else 'archived',
          link=mode!='missing-queue-backup')
    video = root/'final_compilation.mp4'
    if mode=='different-queue-backup':
        q=session.get(QueueItem,session.get(CountdownProject,cid).queue_item_id)
        q.drive_link += '-different-file';session.commit()
    if mode=='changed-bytes': video.write_bytes(b'different render')
    if mode=='other-reference':
        session.add(QueueItem(title='Other export',status='ready',video_path=str(video))); session.commit()
    countdown.sweep_posted(session)
    assert video.exists()
    session.expire_all()
    assert not session.get(CountdownProject,cid).render.get('archived_at')


def test_sweep_requires_exclusive_file_ownership_then_keeps_verified_receipt(session,campaign):
    cid,root = campaign; saved(session,cid,root)
    video = root/'final_compilation.mp4'
    def sweep():
        with SessionLocal() as s: countdown.sweep_posted(s)
    with file_use.pin([video]): other(sweep)
    assert video.exists()
    countdown.sweep_posted(session)
    session.expire_all()
    c = session.get(CountdownProject,cid)
    assert not video.exists() and c.render['archived_at']
    assert c.render['drive']['verification']['remote_file_id']=='synthetic-remote'
    assert (root/'thumbnails'/'thumbnail.jpg').exists()


def test_owner_delete_preserves_shared_and_uncertain_queue_media(session,campaign):
    cid,root = campaign; video = root/'final_compilation.mp4'
    qid = saved(session,cid,root,status='ready')
    foreign = QueueItem(title='Other queue consumer',status='ready',video_path=str(video))
    session.add(foreign); session.commit()
    client = TestClient(app,headers={'x-access-token':'test-token'})
    assert client.delete(f'/api/countdowns/{cid}').status_code==409
    session.refresh(foreign); session.delete(foreign); session.commit()
    q = session.get(QueueItem,qid)
    q.publish_requests = [{'state':'unknown','client_request_id':uuid4().hex}]; session.commit()
    assert client.delete(f'/api/countdowns/{cid}').status_code==409
    assert video.exists() and session.get(QueueItem,qid)
    q.publish_requests=[]; session.commit()
    with file_use.pin([video]):
        assert other(lambda:client.delete(f'/api/countdowns/{cid}')).status_code==409
    response = client.delete(f'/api/countdowns/{cid}')
    assert response.status_code==200,response.text
    session.expire_all()
    assert session.get(QueueItem,qid) is None and not root.exists()


def test_thumbnail_edit_refuses_queued_or_reading_media(session,campaign):
    cid,root = campaign; thumb=root/'thumbnails'/'thumbnail.jpg'
    client=TestClient(app,headers={'x-access-token':'test-token'})
    with file_use.pin([root]):
        assert other(lambda:client.post(f'/api/countdowns/{cid}/thumbnail',json={'option':'poster'})).status_code==409
    q=QueueItem(title='Queued thumbnail',status='ready',thumb_path=str(thumb));session.add(q);session.commit()
    assert client.post(f'/api/countdowns/{cid}/thumbnail',json={'option':'poster'}).status_code==409
    assert client.post(f'/api/countdowns/{cid}/thumbnails/refresh').status_code==409
    assert thumb.read_bytes()==b'original thumbnail'
    session.delete(q);session.commit()
    response=client.post(f'/api/countdowns/{cid}/thumbnail',json={'option':'poster'})
    assert response.status_code==200,response.text
    assert thumb.read_bytes()==b'new thumbnail option'


def test_thumbnail_refresh_stages_privately_and_fences_expired_promotion(session,campaign,monkeypatch):
    from backend.app.models import WorkerLease
    from backend.app.worker_leases import LeaseLost
    from backend.app.studio.compilations import stitch
    cid,root=campaign; thumb=root/'thumbnails'/'thumbnail.jpg'
    names={'poster':'thumb_option_1_poster.jpg','action':'thumb_option_2_action.jpg','split':'thumb_option_3_split.jpg'}
    c=session.get(CountdownProject,cid);c.render={**c.render,'thumbnails':names};session.commit()
    expire=[True]
    def generate(projects,title,path):
        path.mkdir(parents=True)
        for name in names.values(): (path/name).write_bytes(b'fresh synthetic thumbnail')
        if expire[0]:
            with SessionLocal() as writer:
                for handle in file_use._active.get():
                    writer.get(WorkerLease,handle.resource).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
                writer.commit()
    monkeypatch.setattr(stitch,'generate_compilation_thumbnails',generate)
    client=TestClient(app,headers={'x-access-token':'test-token'})
    with pytest.raises(LeaseLost):client.post(f'/api/countdowns/{cid}/thumbnails/refresh')
    assert thumb.read_bytes()==b'original thumbnail' and not list(thumb.parent.glob('.refresh-*'))
    expire[0]=False
    response=client.post(f'/api/countdowns/{cid}/thumbnails/refresh')
    assert response.status_code==200,response.text
    assert thumb.read_bytes()==b'fresh synthetic thumbnail'
    assert all((thumb.parent/name).is_file() for name in names.values())
    assert not list(thumb.parent.glob('.refresh-*'))


@pytest.mark.parametrize('same_video',[True,False])
def test_drive_retains_verified_receipt_and_links_only_exact_queue_media(session,campaign,monkeypatch,same_video):
    from backend.app import drive_sync
    from backend.app.worker_leases import LeaseLost
    cid,root=campaign; video=root/'final_compilation.mp4'
    q=QueueItem(title='Queue version',status='ready',video_path=str(video) if same_video else str(root/'older.mp4'))
    session.add(q);session.flush();c=session.get(CountdownProject,cid);c.queue_item_id=q.id;session.commit()
    execute=lambda **kwargs:SimpleNamespace(execute=lambda:{'files':[{'id':'fake-folder'}]})
    svc=SimpleNamespace(files=lambda:SimpleNamespace(list=execute))
    monkeypatch.setattr(drive_sync,'get_drive_service',lambda:svc)
    monkeypatch.setattr(drive_store,'folder_id',lambda svc:'fake-parent')
    calls=[]
    client=TestClient(app,headers={'x-access-token':'test-token'})
    def upload(service,path,parent,name):
        calls.append(path)
        if path==video:
            with pytest.raises(LeaseLost):other(lambda:countdown.drive_job(cid))
            assert other(lambda:client.delete(f'/api/countdowns/{cid}')).status_code==409
            assert other(lambda:client.put(f'/api/countdowns/{cid}/script',json={'intro':'Concurrent edit'})).status_code==409
        return {'id':'synthetic-remote','webViewLink':'https://example.invalid/synthetic-remote','verification':proof(path)}
    monkeypatch.setattr(drive_store,'_upload',upload)
    result=countdown.drive_job(cid)
    session.expire_all(); c=session.get(CountdownProject,cid); q=session.get(QueueItem,q.id)
    assert result['status']=='saved' and countdown.verified_copy(c)
    assert c.render['drive']['verification']==proof(video)
    assert bool(q.drive_link)==same_video and len(calls)==2


def test_late_drive_status_cannot_attach_to_a_replaced_render(session,campaign):
    cid,root=campaign; c=session.get(CountdownProject,cid); version=countdown.render_version(c)
    c.render={**c.render,'rendered_at':'newer-render','drive':{'status':'saved','file_id':'newer-backup'}};session.commit()
    with pytest.raises(drive_store.DriveStoreError,match='changed during transfer'):
        countdown._set_drive(cid,{'status':'saved','file_id':'old-backup'},version)
    session.refresh(c)
    assert c.render['drive']['file_id']=='newer-backup'


def test_countdown_render_holds_segment_files_until_fingerprint_and_stitch_finish(session,campaign,monkeypatch):
    from backend.app.studio.compilations import stitch
    cid,root=campaign
    p=StudioProject(tmdb_id=99881,title='Synthetic source segment');session.add(p);session.flush()
    c=session.get(CountdownProject,cid);c.segment_ids=[p.id];session.commit()
    client=TestClient(app,headers={'x-access-token':'test-token'})
    def fingerprints(s,c):
        assert other(lambda:client.patch(f'/api/studio/projects/{p.id}',json={'target_minutes':2})).status_code==409
        return {str(p.id):'synthetic-fingerprint'}
    def stitch_fake(**kwargs):
        with pytest.raises(file_use.FileBusy):
            other(lambda:_exclusive(runner.project_dir(p.id)))
        return {'compilation_id':root.name,'video_path':str(root/'final_compilation.mp4'),
                'primary_thumb':str(root/'thumbnails'/'thumbnail.jpg'),'chapters':[],'total_seconds':1,'total_minutes':0.02}
    monkeypatch.setattr(countdown,'segment_fingerprints',fingerprints)
    monkeypatch.setattr(countdown,'inspect_countdown',lambda res:{'decode':'passed','errors':[]})
    monkeypatch.setattr(stitch,'stitch_countdown_compilation',stitch_fake)
    countdown.render_job(cid)
    session.refresh(c);assert c.stage_status=='done'


def _exclusive(path):
    with file_use.pin([path],exclusive=True):pass


def test_two_stitch_attempts_in_the_same_second_never_share_output(session,monkeypatch):
    from backend.app.studio.compilations import stitch
    projects=[]
    for n in range(2):
        p=StudioProject(tmdb_id=99890+n,title=f'Synthetic {n}');session.add(p);session.flush()
        root=runner.project_dir(p.id);root.mkdir(parents=True,exist_ok=True)
        video=root/'synthetic.mp4';video.write_bytes(b'synthetic input')
        p.render={'file':str(video)};projects.append(p.id)
    session.commit()
    class Frozen(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,18,12,0,0,tzinfo=tz)
    monkeypatch.setattr(stitch.datetime,'datetime',Frozen)
    outputs=[]
    def fail_after_output_claim(projects,title,path,**kwargs):
        path.write_bytes(f'isolated attempt {len(outputs)}'.encode());outputs.append(path)
        raise RuntimeError('Synthetic interruption after output claim')
    monkeypatch.setattr(stitch,'create_intro_clip',fail_after_output_claim)
    for _ in range(2):
        with pytest.raises(RuntimeError,match='Synthetic interruption'):
            stitch.stitch_countdown_compilation(projects,'Synthetic',send_to_queue=False)
    assert outputs[0].parent.parent!=outputs[1].parent.parent
    assert outputs[0].read_bytes()==b'isolated attempt 0'
    assert outputs[1].read_bytes()==b'isolated attempt 1'
