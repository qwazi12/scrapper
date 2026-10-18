import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import undo
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import QueueItem,StudioProject
from backend.app.social import ai_bulk


def test_undo_cannot_erase_a_submission_intent(session):
    row=QueueItem(title='Original',accounts=['default:youtube'],status='review');session.add(row);session.commit()
    undo.record(session,'queue','Edit title',rows=[row]);row.title='Edited';session.commit()
    row.status='posting';row.publish_requests=[{'state':'intent','client_request_id':uuid4().hex}];session.commit()
    client=TestClient(app,headers={'x-access-token':'test-token'})
    response=client.post('/api/undo',json={'scope':'queue'})
    assert response.status_code==409,response.text
    session.refresh(row)
    assert row.title=='Edited' and row.publish_requests[0]['state']=='intent'


def test_undo_rechecks_narration_after_upstream_evidence_changes(session):
    p=StudioProject(tmdb_id=99221,title='Synthetic undo',facts={'version':'old'},
        script={'sentences':[{'text':'Original fact','check':'ok'}]})
    session.add(p);session.commit();pid=p.id
    undo.record(session,f'studio:{pid}','Edit script',rows=[p],model='studio_projects',fields=['script','plan','render'])
    p.script={'sentences':[{'text':'Revised fact','check':'unchecked'}]};p.facts={'version':'new'};session.commit()
    undo.undo(session,f'studio:{pid}')
    assert p.script['sentences'][0]['text']=='Original fact'
    assert p.script['sentences'][0]['check']=='unchecked' and p.plan is None and p.render is None


def test_bulk_ai_cannot_overwrite_metadata_edited_during_provider_io(session,monkeypatch):
    row=QueueItem(title='Initial owner title',description='Original',status='review',accounts=['default:youtube'])
    session.add(row);session.commit();qid=row.id
    async def generate(item,s,cache):
        with SessionLocal() as owner:
            owner.get(QueueItem,qid).title='Newer owner title';owner.commit()
        return {'title':'Stale generated title'},None
    monkeypatch.setattr(ai_bulk,'ai_for',generate)
    monkeypatch.setattr(ai_bulk,'status',{**ai_bulk.status,'aborted':None,'failed':0,'done':0,'errors':[]})
    asyncio.run(ai_bulk._one(qid,asyncio.Semaphore(1)))
    session.refresh(row)
    assert row.title=='Newer owner title' and ai_bulk.status['failed']==1 and ai_bulk.status['done']==0


def test_bulk_ai_never_dispatches_for_submitted_items(session,monkeypatch):
    row=QueueItem(title='Submitted',status='posting',accounts=['default:youtube'])
    session.add(row);session.commit()
    calls=[]
    async def generate(*args):calls.append(True)
    monkeypatch.setattr(ai_bulk,'ai_for',generate)
    monkeypatch.setattr(ai_bulk,'status',{**ai_bulk.status,'aborted':None,'failed':0,'done':0,'errors':[]})
    asyncio.run(ai_bulk._one(row.id,asyncio.Semaphore(1)))
    assert calls==[] and ai_bulk.status['failed']==1
