"""Original Studio dispatch requires retained script/fact/media provenance."""
import asyncio

import pytest

from backend.app.db import SessionLocal
from backend.app.models import QueueItem
from backend.app.social import queue_manager as qm,upload_post


@pytest.mark.parametrize('source',['LongForm Studio','Countdowns Studio'])
def test_unbound_original_studio_row_cannot_dispatch_without_source_evidence(tmp_path,monkeypatch,source):
    file=tmp_path/'synthetic-opaque.mp4';file.write_bytes(b'isolated opaque fixture; not media-quality evidence')
    with SessionLocal() as s:
        item=QueueItem(title='Unbound original studio fixture',source=source,pipeline='LongForm' if source=='LongForm Studio' else 'Countdowns',
            video_path=str(file),status='ready',accounts=['default:youtube'],research={'matched':True})
        s.add(item);s.commit();qid=item.id
    calls=[]
    async def accounts():return [{'id':'default:youtube','profile':'default'}]
    async def upload(*args,**kwargs):calls.append(kwargs);return 'synthetic-must-not-submit'
    monkeypatch.setattr(upload_post,'list_accounts',accounts);monkeypatch.setattr(upload_post,'upload_video',upload)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError,match='source') as error:
        asyncio.run(qm.publish_queue_item(qid,s))
    assert error.value.status_code==409 and not calls
    with SessionLocal() as s:assert not s.get(QueueItem,qid).publish_requests


@pytest.fixture
def reviewed_source(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.models import StudioProject
    from backend.app.studio import runner,media,media_quality,routes
    client=TestClient(app,headers={'x-access-token':'test-token'})
    monkeypatch.setattr(routes,'_start_drive',lambda project:None)
    monkeypatch.setattr(qm,'wake',lambda:None)
    monkeypatch.setattr(qm,'default_destination',lambda profile:['default:youtube'])
    def produce():
        with SessionLocal() as s:
            project=StudioProject(tmdb_id=99200,title='Synthetic source binding',stage_status='done',facts={
                'title':'Synthetic source binding','primary_date':'2026-01-01','media_type':'movie',
                'cast':[{'actor':'Ada Example','character':'The Traveler'}]},
                script={'sentences':[{'paragraph':1,'text':'Ada Example plays The Traveler.','check':'ok','refs':['F1']}],
                    'youtube_title':'Original source proof fixture','description':'Original procedural footage used for isolated proof tests.',
                    'tags':['original'],'check_issues':[],'storyboard':{'F':['Synthetic credit'],'R':[]}},plan=[],shots=[])
            s.add(project);s.commit();pid=project.id
        root=runner.project_dir(pid);root.mkdir(parents=True,exist_ok=True);file=root/'final.mp4'
        media.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=1',
            '-f','lavfi','-i',f'sine=f={700+pid*13}:d=1','-c:a','aac','-c:v','libx264','-pix_fmt','yuv420p',str(file)])
        qa=media_quality.inspect_export(file,expected_seconds=1)
        assert not qa['errors']
        with SessionLocal() as s:
            project=s.get(StudioProject,pid);project.render={'file':'final.mp4','quality':qa};s.commit()
        response=client.get(f'/api/studio/projects/{pid}/export-review');assert response.status_code==200,response.text
        current=response.json()
        response=client.post(f'/api/studio/projects/{pid}/approve-export',json={
            'expected_fingerprint':current['fingerprint'],'checks':list(current['checks']),
            'warning_decisions':{row['id']:'Original generated fixture intentionally inspected as a local quality/admission test.' for row in current['findings']},
            'rights_rationale':'All test footage and tone is originally generated for this isolated fixture; no real-world facts are asserted.', 'reviewed':True})
        assert response.status_code==200,response.text
        return client,pid,file,qa
    return produce


def test_real_reviewed_source_is_retained_bound_and_dispatched_with_its_receipt(reviewed_source,monkeypatch):
    from pathlib import Path
    from backend.app import queue_sources
    from backend.app.models import PublicationEvent
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);item.accounts=['default:youtube'];s.commit()
        bound=queue_sources.head(s,item);assert bound and bound.revision==1
        proof=queue_sources.verify(s,item);assert proof.id==bound.id
        retained=Path(item.video_path);assert retained!=original and retained.read_bytes()==original.read_bytes()
        bid=bound.id
    # The source render is mutable; the queued approved bytes remain the retained version.
    original.write_bytes(b'replaced original working path')
    calls=[]
    async def accounts():return [{'id':'default:youtube','profile':'default'}]
    async def upload(path,**kwargs):
        assert path==retained and path.read_bytes()!=b'replaced original working path'
        with SessionLocal() as check:
            row=check.get(QueueItem,qid);assert row.publish_requests[-1]['source_binding_id']==bid
        calls.append(path);return 'synthetic-source-bound-receipt'
    monkeypatch.setattr(upload_post,'list_accounts',accounts);monkeypatch.setattr(upload_post,'upload_video',upload)
    with SessionLocal() as s:
        result=asyncio.run(qm.publish_queue_item(qid,s));assert result.status=='posting'
        assert result.publish_requests[-1]['source_binding_id']==bid
        assert s.query(PublicationEvent).filter_by(queue_id=qid,kind='transport_result').first().payload['source_binding_id']==bid
    assert len(calls)==1


def test_source_copy_edits_and_paid_rewrites_are_rejected_before_work(reviewed_source,monkeypatch):
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    assert client.patch(f'/api/queue/{qid}',json={'title':'Unreviewed replacement title'}).status_code==409
    assert client.post('/api/queue/bulk-action',json={'ids':[qid],'action':'edit','description':'Unreviewed bulk words'}).status_code==409
    assert client.post(f'/api/queue/{qid}/generate-ai').status_code==409
    assert client.post(f'/api/queue/{qid}/thumbnail',files={'file':('synthetic.png',b'opaque placeholder')}).status_code==409
    assert client.patch(f'/api/queue/{qid}',json={'notes':'Private owner notes preserved','accounts':['default:youtube']}).status_code==200


def test_binding_journal_is_append_only_and_survives_queue_id_reuse(reviewed_source):
    from datetime import timedelta
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    from backend.app import queue_sources
    from backend.app.models import QueueSourceBinding
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);bound=queue_sources.head(s,item);bid=bound.id;stamp=item.created_at
    for query in ['UPDATE queue_source_bindings SET revision=99 WHERE id=:id','DELETE FROM queue_source_bindings WHERE id=:id']:
        with SessionLocal() as s,pytest.raises(IntegrityError,match='append-only'):
            s.execute(text(query),{'id':bid});s.commit()
    with SessionLocal() as s:
        s.delete(s.get(QueueItem,qid));s.commit()
        newer=QueueItem(id=qid,created_at=stamp+timedelta(seconds=1),title='Different later manual row',source='Manual original fixture',
            video_path=str(original),status='review',accounts=[])
        s.add(newer);s.commit()
        assert queue_sources.head(s,newer) is None and s.get(QueueSourceBinding,bid) is not None


@pytest.mark.parametrize('changed',['missing','corrupt','redirect-outside'])
def test_bad_retained_bytes_never_fall_back_to_an_unproven_drive_copy(reviewed_source,monkeypatch,tmp_path,changed):
    from pathlib import Path
    from backend.app import drive_sync
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);item.accounts=['default:youtube'];item.drive_link=f'https://drive.google.com/file/d/synthetic-only-{qid}/view'
        s.commit();path=Path(item.video_path)
    before=path.read_bytes()
    if changed=='missing':path.unlink()
    elif changed=='corrupt':path.write_bytes(b'corrupted retained source')
    else:
        outside=tmp_path/'outside-opaque-test.mp4';outside.write_bytes(before);path.unlink();path.symlink_to(outside)
    monkeypatch.setattr(drive_sync,'download_drive_file',lambda *args:pytest.fail('Unproven fallback must not run'))
    async def no_provider(*args,**kwargs):pytest.fail('Invalid source must not reach provider')
    monkeypatch.setattr(upload_post,'upload_video',no_provider)
    try:
        with SessionLocal() as s,pytest.raises(upload_post.UploadPostError) as error:asyncio.run(qm.publish_queue_item(qid,s))
        assert error.value.status_code==409
        with SessionLocal() as s:assert not s.get(QueueItem,qid).publish_requests
    finally:
        if path.is_symlink():path.unlink()
        path.write_bytes(before)


def test_source_revision_change_after_validation_rejects_the_atomic_intent_claim(reviewed_source,monkeypatch):
    from copy import deepcopy
    from backend.app import queue_sources
    from backend.app.models import QueueSourceBinding
    from backend.app.publication_journal import digest
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    with SessionLocal() as s:item=s.get(QueueItem,qid);item.accounts=['default:youtube'];s.commit()
    verify=queue_sources.verify;changed=[]
    def later_policy(session,item):
        proof=verify(session,item)
        if not changed:
            with SessionLocal() as other:
                data=deepcopy(proof.data);data['policy']='future-source-policy-requires-new-worker'
                revision=proof.revision+1
                newer=QueueSourceBinding(id=digest([proof.queue_identity,revision,data]),queue_id=qid,
                    queue_identity=proof.queue_identity,revision=revision,data=data)
                other.add(newer);other.commit();changed.append(newer.id)
        return proof
    monkeypatch.setattr(queue_sources,'verify',later_policy)
    async def no_provider(*args,**kwargs):pytest.fail('An older source generation cannot submit')
    monkeypatch.setattr(upload_post,'upload_video',no_provider)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError,match='changed'):
        asyncio.run(qm.publish_queue_item(qid,s))
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);assert item.status=='ready' and not item.publish_requests
        assert queue_sources.head(s,item).id==changed[0]


@pytest.mark.parametrize('field,value', [('source','Manual replacement'),('pipeline','Shorts'),('research',{'matched':False})])
def test_source_metadata_change_after_validation_rejects_atomic_claim(reviewed_source,monkeypatch,field,value):
    from backend.app import queue_sources
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id'];verify=queue_sources.verify
    def changed(session,item):
        proof=verify(session,item)
        with SessionLocal() as other:
            setattr(other.get(QueueItem,qid),field,value);other.commit()
        return proof
    monkeypatch.setattr(queue_sources,'verify',changed)
    async def no_provider(*args,**kwargs):pytest.fail('Changed source metadata cannot submit')
    monkeypatch.setattr(upload_post,'upload_video',no_provider)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError,match='changed'):
        asyncio.run(qm.publish_queue_item(qid,s))
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);assert item.status=='ready' and not item.publish_requests
        assert getattr(item,field)==value


def test_source_identity_stays_in_a_receipt_after_publication_lease_is_revoked(reviewed_source,monkeypatch):
    from backend.app import queue_sources,worker_leases as leases,publication_journal as journal
    from backend.app.models import PublicationEvent
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);item.accounts=['default:youtube'];s.commit();bid=queue_sources.head(s,item).id
    async def accounts():return [{'id':'default:youtube','profile':'default'}]
    async def delayed(*args,**kwargs):
        with leases.bind(None),SessionLocal() as owner:leases.revoke(owner,f'publication:{qid}');owner.commit()
        return 'synthetic-late-bound-source-receipt'
    monkeypatch.setattr(upload_post,'list_accounts',accounts);monkeypatch.setattr(upload_post,'upload_video',delayed)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError):asyncio.run(qm.publish_queue_item(qid,s))
    with SessionLocal() as s:
        row=s.query(PublicationEvent).filter_by(queue_id=qid,kind='transport_result').one()
        assert row.payload['source_binding_id']==bid and row.payload['request_id']=='synthetic-late-bound-source-receipt'
        assert journal.unresolved_late_receipt(s,s.get(QueueItem,qid))


def test_legacy_or_recycled_numeric_link_is_not_adopted_or_overwritten(reviewed_source):
    from backend.app.models import StudioProject
    from backend.app import queue_sources
    client,pid,original,qa=reviewed_source()
    with SessionLocal() as s:
        unrelated=QueueItem(title='Different manual owner words',description='These words belong to another item.',source='Manual fixture',
            status='review',video_path=str(original),accounts=[])
        s.add(unrelated);s.flush();oldid=unrelated.id
        s.get(StudioProject,pid).queue_item_id=oldid;s.commit()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    new_id=response.json()['queue_item_id'];assert new_id!=oldid
    with SessionLocal() as s:
        older=s.get(QueueItem,oldid);assert older.title=='Different manual owner words' and older.status=='review'
        assert queue_sources.head(s,older) is None and queue_sources.head(s,s.get(QueueItem,new_id))


def test_repeated_source_send_reuses_current_binding_without_discarding_history(reviewed_source):
    from backend.app import queue_sources
    client,pid,original,qa=reviewed_source()
    first=client.post(f'/api/studio/projects/{pid}/publish');assert first.status_code==200,first.text
    qid=first.json()['queue_item_id']
    with SessionLocal() as s:bid=queue_sources.head(s,s.get(QueueItem,qid)).id
    second=client.post(f'/api/studio/projects/{pid}/publish');assert second.status_code==200,second.text
    assert second.json()['queue_item_id']==qid
    with SessionLocal() as s:
        bound=queue_sources.head(s,s.get(QueueItem,qid));assert bound.id==bid and bound.revision==1


def test_native_bulk_copy_edit_is_also_refused_before_creating_an_unreleasable_row():
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.tests.test_native_campaigns import rendered,approve_native
    client=TestClient(app,headers={'x-access-token':'test-token'})
    oid,cid,export=rendered(client);approve_native(client,export['id'])
    queued=client.post(f"/api/editorial/exports/{export['id']}/enqueue",json={'accounts':['default:instagram']}).json()
    response=client.post('/api/queue/bulk-action',json={'ids':[queued['queue_item_id']],'action':'edit','title':'Unreviewed bulk native copy'})
    assert response.status_code==409,response.text


def test_retained_script_is_checked_even_when_live_project_is_different_and_green(reviewed_source,monkeypatch):
    from copy import deepcopy
    from types import SimpleNamespace
    from datetime import datetime,timezone
    from backend.app import queue_sources
    from backend.app.models import QueueSourceBinding,ExportVersion,StudioProject
    from backend.app.publication_journal import digest
    from backend.app.studio.export_acceptance import accept,AcceptanceIn,view
    from backend.app.studio.runner import project_dir
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);item.accounts=['default:youtube'];s.flush()
        previous=queue_sources.head(s,item);source=s.get(ExportVersion,previous.data['source_version'])
        payload=deepcopy(source.manifest['snapshot'])
        words='Invented Performer plays The Traveler.'
        payload['inputs']['script']['sentences'][0]['text']=words
        historical=SimpleNamespace(**payload['inputs'],render=payload['render'],review={},created_at=source.manifest['project_created_at'])
        current=view(historical,project_dir(pid))
        request=AcceptanceIn(expected_fingerprint=current['fingerprint'],checks=list(current['checks']),
            warning_decisions={r['id']:'Historical synthetic media fixture, not proof of independent factual truth.' for r in current['findings']},
            rights_rationale='Original generated media with a synthetic historical checker mistake for admission regression.',reviewed=True)
        payload['approval']=accept(historical,project_dir(pid),request,datetime.now(timezone.utc))
        eid=digest([source.kind,source.project_identity,payload])
        manifest={**deepcopy(source.manifest),'snapshot':payload}
        s.add(ExportVersion(id=eid,kind=source.kind,project_id=pid,project_identity=source.project_identity,manifest=manifest))
        data=deepcopy(previous.data);data['source_version']=eid;revision=previous.revision+1
        s.add(QueueSourceBinding(id=digest([previous.queue_identity,revision,data]),queue_id=qid,
            queue_identity=previous.queue_identity,revision=revision,data=data));s.commit()
        assert s.get(StudioProject,pid).script['sentences'][0]['text']!=words
    async def no_provider(*args,**kwargs):pytest.fail('A retained factual conflict cannot dispatch')
    monkeypatch.setattr(upload_post,'upload_video',no_provider)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError,match='structured cast relationship'):
        asyncio.run(qm.publish_queue_item(qid,s))
    with SessionLocal() as s:assert not s.get(QueueItem,qid).publish_requests


def test_retained_source_does_not_adopt_a_reused_live_project_identity(reviewed_source):
    from datetime import timedelta
    from backend.app import queue_sources
    from backend.app.models import StudioProject,ExportVersion
    from backend.app.publication_journal import identity
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id']
    with SessionLocal() as s:
        original_project=s.get(StudioProject,pid);stamp=original_project.created_at
        s.delete(original_project);s.commit()
        replacement=StudioProject(id=pid,created_at=stamp+timedelta(seconds=1),tmdb_id=99299,title='Different later source identity',
            facts={'title':'A different film','cast':[]},script=None)
        s.add(replacement);s.commit()
        item=s.get(QueueItem,qid);proof=queue_sources.verify(s,item)
        old=s.get(ExportVersion,proof.data['source_version'])
        assert old.project_identity!=identity(replacement)
        assert old.manifest['snapshot']['inputs']['facts']['title']=='Synthetic source binding'


def test_source_context_is_owner_only_identity_bound_and_read_only(reviewed_source):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import auth,queue_sources
    client,pid,original,qa=reviewed_source()
    response=client.post(f'/api/studio/projects/{pid}/publish');assert response.status_code==200,response.text
    qid=response.json()['queue_item_id'];url=f'/api/queue/{qid}/source-context'
    public=TestClient(app)
    assert public.get(url).status_code==401
    assert public.get(url+'?g='+auth.media_pass()['pass']).status_code==401
    assert client.get(url,params={'expected_created_at':'2000-01-01T00:00:00Z'}).status_code==409
    inspected=client.get(url);assert inspected.status_code==200,inspected.text
    current=inspected.json();assert current['state']=='checks_passed'
    assert current['source']['facts']['title']=='Synthetic source binding'
    assert current['source']['current_project_matches'] and current['source']['assets'][0]['sha256']==qa['sha256']
    assert 'blob' not in current['source']['assets'][0]
    with SessionLocal() as s:
        item=s.get(QueueItem,qid);bid=queue_sources.head(s,item).id
        assert current['binding']['id']==bid and item.status=='ready' and not item.publish_requests
        assert queue_sources.head(s,item).revision==1
