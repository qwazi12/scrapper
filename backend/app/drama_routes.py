"""Authenticated owner operations; isolated from generic queue/schedule wakes."""
from __future__ import annotations

import contextlib
import contextvars
import asyncio
import datetime as dt
from typing import Literal
from uuid import uuid4
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import drama_archive, drama_controls as dc, pending_metadata as pm, publication_journal as journal, worker_leases
from .auth import require_token
from .budget_reservations import lock
from .db import get_session
from .models import AppSetting, DramaEpisode, DramaRecord, PublicationEvent, QueueItem, WorkerLease

router = APIRouter(prefix='/api/drama', dependencies=[Depends(require_token)])
_owned_handles = contextvars.ContextVar('drama_owned_handles', default=())


def lock_owned(s):
    lock(s)
    # Each row's publication generation is held through this content commit,
    # including multi-row plans where the innermost ContextVar covers one row.
    for handle in _owned_handles.get():
        worker_leases.guard(s, handle)


class Operation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation_id: str = Field(pattern=pm.OPERATION_PATTERN)


class ItemOperation(Operation):
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


class Inspect(ItemOperation):
    manifest_id: str = Field(pattern=r'^[A-Za-z0-9_-]{10,160}$')
    source_video_id: str = Field(pattern=r'^\d{15,24}$')


class XVariant(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str = Field(min_length=1, max_length=2000)
    approved_revision: str = Field(min_length=1, max_length=120)
    approved_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class DerivativeReview(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    derivative_id:str=Field(pattern=pm.OPERATION_PATTERN)
    document_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    decision:Literal['I reviewed the exact original and derivative video and every audio channel, and approve this exact lineage artifact without cuts or content changes']


class ReviewContent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    evidence_id: str = Field(pattern=pm.OPERATION_PATTERN)
    approved_revision: str = Field(min_length=1, max_length=120)
    approved_workbook_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    approved_proposal_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['Accept this exact canonical episode and public copy for guarded release']
    facebook_media_type: Literal['REELS', 'VIDEO'] = 'VIDEO'
    x_variant: XVariant | None = None
    derivative: DerivativeReview | None = None


class ReviewPreview(ItemOperation, ReviewContent):
    pass


class Review(ReviewPreview):
    acknowledged_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


class Ready(ItemOperation):
    evidence_id: str = Field(pattern=pm.OPERATION_PATTERN)
    review_id: str = Field(pattern=pm.OPERATION_PATTERN)


class Selected(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: int = Field(gt=0)
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


class Plan(Operation):
    expected_plan_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    series: list[str] = Field(min_length=2, max_length=2)
    items: list[Selected] = Field(min_length=1, max_length=500)


class Rollback(Operation):
    expected_plan_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')


def request_hash(kind, req, qid=None):
    request=req.model_dump(exclude={'operation_id'})
    if request.get('derivative','absent') is None:request.pop('derivative',None)
    return pm.digest({'version': dc.VERSION, 'kind': kind, 'queue_id': qid,
                      'request': request})


@contextlib.contextmanager
def owned(s, queue_ids, *, plan=False):
    s.rollback()
    try:
        with contextlib.ExitStack() as scopes:
            handles = []
            if plan:
                handles.append(scopes.enter_context(worker_leases.scope('drama:plan', session=s)))
            for qid in sorted(set(queue_ids)):
                handles.append(scopes.enter_context(worker_leases.scope(f'publication:{qid}', session=s)))
            token = _owned_handles.set(tuple(handles))
            try: yield
            finally: _owned_handles.reset(token)
    except worker_leases.LeaseLost as exc:
        s.rollback()
        raise HTTPException(409, 'Drama operation is running; inspect its receipt before retrying unchanged') from exc
    except IntegrityError as exc:
        s.rollback()
        raise HTTPException(409, 'Canonical episode, queue identity or operation already has a binding; refresh') from exc
    except BaseException:
        s.rollback()
        raise


def event_hash(s, item):
    return pm.digest([r.event_key for r in s.query(PublicationEvent).filter_by(
        queue_id=item.id, queue_identity=journal.identity(item)).order_by(PublicationEvent.id)])


def mutable_state(item):
    return {k: getattr(item, k).isoformat() if isinstance(getattr(item, k), dt.datetime) else getattr(item, k)
            for k in ('status', 'scheduled_at', 'pinned_at')}


def connected_for_review(s, qid):
    item = s.get(QueueItem, qid, populate_existing=True)
    if item and item.accounts == ['Dramarama:*']:
        from .social.upload_post import list_accounts, UploadPostError
        try:
            return asyncio.run(list_accounts())
        except (UploadPostError, OSError) as exc:
            dc.fail('connected destinations could not be verified; retain the wildcard and retry the read later')
    return None


def review_document(s, item, req, connected=None):
    dc.require_item(s, item, pristine=True)
    dc.expected(s, item, req.expected_fingerprint)
    evidence = dc.binding(s, item)
    if not evidence or evidence.id != req.evidence_id:
        dc.fail('server-inspected canonical episode evidence is required')
    accounts = dc.targets(item.accounts, connected)
    from . import queue_sources
    queue_sources.verify(s, item)  # Keep actual existing Studio/export gates.
    if req.approved_proposal_sha256 != pm.digest({key: getattr(item, key) for key in pm.PUBLIC_FIELDS}):
        dc.fail('approved proposal hash differs from the exact current public copy')
    content = req.model_dump(include=set(ReviewContent.model_fields))
    from . import drama_media
    derivative=drama_media.selected(s,item,content.get('derivative')) if content.get('derivative') else None
    if derivative:
        row=s.query(DramaRecord).filter_by(operation_id=derivative['derivative_id']).one()
        if row.data['document']['prepared_queue_fingerprint']!=req.expected_fingerprint:
            dc.fail('queue state changed since derivative preparation; do not approve a stale source/copy/target artifact')
    transport=derivative['artifact'] if derivative else drama_media.original_observation(s,item,evidence)
    from .drama_payloads import build
    payload=build(item,transport,content)
    if derivative:
        # Older publishers compare their ordinary values-only digest to this
        # field. Salting the reviewed digest makes them fail closed instead of
        # ignoring a derivative and submitting the original after rollback.
        payload={**payload,'values_sha256':payload['sha256'],
            'sha256':pm.digest({'values':payload['values'],'derivative_lineage':drama_media.binding(derivative)})}
    value = {'version': dc.VERSION, 'queue_identity': journal.identity(item), 'evidence_id': evidence.id,
             'copy_hash': dc.copy_hash(item), 'targets': accounts, 'account_selection': list(item.accounts), 'review': content,
             'derivative_lineage':drama_media.binding(derivative),'transport_media':transport,
             'payload':payload}
    return evidence, value, pm.digest(value)


@router.get('/contract')
def contract():
    return {'version': dc.VERSION, 'enabled_by_deployment': False, 'timezone': dc.TZ.key,
        'times': dc.TIMES, 'series_at_slot': ['A', 'B'] * 4, 'daily_total': 8, 'per_series': 4,
        'required_destinations': ['Dramarama:facebook', 'Dramarama:twitter (or connected x alias)'],
        'sequence': 'complete canonical inventory; EP1 upward; both required destinations confirmed before next episode',
        'missed_slots': 'never caught up automatically; new conditional future plan required',
        'rights': 'preserve applicable existing export/media/rights gates; technical evidence and posting authority do not certify rights',
        'cutover': 'quiesce old Drama publishers and unresolved in-flight requests before activation',
        'activation_owner': 'SEO coordinator; this deployment does not apply live queue or schedule changes',
        'one_shot_canary_contract':'/api/drama/canary/contract; separate exact owner authorization/rights and dispatch required',
        'activation': dc.activation_contract()}


@router.get('/plan')
def inspect_plan(s: Session = Depends(get_session)):
    return dc.state(s)


@router.get('/published-media/{platform}')
async def published_media(platform: Literal['facebook','x'], limit: int = Query(default=5,ge=1,le=25)):
    from . import drama_readback
    from .social import upload_post
    try:
        body=await upload_post.published_media('Dramarama',platform,limit)
    except upload_post.UploadPostError as exc:
        raise HTTPException(502,'Native published-media read unavailable; retain the delivery hold') from exc
    return {'read_only':True,'writes':0,'profile':'Dramarama','platform':platform,
        'readback_source':'native platform media list, not submitted upload metadata',
        'media':[drama_readback.native_view(row,platform) for row in body['media']],
        'has_more':(body.get('pagination') or {}).get('has_more'), 'bounded_pages':1,
        'fidelity_verified':False,'activation':dc.activation_contract()}


@router.get('/items/{qid}/delivery-readback')
def delivery_readback(qid: int, s: Session = Depends(get_session)):
    from . import drama_readback
    from .social import upload_post
    item=s.get(QueueItem,qid,populate_existing=True)
    dc.require_item(s,item)
    evidence,ready=dc.readiness(s,item)
    reviewed=dc.payload(s,item)
    results={}
    for event in s.query(PublicationEvent).filter_by(queue_id=qid,queue_identity=journal.identity(item)).order_by(PublicationEvent.id):
        entry=event.payload
        if entry.get('drama_ready_id')!=ready.operation_id or not entry.get('request_id'): continue
        status=entry.get('observed_status') or entry.get('provider_status')
        if not status: continue
        from .social.queue_manager import resolve_results
        resolved=resolve_results([entry],{entry['request_id']:status},dc.now(s))
        if resolved is None: continue
        for account,url in resolved['ok']: results[account]={'provider_request_id':entry['request_id'],'provider_post_url':url}
        for account in resolved['failed']: results.pop(account,None)
    identity=journal.identity(item);proof=dict(evidence.data);ready_id=ready.operation_id
    evidence_id=evidence.id;targets=list(ready.data['targets'])
    s.rollback()  # Native/CDN reads and decode must not hold DB writer ownership.
    observations={}
    for account,receipt in results.items():
        platform=account.partition(':')[2]
        platform='x' if platform=='twitter' else platform
        if account not in targets or platform not in ('facebook','x'): continue
        try:
            observation=asyncio.run(drama_readback.inspect_destination(platform,receipt['provider_post_url'],reviewed,proof))
        except upload_post.UploadPostError:
            observation={'status':'unknown','fidelity_verified':False,'reason':'Native readback unavailable'}
        observations[account]={**receipt,**observation}
    return {'read_only':True,'writes':0,'queue_identity':identity,'canonical_evidence_id':evidence_id,
        'reviewed_ready_id':ready_id,'canonical_source':{key:proof[key] for key in ('series_key','episode','source_video_id','sha256','duration')},
        'observations':observations,'fidelity_verified':False,'activation':dc.activation_contract(),
        'notice':'Partial native observations only. Missing title/media/audio proof holds the lane; no repost or episode advance.'}


@router.get('/cutover-status')
def cutover_status(s: Session = Depends(get_session)):
    """Read-only current + retained-history inventory, never an activation grant."""
    episodes = {e.queue_identity for e in s.query(DramaEpisode)}
    rows = {journal.identity(item): item for item in s.query(QueueItem)
            if item.pipeline == dc.PIPELINE or journal.identity(item) in episodes}
    events = []
    for event in s.query(PublicationEvent).order_by(PublicationEvent.id):
        payload = event.payload
        if (event.queue_identity in rows or event.queue_identity in episodes or payload.get('profile') == 'Dramarama'
                or any(str(a).startswith('Dramarama:') for a in payload.get('accounts', []))):
            events.append(event)
    historical = {e.queue_identity for e in events}
    ids = {item.id for item in rows.values()} | {e.queue_id for e in events}
    current = worker_leases.clock(s)
    live = [lease.resource for lease in s.query(WorkerLease).filter(WorkerLease.expires_at > current)
            if lease.owner and lease.resource in {f'publication:{qid}' for qid in ids}]
    late = [item.id for identity, item in rows.items() if identity in historical and journal.unresolved_late_receipt(s,item)]
    # A cleared mutable field cannot erase a retained transport intent. Report
    # unresolved history conservatively; this endpoint never certifies delivery.
    attempts = {}
    for event in events:
        key = (event.queue_identity, event.attempt_id)
        if event.kind in ('batch_returned','not_posted'):
            attempts.pop(key, None)
        elif event.payload.get('state'):
            attempts[key] = event.payload['state']
    unresolved = sum(state in ('intent','unknown','accepted') for state in attempts.values())
    return {'read_only': True, 'writes': 0, 'current_rows': len(rows),
        'current_posting_rows': sum(item.status == 'posting' for item in rows.values()),
        'current_request_rows': sum(bool(item.publish_requests) for item in rows.values()),
        'live_publication_leases': len(live), 'late_receipt_rows': len(late),
        'retained_events': len(events), 'retained_unresolved_attempts': unresolved,
        'historical_identities_without_current_row': len(historical - rows.keys()),
        'history_attribution_unverified': len(historical - episodes),
        'wildcard_rows': sum(item.accounts == ['Dramarama:*'] for item in rows.values()),
        'notice': 'Inventory only; absence of mutable requests is not proof of delivery or a release certificate. Reconcile retained uncertainty and stop old publishers before activation.',
        'activation': dc.activation_contract()}


@router.get('/receipts/{operation_id}')
def receipt(operation_id: str, s: Session = Depends(get_session)):
    row = s.query(DramaRecord).filter_by(operation_id=operation_id).first()
    if not row:
        raise HTTPException(404, 'No committed Drama operation receipt')
    return {'operation_id': operation_id, 'kind': row.kind, 'queue_id': row.queue_id,
            'queue_identity': row.queue_identity, 'request_hash': row.request_hash,
            'created_at': row.created_at, 'historical_snapshot_only': True, **row.data}


@router.get('/items/{qid}')
def inspect_item(qid: int, s: Session = Depends(get_session)):
    item = s.get(QueueItem, qid, populate_existing=True)
    dc.require_item(s, item)
    evidence = dc.binding(s, item)
    return {**pm.view(s, item), 'copy_hash': dc.copy_hash(item),
            'evidence': {'id': evidence.id, **evidence.data} if evidence else None,
            'review_id': (review.operation_id if (review := dc.latest(s, item, 'review')) else None),
            'ready_id': (ready.operation_id if (ready := dc.latest(s, item, 'ready')) else None),
            'plan_fingerprint': dc.state(s)['fingerprint']}


@router.post('/items/{qid}/inspect')
def inspect_archive(qid: int, req: Inspect, s: Session = Depends(get_session)):
    hashed = request_hash('inspect', req, qid)
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    with owned(s, [qid]):
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        item = s.get(QueueItem, qid, populate_existing=True)
        dc.require_item(s, item, pristine=True); dc.expected(s, item, req.expected_fingerprint)
        frozen = SimpleNamespace(**{c.key: getattr(item, c.key) for c in QueueItem.__table__.columns})
        s.rollback()  # No DB transaction is held during media download/decode.
        evidence = drama_archive.inspect(frozen, req.manifest_id, req.source_video_id)
        lock_owned(s)
        item = s.get(QueueItem, qid, populate_existing=True)
        dc.require_item(s, item, pristine=True); dc.expected(s, item, req.expected_fingerprint)
        dc.require_source_history(s, item, evidence)
        existing = dc.binding(s, item)
        if existing:
            for key in ('series_key', 'episode', 'catalog_hash', 'source_video_id', 'drive_file_id', 'sha256', 'file_size'):
                if existing.data[key] != evidence[key]:
                    dc.fail('an immutable canonical binding already exists with different evidence')
            bound = existing
        else:
            bound = DramaEpisode(id=str(uuid4()), queue_id=qid, queue_identity=journal.identity(item),
                queue_created_at=item.created_at, series_key=evidence['series_key'], episode=evidence['episode'],
                total_episodes=evidence['total_episodes'], data=evidence)
            s.add(bound); s.flush()
        response = dc.record(s, req.operation_id, hashed, 'inspect', {'evidence_id': bound.id,
            'fingerprint': pm.fingerprint(s, item), 'evidence': bound.data}, item=item,live_media_observation=evidence)
        s.commit(); return response


@router.post('/items/{qid}/review-preview')
def preview_review(qid: int, req: ReviewPreview, s: Session = Depends(get_session)):
    connected = connected_for_review(s, qid)
    item = s.get(QueueItem, qid, populate_existing=True)
    evidence, document, fingerprint = review_document(s, item, req, connected)
    return {'acknowledged_fingerprint': fingerprint, 'document': document,
            'server_media_evidence': evidence.data, 'public_copy': {k: getattr(item, k) for k in pm.PUBLIC_FIELDS},
            'rights_status': 'technical evidence does not certify rights; existing source/export gates remain applicable'}


@router.post('/items/{qid}/review')
def accept_review(qid: int, req: Review, s: Session = Depends(get_session)):
    hashed = request_hash('review', req, qid)
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    connected = connected_for_review(s, qid)  # Provider read precedes lease/mutex writes.
    with owned(s, [qid]):
        lock_owned(s)
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        item = s.get(QueueItem, qid, populate_existing=True)
        evidence, document, fingerprint = review_document(s, item, req, connected)
        if fingerprint != req.acknowledged_fingerprint:
            dc.fail('exact media/copy/review acknowledgement changed; inspect the preview again')
        if document['payload']['errors']:
            dc.fail('; '.join(document['payload']['errors']))
        response = dc.record(s, req.operation_id, hashed, 'review', {'review_id': req.operation_id,
            'fingerprint': pm.fingerprint(s, item), 'review_fingerprint': fingerprint}, item=item,
            evidence_id=evidence.id, copy_hash=dc.copy_hash(item), targets=document['targets'],
            document=document, rights_status='not certified by this technical/copy receipt; existing source/export gates preserved')
        s.commit(); return response


@router.post('/items/{qid}/ready')
def mark_ready(qid: int, req: Ready, s: Session = Depends(get_session)):
    hashed = request_hash('ready', req, qid)
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    with owned(s, [qid]):
        lock_owned(s)
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        item = s.get(QueueItem, qid, populate_existing=True)
        dc.require_item(s, item, pristine=True); dc.expected(s, item, req.expected_fingerprint)
        evidence, review = dc.require_review(s, item, req.evidence_id, req.review_id)
        if list(item.accounts) != review.data['document']['account_selection']:
            dc.fail('destination selection changed after exact copy review')
        frozen = SimpleNamespace(**{c.key: getattr(item, c.key) for c in QueueItem.__table__.columns})
        proof = dict(evidence.data)
        s.rollback()
        observed = drama_archive.inspect(frozen, proof['manifest_id'], proof['source_video_id'])
        from . import drama_media
        derivative=drama_media.selected(s,frozen)
        if derivative and drama_media.inspect(derivative['path'])!=derivative['artifact']:
            dc.fail('derivative changed since actual server media inspection/review')
        lock_owned(s)
        item = s.get(QueueItem, qid, populate_existing=True)
        dc.require_item(s, item, pristine=True); dc.expected(s, item, req.expected_fingerprint)
        evidence, review = dc.require_review(s, item, req.evidence_id, req.review_id)
        if list(item.accounts) != review.data['document']['account_selection']:
            dc.fail('destination selection changed during live media verification')
        for key in ('series_key', 'episode', 'catalog_hash', 'source_video_id', 'drive_file_id', 'sha256', 'file_size'):
            if observed[key] != evidence.data[key]:
                dc.fail('live canonical media differs from the inspected/reviewed evidence')
        dc.require_source_history(s, item, observed)
        from . import queue_sources
        queue_sources.verify(s, item)
        dc.predecessors(s, evidence)
        before = mutable_state(item)
        dc.conditional_update(s, item, status='ready', scheduled_at=None, pinned_at=None)
        response = dc.record(s, req.operation_id, hashed, 'ready', {'ready_id': req.operation_id,
            'fingerprint': pm.fingerprint(s, item)}, item=item, evidence_id=evidence.id,
            review_id=review.operation_id, copy_hash=dc.copy_hash(item), targets=review.data['targets'],
            initial_accounts=list(item.accounts),
            live_media_observation=observed, live_observed_at=dc.now(s).isoformat(),
            undo={'items': [{'id': qid, 'identity': journal.identity(item), 'before': before,
                'after_fingerprint': pm.fingerprint(s, item), 'events': event_hash(s, item)}]})
        s.commit(); return response


@router.post('/plan-preview')
def preview_plan(req: Plan, s: Session = Depends(get_session)):
    current = dc.state(s)
    if current['fingerprint'] != req.expected_plan_fingerprint:
        dc.fail('active plan changed before preview')
    items = []
    for selected in req.items:
        item = s.get(QueueItem, selected.id, populate_existing=True)
        dc.require_item(s, item); dc.expected(s, item, selected.expected_fingerprint)
        items.append(item)
    plan = dc.prepare_plan(s, req.operation_id, req.series, items)
    return {'preview_only': True, 'writes': 0, 'plan': plan, 'activation': dc.activation_contract(),
            'notice': 'Not an applied plan or release certificate; future eligibility must be rechecked on application'}


@router.post('/plan')
def apply_plan(req: Plan, s: Session = Depends(get_session)):
    hashed = request_hash('plan', req)
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    dc.require_activation_capability()
    # A plan replacement must also fence every previous assignment, including
    # one that is currently dispatching and was omitted from the new selection.
    previous_ids = [int(qid) for qid in (dc.state(s)['plan'] or {}).get('assignments', {})]
    with owned(s, [i.id for i in req.items] + previous_ids, plan=True):
        lock_owned(s)
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        current = dc.state(s)
        if current['fingerprint'] != req.expected_plan_fingerprint:
            dc.fail('active Drama plan changed; refresh without replanning other brands')
        items, undo_items = [], []
        for selected in req.items:
            item = s.get(QueueItem, selected.id, populate_existing=True)
            dc.require_item(s, item); dc.expected(s, item, selected.expected_fingerprint)
            items.append(item)
            undo_items.append({'id': item.id, 'identity': journal.identity(item), 'before': mutable_state(item),
                               'events': event_hash(s, item)})
        plan = dc.prepare_plan(s, req.operation_id, req.series, items)
        for item, old in zip(items, undo_items):
            at = dt.datetime.fromisoformat(plan['assignments'][str(item.id)]['at'])
            dc.conditional_update(s, item, status='ready', scheduled_at=at, pinned_at=None)
            old['after_fingerprint'] = pm.fingerprint(s, item)
        setting = s.get(AppSetting, dc.PLAN_KEY)
        if setting: setting.value = plan
        else: s.add(AppSetting(key=dc.PLAN_KEY, value=plan))
        response = dc.record(s, req.operation_id, hashed, 'plan', {'plan': plan,
            'plan_fingerprint': pm.digest(plan), 'items': [{i.id: pm.fingerprint(s, i)} for i in items]},
            plan=plan, undo={'items': undo_items, 'plan_before': current['plan'], 'plan_after': pm.digest(plan)})
        s.commit(); return response


@router.post('/receipts/{target_id}/rollback')
def rollback(target_id: str, req: Rollback, s: Session = Depends(get_session)):
    hashed = pm.digest({'kind': 'rollback', 'target': target_id, 'request': req.model_dump(exclude={'operation_id'})})
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    target = s.query(DramaRecord).filter_by(operation_id=target_id).first()
    if not target or target.kind not in ('ready', 'plan'):
        raise HTTPException(409, 'Only guarded readiness or plan mutations have a supported conditional rollback')
    old = target.data['undo']
    with owned(s, [v['id'] for v in old['items']], plan=True):
        lock_owned(s)
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        current = dc.state(s)
        if current['fingerprint'] != req.expected_plan_fingerprint:
            dc.fail('Drama plan changed before rollback')
        if target.kind == 'plan' and current['fingerprint'] != old['plan_after']:
            dc.fail('another plan superseded this receipt')
        if target.kind == 'ready' and str(target.queue_id) in (current['plan'] or {}).get('assignments', {}):
            dc.fail('roll back its schedule plan before reversing readiness')
        rows = []
        for saved in old['items']:
            item = s.get(QueueItem, saved['id'], populate_existing=True)
            if (not item or journal.identity(item) != saved['identity']
                    or pm.fingerprint(s, item) != saved['after_fingerprint'] or event_hash(s, item) != saved['events']
                    or item.status not in ('ready', 'retry') or journal.unresolved_late_receipt(s, item)):
                dc.fail('queue/publication state changed; preserve it instead of restoring an old snapshot')
            values = dict(saved['before'])
            for field in ('scheduled_at', 'pinned_at'):
                if values[field]:
                    values[field] = dt.datetime.fromisoformat(values[field])
                    if dc.aware(values[field]) <= dc.now(s):
                        dc.fail('rollback would restore a past slot; use a new future plan')
            rows.append((item, values))
        for item, values in rows:
            dc.conditional_update(s, item, **values)
        if target.kind == 'plan':
            setting = s.get(AppSetting, dc.PLAN_KEY)
            setting.value = old['plan_before']
        response = dc.record(s, req.operation_id, hashed, 'rollback',
            {'rolled_back': target_id, 'plan_fingerprint': pm.digest(old['plan_before']) if target.kind == 'plan' else current['fingerprint'],
             'items': [{i.id: pm.fingerprint(s, i)} for i, _ in rows]})
        s.commit(); return response
