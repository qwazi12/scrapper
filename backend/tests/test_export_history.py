"""Retained bytes, project identities, immutable evidence and draft recovery."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app import auth,export_versions,file_use
from backend.app.config import settings
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import CountdownProject,ExportVersion,QueueItem,StudioProject
from backend.app.studio import runner

client=TestClient(app,headers={'x-access-token':'test-token'})


@pytest.fixture
def produced(session):
    p=StudioProject(tmdb_id=999301,title='Original retained export',
                    facts={'title':'Original retained export'},
                    script={'sentences':[{'text':'Original owner words.','check':'ok'}]})
    session.add(p);session.commit();pid=p.id
    root=runner.project_dir(pid);video=root/'original.mp4';thumb=root/'original.jpg'
    video.write_bytes(b'original synthetic export bytes');thumb.write_bytes(b'original synthetic thumbnail')
    p.render={'file':video.name,'thumbnail':thumb.name,'rendered_at':'fixture-v1',
              'quality':{'schema':1,'decode':'passed','errors':[],'sha256':hashlib.sha256(video.read_bytes()).hexdigest()}}
    session.commit()
    rows=session.query(ExportVersion).filter_by(kind='studio',project_id=pid).all()
    assert len(rows)==1
    yield p,rows[0].id,root


def test_old_media_and_inputs_survive_script_invalidation_and_source_deletion(session,produced):
    p,eid,root=produced
    response=client.patch(f'/api/studio/projects/{p.id}',json={'script':{'sentences':[{'text':'New owner draft.'}]}})
    assert response.status_code==200,response.text
    (root/'original.mp4').unlink();(root/'original.jpg').write_bytes(b'replaced original path')
    result=client.get(f'/api/export-history/{eid}').json()
    assert result['manifest']['snapshot']['inputs']['script']['sentences'][0]['text']=='Original owner words.'
    file=client.get(f'/api/export-history/{eid}/file/video')
    assert file.status_code==200 and file.content==b'original synthetic export bytes'
    assert client.get(f'/api/export-history/{eid}/file/thumbnail').content==b'original synthetic thumbnail'


def test_internal_upstream_mutations_cannot_keep_an_old_green_script_or_render(session,produced):
    p,eid,root=produced
    p.facts={**p.facts,'primary_date':'2027-01-01'};session.commit()
    assert p.render is None and p.plan is None
    assert p.script['sentences'][0]['text']=='Original owner words.'
    assert p.script['sentences'][0]['check']=='unchecked'
    assert client.get(f'/api/export-history/{eid}/file/video').content==b'original synthetic export bytes'


def test_history_survives_project_deletion_and_cannot_restore_into_reused_identity(session,produced):
    p,eid,root=produced;pid=p.id
    response=client.delete(f'/api/studio/projects/{pid}')
    assert response.status_code==200,response.text
    assert not root.exists() and client.get(f'/api/export-history/{eid}/file/video').status_code==200
    session.expunge(p)  # The other HTTP session deleted this identity.
    replacement=StudioProject(id=pid,tmdb_id=999302,title='Different project with reused numeric ID')
    session.add(replacement);session.commit()
    current=client.get(f'/api/export-history?kind=studio&project_id={pid}').json()
    assert current['versions']==[]
    response=client.post(f'/api/export-history/{eid}/restore-draft',json={'reviewed':True,'expected_project_version':current['project_version']})
    assert response.status_code==409
    session.refresh(replacement);assert replacement.script is None
    assert any(r['id']==eid for r in client.get('/api/export-history').json()['versions'])


def test_deduplicated_blobs_and_raw_sql_immutability(session,produced):
    p,eid,root=produced
    p.review={'export_approval':{'fingerprint':'synthetic-inspection','approved_at':'fixture'}};session.commit()
    versions=session.query(ExportVersion).filter_by(kind='studio',project_id=p.id).all()
    assert len(versions)==2
    assert versions[0].manifest['assets']==versions[1].manifest['assets']
    for statement in ["UPDATE export_versions SET kind='countdown' WHERE id=:id",'DELETE FROM export_versions WHERE id=:id']:
        with pytest.raises(IntegrityError):session.execute(text(statement),{'id':eid})
        session.rollback()
    assert session.get(ExportVersion,eid)


def test_restore_is_versioned_preserves_new_words_until_review_and_invalidates_release(session,produced):
    p,eid,root=produced;pid=p.id
    before=client.get(f'/api/export-history?kind=studio&project_id={pid}').json()
    client.patch(f'/api/studio/projects/{pid}',json={'script':{'sentences':[{'text':'Newer owner words.'}]}})
    stale=client.post(f'/api/export-history/{eid}/restore-draft',json={'reviewed':True,'expected_project_version':before['project_version']})
    assert stale.status_code==409
    fresh=client.get(f'/api/export-history?kind=studio&project_id={pid}').json()
    args={'reviewed':True,'expected_project_version':fresh['project_version']}
    assert client.post(f'/api/export-history/{eid}/restore-draft',json={**args,'reviewed':False}).status_code==409
    response=client.post(f'/api/export-history/{eid}/restore-draft',json=args)
    assert response.status_code==200,response.text
    session.refresh(p)
    assert p.script['sentences'][0]['text']=='Original owner words.' and p.script['sentences'][0]['check']=='unchecked'
    assert p.render is None and p.plan is None and not (p.review or {}).get('export_approval')


def test_restore_refuses_active_queue_and_file_readers(session,produced):
    from concurrent.futures import ThreadPoolExecutor
    p,eid,root=produced;pid=p.id
    q=QueueItem(title='Active queue',status='ready',video_path=str(root/'original.mp4'))
    session.add(q);session.flush();p.queue_item_id=q.id;session.commit()
    current=client.get(f'/api/export-history?kind=studio&project_id={pid}').json()
    args={'reviewed':True,'expected_project_version':current['project_version']}
    assert client.post(f'/api/export-history/{eid}/restore-draft',json=args).status_code==409
    session.delete(q);p.queue_item_id=None;session.commit()
    args['expected_project_version']=client.get(f'/api/export-history?kind=studio&project_id={pid}').json()['project_version']
    with file_use.pin([root]):
        with ThreadPoolExecutor(1) as pool:
            response=pool.submit(lambda:client.post(f'/api/export-history/{eid}/restore-draft',json=args)).result(timeout=10)
            assert response.status_code==409


def test_force_delete_cannot_erase_unknown_submission_or_foreign_active_media(session,produced):
    p,eid,root=produced
    q=QueueItem(title='Ambiguous submission',status='error',video_path=str(root/'original.mp4'),
                publish_requests=[{'state':'unknown','client_request_id':'synthetic-uncertain'}])
    session.add(q);session.flush();p.queue_item_id=q.id;session.commit()
    assert client.delete(f'/api/studio/projects/{p.id}?force=true').status_code==409
    assert (root/'original.mp4').exists()
    q.publish_requests=[];p.queue_item_id=None;session.commit()
    assert client.delete(f'/api/studio/projects/{p.id}?force=true').status_code==409
    assert (root/'original.mp4').exists()


def test_deleted_project_preserves_settled_queue_bytes_and_records_relocation(session,produced):
    from backend.app.models import PublicationEvent
    p,eid,root=produced
    q=QueueItem(title='Settled archive',status='archived',video_path=str(root/'original.mp4'))
    session.add(q);session.flush();p.queue_item_id=q.id;session.commit();pid=p.id;qid=q.id
    response=client.delete(f'/api/studio/projects/{pid}')
    assert response.status_code==200,response.text
    session.refresh(q)
    assert not root.exists() and Path(q.video_path).read_bytes()==b'original synthetic export bytes'
    assert session.query(PublicationEvent).filter_by(queue_id=qid,kind='media_relocated').count()==1


def test_export_file_pass_cannot_read_private_inputs_or_restore(session,produced):
    p,eid,root=produced;query='?g='+auth.media_pass()['pass']
    public=TestClient(app)
    assert public.get(f'/api/export-history/{eid}/file/video'+query).status_code==200
    assert public.get(f'/api/export-history/{eid}'+query).status_code==401
    assert public.get('/api/export-history'+query).status_code==401
    assert public.post(f'/api/export-history/{eid}/restore-draft'+query,json={'reviewed':True,'expected_project_version':'ignored'}).status_code==401


def test_tampered_history_is_not_served(session,produced):
    p,eid,root=produced
    row=session.get(ExportVersion,eid);asset=next(a for a in row.manifest['assets'] if a['role']=='thumbnail')
    path=Path(asset['blob']);original=path.read_bytes();path.write_bytes(b'corrupted history')
    try:assert client.get(f'/api/export-history/{eid}/file/thumbnail').status_code==409
    finally:path.write_bytes(original)  # Only this test's synthetic shared blob.


def test_disk_pressure_preserves_the_previous_export_and_rolls_back_new_pointer(session,produced,monkeypatch):
    p,eid,root=produced;old=dict(p.render)
    replacement=root/'new.mp4';replacement.write_bytes(b'different synthetic export')
    monkeypatch.setattr(export_versions.shutil,'disk_usage',lambda path:SimpleNamespace(free=0))
    p.render={**p.render,'file':replacement.name,'rendered_at':'fixture-v2'}
    with pytest.raises(HTTPException,match=''):session.commit()
    session.rollback();session.refresh(p)
    assert p.render==old and client.get(f'/api/export-history/{eid}/file/video').content==b'original synthetic export bytes'
    assert not list((settings.data_path/'export_history'/'blobs').glob('.copy-*'))


def test_countdown_export_retains_current_script_and_draft_recovery(session):
    from uuid import uuid4
    root=settings.data_path/'studio'/'compilations'/uuid4().hex;root.mkdir(parents=True)
    video=root/'final_compilation.mp4';video.write_bytes(b'original countdown')
    c=CountdownProject(topic='Synthetic history',script={'intro':'Original hook','outro':'Original payoff','title':'Synthetic'})
    session.add(c);session.commit()
    c.render={'file':str(video),'compilation_id':root.name,'rendered_at':'countdown-v1'};session.commit()
    entries=client.get(f'/api/export-history?kind=countdown&project_id={c.id}').json()
    eid=entries['versions'][0]['id']
    c.script={**c.script,'intro':'Newer hook'};c.render=None;session.commit()
    current=client.get(f'/api/export-history?kind=countdown&project_id={c.id}').json()
    response=client.post(f'/api/export-history/{eid}/restore-draft',json={'reviewed':True,'expected_project_version':current['project_version']})
    assert response.status_code==200,response.text
    session.refresh(c);assert c.script['intro']=='Original hook' and c.render is None


def test_history_cannot_turn_a_private_backup_path_into_a_general_media_download(session):
    p=StudioProject(tmdb_id=999303,title='Malformed legacy render pointer');session.add(p);session.commit()
    private=settings.data_path/'backups'/'synthetic-private.db.gz';private.parent.mkdir(exist_ok=True)
    private.write_bytes(b'synthetic private database bytes')
    p.render={'file':str(private),'rendered_at':'untrusted-legacy'};session.commit()
    versions=client.get(f'/api/export-history?kind=studio&project_id={p.id}').json()['versions']
    eid=versions[0]['id']
    assert not versions[0]['assets'][0]['available']
    public=TestClient(app)
    assert public.get(f'/api/export-history/{eid}/file/video?g='+auth.media_pass()['pass']).status_code==404
