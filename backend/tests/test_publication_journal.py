"""Publication uncertainty tests use synthetic files and fake provider boundaries."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app import publication_journal as journal, worker_leases as leases
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import PublicationDecision, PublicationEvent, QueueItem
from backend.app.social import queue_manager as qm, upload_post


@pytest.fixture
def client():
    return TestClient(app, headers={'x-access-token': 'test-token'})


def row(s, **kwargs):
    entry = dict(profile='default', platforms=['youtube'], state='unknown',
                 client_request_id=uuid4().hex, submitted_at=datetime.now(timezone.utc).isoformat())
    item = QueueItem(title='Synthetic uncertainty', pipeline='Test', accounts=['default:youtube'],
                     status='posting', publish_requests=[entry], **kwargs)
    s.add(item); s.commit()
    journal.record(s, item, entry); s.commit()
    return item, entry


def decision(client, item, **kwargs):
    view = client.get(f'/api/queue/{item.id}/publication-evidence').json()['current'][-1]
    return dict(request_key=str(uuid4()), attempt_id=view['entry']['client_request_id'],
                expected_version=view['version'], account='default:youtube', outcome='accepted',
                provider_request_id='fixture-receipt', evidence_url='https://provider.example/receipt',
                rationale='I reviewed the exact provider account, submission time and receipt identity.',
                reviewed_account=True, **kwargs)


def send(client, item, data):
    return client.post(f'/api/queue/{item.id}/publication-decisions', json=data)


def test_lost_response_owner_receipt_reconciles_without_resubmitting(session, client, tmp_path, monkeypatch):
    file = tmp_path/'synthetic.mp4'; file.write_bytes(b'synthetic transport placeholder')
    item = QueueItem(title='Synthetic fixture', description='Original description', tags='#fixture',
                     status='review', pipeline='Test', accounts=['default:youtube'], video_path=str(file))
    session.add(item); session.commit(); qid=item.id
    calls=[]
    async def accounts(): return [{'id':'default:youtube','profile':'default'}]
    async def lost(*a, **kw):
        with SessionLocal() as read:
            saved=read.get(QueueItem,qid)
            assert saved.publish_requests[-1]['state']=='intent'
            assert read.query(PublicationEvent).filter_by(queue_id=qid,queue_identity=journal.identity(saved),kind='intent').count()==1
        calls.append(kw['idempotency_key'])
        raise TimeoutError('Synthetic lost response after acceptance')
    monkeypatch.setattr(upload_post,'list_accounts',accounts)
    monkeypatch.setattr(upload_post,'upload_video',lost)
    monkeypatch.setattr(qm,'auto_seo_enabled',lambda s:False)
    asyncio.run(qm.publish_queue_item(qid,session))
    assert len(calls)==1 and item.publish_requests[0]['state']=='unknown'
    data=decision(client,item)
    result=send(client,item,data); assert result.status_code==200,result.text
    assert send(client,item,data).json()['duplicate'] is True
    assert len(calls)==1  # Recording owner evidence performs no provider call.
    async def status(rid):
        assert rid=='fixture-receipt'
        return {'status':'completed','results':[{'profile_username':'default','platform':'youtube','success':True,'post_url':'https://provider.example/post/1'}]}
    monkeypatch.setattr(upload_post,'get_status',status)
    session.expire_all()
    assert qm.reconcile_posting(session,datetime.now(timezone.utc))==1
    assert item.status=='archived' and len(calls)==1
    before=client.get(f'/api/queue/{qid}/publication-evidence').json()
    assert {'batch_intent','intent','transport_result','owner_decision','provider_observation','settled'} <= {e['kind'] for e in before['events']}
    assert qm.reconcile_posting(session,datetime.now(timezone.utc))==0
    assert client.get(f'/api/queue/{qid}/publication-evidence').json()==before


def test_explicit_nonacceptance_requires_evidence_and_returns_to_review(session,client):
    item,_=row(session)
    data=decision(client,item);data.update(outcome='not_accepted',provider_request_id=None)
    assert send(client,item,data).status_code==422
    data['absence_confirmed']=True
    wrong={**data,'account':'another:youtube'}
    assert send(client,item,wrong).status_code==422
    assert send(client,item,{**data,'expected_version':'0'*64}).status_code==409
    assert send(client,item,{**data,'evidence_url':'https://secret@provider.example/x'}).status_code==422
    assert send(client,item,data).status_code==200
    session.refresh(item)
    assert item.status=='review' and item.scheduled_at is None
    assert item.publish_requests[0]['state']=='confirmed_not_accepted'
    assert send(client,item,{**data,'request_key':str(uuid4())}).status_code==409
    assert send(client,item,{**data,'rationale':data['rationale']+' changed'}).status_code==409


def test_competing_decisions_only_one_version_wins(session,client):
    item,_=row(session)
    data=decision(client,item)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda d:send(client,item,d).status_code,[data,{**data,'request_key':str(uuid4()),'provider_request_id':'other-receipt'}]))
    assert sorted(results)==[200,409]
    assert session.query(PublicationDecision).filter_by(queue_identity=journal.identity(item)).count()==1


def test_live_submission_refuses_resolution_and_early_reconciliation(session,client,monkeypatch):
    item,_=row(session)
    data=decision(client,item)
    with leases.scope(f'publication:{item.id}'):
        assert send(client,item,data).status_code==409
        monkeypatch.setattr(qm,'_fetch_statuses',lambda e:pytest.fail('In-flight batch must not be polled'))
        assert qm.reconcile_posting(session,datetime.now(timezone.utc))==0


def test_late_orphan_receipt_survives_and_requires_exact_adoption(session,client,monkeypatch):
    item,entry=row(session)
    orphan={**entry,'client_request_id':uuid4().hex,'state':'accepted','request_id':'late-receipt'}
    journal.record(session,item,orphan,'transport_result');session.commit()
    assert journal.unresolved_late_receipt(session,item)
    monkeypatch.setattr(qm,'_fetch_statuses',lambda e:pytest.fail('Review late receipt before polling settlement'))
    assert qm.reconcile_posting(session,datetime.now(timezone.utc))==0
    data=decision(client,item)
    assert data['attempt_id']==orphan['client_request_id']
    assert send(client,item,{**data,'outcome':'not_accepted','absence_confirmed':True}).status_code==422
    assert send(client,item,data).status_code==422
    data['provider_request_id']='late-receipt'
    assert send(client,item,data).status_code==200
    session.refresh(item)
    assert not journal.unresolved_late_receipt(session,item)
    assert item.publish_requests[-1]['request_id']=='late-receipt'


def test_journal_cannot_be_updated_or_deleted_and_survives_row_id_reuse(session,client):
    item,entry=row(session);old_id=item.id;old_identity=journal.identity(item)
    for sql in ('UPDATE publication_events SET kind = :kind WHERE queue_identity = :identity',
                'DELETE FROM publication_events WHERE queue_identity = :identity'):
        with pytest.raises(IntegrityError,match='append-only'):
            session.execute(text(sql),{'kind':'erased','identity':old_identity});session.commit()
        session.rollback()
    session.delete(session.get(QueueItem,old_id));session.commit()
    newer=QueueItem(id=old_id,title='A distinct row',pipeline='Test',accounts=[],status='review')
    session.add(newer);session.commit()
    assert journal.identity(newer)!=old_identity
    view=client.get(f'/api/queue/{old_id}/publication-evidence').json()
    assert view['events']==[] and view['decisions']==[]
    exported=client.get('/api/queue/publication-ledger/export').json()
    assert any(e['queue_identity']==old_identity for e in exported)
    assert TestClient(app).get('/api/queue/publication-ledger/export').status_code==401


@pytest.mark.parametrize('results',[
    [],[{'platform':'youtube','success':'true'}],
    [{'platform':'instagram','success':True}],
    [{'platform':'youtube','success':True}]*2,
    [{'platform':'youtube','profile_username':'wrong','success':True}],
])
def test_malformed_completed_receipt_is_not_cached_as_final(session,monkeypatch,results):
    item,entry=row(session)
    item.publish_requests=[{**entry,'state':'accepted','request_id':'fixture-rid'}];session.commit()
    responses=[{'status':'completed','results':results},
               {'status':'completed','results':[{'platform':'youtube','success':True}]}]
    async def status(rid):return responses.pop(0)
    monkeypatch.setattr(upload_post,'get_status',status)
    assert qm.reconcile_posting(session,datetime.now(timezone.utc))==0
    assert 'provider_status' not in item.publish_requests[0]
    assert item.status=='posting'
    assert qm.reconcile_posting(session,datetime.now(timezone.utc))==1
    assert item.status=='archived'


def test_owner_decision_during_provider_read_is_not_overwritten(session,client,monkeypatch):
    item,entry=row(session)
    accepted={**entry,'client_request_id':uuid4().hex,'state':'accepted','request_id':'known-rid'}
    item.publish_requests=[accepted,entry];session.commit()
    data=decision(client,item)
    async def status(rid):
        assert send(client,item,data).status_code==200
        return {'status':'completed','results':[{'platform':'youtube','success':True}]}
    monkeypatch.setattr(upload_post,'get_status',status)
    assert qm.reconcile_posting(session,datetime.now(timezone.utc))==0
    session.refresh(item)
    assert item.publish_requests[-1]['state']=='accepted'
    assert item.publish_requests[-1]['request_id']=='fixture-receipt'


def test_transport_receipt_survives_lease_revocation(session,client,tmp_path,monkeypatch):
    file=tmp_path/'fixture.mp4';file.write_bytes(b'original synthetic transport fixture')
    item=QueueItem(title='Revoked transport',status='review',pipeline='Test',description='Complete synthetic fixture',
                   tags='#synthetic',accounts=['default:youtube'],video_path=str(file))
    session.add(item);session.commit();qid=item.id
    async def accounts():return [{'id':'default:youtube','profile':'default'}]
    async def delayed(*args,**kwargs):
        with leases.bind(None),SessionLocal() as owner:
            leases.revoke(owner,f'publication:{qid}');owner.commit()
        return 'arrived-after-revocation'
    monkeypatch.setattr(upload_post,'list_accounts',accounts)
    monkeypatch.setattr(upload_post,'upload_video',delayed)
    monkeypatch.setattr(qm,'auto_seo_enabled',lambda s:False)
    with pytest.raises(upload_post.UploadPostError,match='expired or superseded'):
        asyncio.run(qm.publish_queue_item(qid,session))
    session.rollback();session.refresh(item)
    view=client.get(f'/api/queue/{qid}/publication-evidence').json()
    assert any(e['payload'].get('request_id')=='arrived-after-revocation' for e in view['events'])
    assert item.publish_requests[0]['state']=='intent'
    assert journal.unresolved_late_receipt(session,item)
    data=decision(client,item);data['provider_request_id']='arrived-after-revocation'
    assert send(client,item,data).status_code==200
