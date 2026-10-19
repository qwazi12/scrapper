"""Append-only publication evidence and explicit owner uncertainty decisions.

No external provider or publishing calls occur in these routes. A decision of
acceptance attaches a receipt for the ordinary read-only reconciler to inspect;
it never asserts that a post was published. Negative evidence returns to review.
"""
from __future__ import annotations
import copy
import hashlib
import json
from urllib.parse import urlsplit
from uuid import UUID
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from .auth import require_token
from .budget_reservations import lock
from .db import get_session
from .models import QueueItem,PublicationEvent,PublicationDecision

router=APIRouter(prefix='/api/queue',dependencies=[Depends(require_token)])


def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def identity(item):
    from datetime import timezone
    stamp=item.created_at
    if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=timezone.utc)
    return digest([item.id,stamp.astimezone(timezone.utc).isoformat()])


def append(s,qid,attempt,kind,payload,queue_identity=None):
    if not queue_identity:
        item=s.get(QueueItem,qid)
        if not item: return
        queue_identity=identity(item)
    key=digest([queue_identity,attempt,kind,payload])
    if s.get_bind().dialect.name=='sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    s.execute(insert(PublicationEvent).values(event_key=key,queue_id=qid,queue_identity=queue_identity,attempt_id=attempt,
        kind=kind,payload=copy.deepcopy(payload)).on_conflict_do_nothing(index_elements=['event_key']))


def record(s,item,entry,kind=None):
    ident=entry.get('client_request_id')
    if ident: append(s,item.id,ident,kind or entry.get('state','observation'),entry)


def snapshot(s,item):
    events=s.query(PublicationEvent).filter_by(queue_id=item.id,queue_identity=identity(item)).order_by(PublicationEvent.id).all()
    pending = unadopted_receipts(s, item)
    entries = copy.deepcopy(item.publish_requests or [])
    present = {e.get('client_request_id') for e in entries}
    for attempt, receipt in pending.items():
        if attempt not in present:
            entries.append({**receipt, 'state': 'unknown', 'error': 'Late receipt requires explicit adoption'})
    live=[]
    for e in entries:
        live.append({'entry':e,'version':digest(e), 'resolvable':(e.get('state') in ('intent','unknown') or e.get('client_request_id') in pending) and bool(e.get('client_request_id'))})
    return dict(queue_id=item.id,status=item.status,current=live,
        events=[dict(id=r.id,attempt_id=r.attempt_id,kind=r.kind,payload=r.payload,at=r.created_at)
                for r in events],
        decisions=[dict(id=r.id,attempt_id=r.attempt_id,evidence=r.evidence,at=r.created_at)
                   for r in s.query(PublicationDecision).filter_by(queue_id=item.id,queue_identity=identity(item)).order_by(PublicationDecision.id)])


@router.get('/{item_id}/publication-evidence')
def history(item_id:int,s:Session=Depends(get_session)):
    item=s.get(QueueItem,item_id)
    if not item: raise HTTPException(404,'Queue item not found')
    return snapshot(s,item)


class DecisionIn(BaseModel):
    request_key: UUID
    attempt_id: str = Field(min_length=8,max_length=80)
    expected_version: str = Field(min_length=64,max_length=64)
    account: str = Field(min_length=1,max_length=160)
    outcome: Literal['accepted','not_accepted','unresolved']
    provider_request_id: str | None = Field(default=None,max_length=160,pattern=r'^[a-zA-Z0-9_.:-]+$')
    evidence_url: str = Field(min_length=10,max_length=2048)
    rationale: str = Field(min_length=40,max_length=4000)
    reviewed_account: Literal[True]
    absence_confirmed: bool = False


@router.post('/{item_id}/publication-decisions')
def decide(item_id:int,req:DecisionIn,s:Session=Depends(get_session)):
    lock(s)
    item=s.get(QueueItem,item_id,populate_existing=True)
    if not item: raise HTTPException(404,'Queue item not found')
    previous=s.query(PublicationDecision).filter_by(request_key=str(req.request_key)).first()
    if previous:
        if previous.queue_id!=item_id or previous.queue_identity!=identity(item) or previous.evidence!=req.model_dump(mode='json'):
            raise HTTPException(409,'Decision key already records different evidence')
        return {'saved':previous.id,'duplicate':True}
    from .worker_leases import live
    if live(f'publication:{item_id}'): raise HTTPException(409,'A submission is still running; wait for it to finish before reviewing uncertainty')
    entries=copy.deepcopy(item.publish_requests or [])
    candidates=snapshot(s,item)['current']
    candidate=next((v for v in candidates if v['entry'].get('client_request_id')==req.attempt_id),None)
    if not candidate or not candidate['resolvable'] or candidate['version']!=req.expected_version:
        raise HTTPException(409,'Submission evidence changed or is already resolved; reload before deciding')
    entry=next((e for e in entries if e.get('client_request_id')==req.attempt_id),None)
    if entry is None:
        entry=copy.deepcopy(candidate['entry']);entries.append(entry)
    late=unadopted_receipts(s,item).get(req.attempt_id)
    expected=f"{entry['profile']}:{entry['platforms'][0]}" if entry.get('profile') and len(entry.get('platforms',[]))==1 else 'batch'
    if req.account!=expected: raise HTTPException(422,'Confirm this exact provider account and platform')
    url=urlsplit(req.evidence_url)
    if url.scheme!='https' or not url.hostname or url.username or url.password:
        raise HTTPException(422,'Record an HTTPS provider evidence URL without credentials')
    if req.outcome=='accepted':
        if expected=='batch' or not req.provider_request_id: raise HTTPException(422,'Acceptance needs an individual platform receipt')
        if late and late['request_id']!=req.provider_request_id:
            raise HTTPException(422,'Attach the exact late provider receipt shown in the journal')
        entry.pop('error',None);entry.pop('provider_status',None);entry.update(state='accepted',request_id=req.provider_request_id)
        item.status='posting'
    elif req.outcome=='not_accepted':
        if late: raise HTTPException(422,'A late receipt exists; attach it for provider reconciliation before claiming nonacceptance')
        if not req.absence_confirmed: raise HTTPException(422,'Explicitly confirm provider nonacceptance; elapsed time is not evidence')
        entry.update(state='confirmed_not_accepted',error='Owner reviewed provider evidence: '+req.rationale)
        entry.pop('request_id',None)
    data=req.model_dump(mode='json')
    row=PublicationDecision(request_key=str(req.request_key),queue_id=item_id,queue_identity=identity(item),attempt_id=req.attempt_id,evidence=data)
    s.add(row);append(s,item_id,req.attempt_id,'owner_decision',data)
    item.publish_requests=entries
    # No known submissions? Return to owner review, never to an automatic retry.
    if req.outcome=='not_accepted' and not any(e.get('state') in ('intent','unknown','accepted') for e in entries):
        item.status='review';item.scheduled_at=None
        targets=[f"{e['profile']}:{n}" for e in entries if e.get('state')=='confirmed_not_accepted' and e.get('profile') for n in e.get('platforms',[])]
        if targets:item.accounts=targets
    item.notes='Owner evidence recorded. Acceptance still requires provider reconciliation; nonacceptance requires renewed review.'
    s.commit()
    return dict(saved=row.id,duplicate=False,status=item.status)


def unadopted_receipts(s,item):
    events=s.query(PublicationEvent).filter_by(queue_id=item.id,queue_identity=identity(item)).order_by(PublicationEvent.id).all()
    known={e.get('request_id') for e in item.publish_requests or [] if e.get('state') in ('accepted','completed')}
    known|={e.payload.get('request_id') for e in events if e.kind=='settled' and e.payload.get('state')=='completed'}
    return {e.attempt_id:e.payload for e in events if e.kind=='transport_result' and e.payload.get('request_id') and e.payload['request_id'] not in known}


def unresolved_late_receipt(s,item):
    return bool(unadopted_receipts(s,item))


@router.get('/publication-ledger/export')
def export_ledger(limit:int=500,s:Session=Depends(get_session)):
    if not 1<=limit<=2000:raise HTTPException(422,'Choose 1–2000 journal events')
    return [dict(id=r.id,queue_id=r.queue_id,queue_identity=r.queue_identity,attempt_id=r.attempt_id,
                 kind=r.kind,payload=r.payload,at=r.created_at)
            for r in s.query(PublicationEvent).order_by(PublicationEvent.id.desc()).limit(limit)]
