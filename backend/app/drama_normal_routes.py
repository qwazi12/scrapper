"""Conditional normal-plan/permission operations; never an episode dispatch API."""
import asyncio
import datetime as dt
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from . import drama_controls as dc, drama_normal as normal, pending_metadata as pm, publication_journal as journal
from .auth import require_token
from .db import get_session
from .drama_routes import Operation, ItemOperation, Selected, owned, lock_owned
from .models import AppSetting, DramaEpisode, DramaRecord, QueueItem
from .social import upload_post

router = APIRouter(prefix='/api/drama/normal', dependencies=[Depends(require_token)])


class Targets(Operation):
    facebook_page_id: str = Field(pattern=r'^\d{5,30}$')
    x_account_id: str = Field(pattern=r'^\d{5,30}$')
    x_handle: str = Field(pattern=r'^[A-Za-z0-9_]{1,15}$')


class Permission(Targets):
    queue_id: int = Field(gt=0)
    expected_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    evidence_id: str = Field(pattern=pm.OPERATION_PATTERN)
    expected_permission_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    permission_reference: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    authorization_provenance: str = Field(min_length=1, max_length=2000, pattern=r'\S')
    decision: Literal['Record the existing user permission for this exact canonical series on the frozen DramaRama Facebook and X destinations only']


class Revoke(Operation):
    series_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_permission_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    rights_id: str = Field(pattern=pm.OPERATION_PATTERN)
    reason: str = Field(min_length=1, max_length=2000)


class Plan(Targets):
    expected_plan_fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    lanes: list[str | None] = Field(min_length=2, max_length=2)
    items: list[Selected] = Field(min_length=1, max_length=500)


class ApplyPlan(Plan):
    acknowledged_preview_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class VerifyDelivery(ItemOperation):
    ready_id: str = Field(pattern=pm.OPERATION_PATTERN)


def identity(req):
    value = normal.target_identity(asyncio.run(upload_post.canary_target_identity(req.facebook_page_id)))
    if value['x_account_id'] != req.x_account_id or value['x_handle'] != req.x_handle:
        dc.fail('fresh native account differs from the explicitly authorized Page/X identity')
    return value


def digest(kind, req):
    return pm.digest({'version': normal.VERSION, 'kind': kind, 'request': req.model_dump(exclude={'operation_id'})})


@router.get('/contract')
def contract():
    from . import drama_native
    return {'version': normal.VERSION, 'implementation_supported': True, 'enabled_by_deployment': False,
        'times': list(dc.TIMES), 'timezone': dc.TZ.key, 'lane_at_slot': ['A', 'B'] * 4,
        'vacant_lane': None, 'vacant_lane_refill': False, 'daily_maximum': 8, 'daily_per_series': 4,
        'single_approved_lane_daily_maximum': 4, 'catchup': False,
        'rights': 'Explicit series/catalog/native-destination permission reference recorded under existing user authority; no implicit rights from names or another series',
        'first_episode': 'Reviewed compatible source/lineage, permission, native target and future plan checks; no predecessor delivery proof exists for EP1',
        'successor': 'Both predecessor destinations need independently observed delivered media/copy proof; acceptance is insufficient. Immutable verified publication history has no daily expiry or automatic native rereads; later contradiction, identity revision or held observation requires reconciliation. Current series permission and exact targets remain required.',
        'native_read_capability': drama_native.capability(),
        'delivery': {'Facebook': 'native caption and playable media observed; separate title/author fields can be missing',
            'X': 'current supported media-list returns preview image only, not playable video/duration/audio; item-specific hold, no invented proof'},
        'permission_records': 'authenticated coordinator records existing permission, not a demand for another manual human approval round',
        'live_writer': 'SEO coordinator only; no automatic selection or plan on deploy'}


@router.get('/permissions/{series_key}')
def inspect_permission(series_key: str, s: Session = Depends(get_session)):
    row = normal.rights(s, series_key)
    return {'fingerprint': normal.permission_state(s, series_key),
        'rights_id': row.operation_id if row else None, 'document': row.data['document'] if row else None,
        'historical_statement_not_independent_rights_certification': True}


@router.post('/permissions')
def record_permission(req: Permission, s: Session = Depends(get_session)):
    hashed = digest('normal_rights', req)
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    native = identity(req)
    initial=s.get(QueueItem,req.queue_id,populate_existing=True)
    evidence=dc.binding(s,initial) if initial else None
    qids={r.queue_id for r in s.query(DramaEpisode).filter_by(series_key=evidence.series_key)} if evidence else set()
    with owned(s, qids|{req.queue_id}, plan=True):
        lock_owned(s)
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        item = s.get(QueueItem, req.queue_id, populate_existing=True)
        dc.require_item(s, item); dc.expected(s, item, req.expected_fingerprint)
        evidence = dc.binding(s, item)
        if not evidence or evidence.id != req.evidence_id:
            dc.fail('actual canonical series evidence is required before recording its permission')
        if req.expected_permission_fingerprint != normal.permission_state(s, evidence.series_key):
            dc.fail('series permission/revocation changed; refresh before recording authority')
        if not req.permission_reference.strip() or not req.authorization_provenance.strip():
            dc.fail('actual existing user permission reference and truthful provenance required')
        document = {'version': normal.RIGHTS_VERSION, 'series_key': evidence.series_key,
            'series_id': evidence.data['series_id'], 'season': evidence.data['season'],
            'catalog_hash': evidence.data['catalog_hash'],
            'destinations': ['Dramarama:facebook', 'Dramarama:x'], 'target_identity': native,
            'permission_reference': req.permission_reference, 'authorization_provenance': req.authorization_provenance,
            'decision': req.decision, 'recorded_as': 'Authenticated agent/coordinator recording existing user authority; no manual-owner-review or independently verified licence claim'}
        result = dc.record(s, req.operation_id, hashed, 'normal_rights',
            {'rights_id': req.operation_id, 'series_key': evidence.series_key, 'queue_changed': False,
             'plan_activated': False, 'permission_document_sha256': pm.digest(document)}, item=item, document=document)
        s.commit(); return result


@router.post('/permissions/revoke')
def revoke_permission(req: Revoke, s: Session = Depends(get_session)):
    hashed = digest('normal_rights_revoke', req)
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    qids={r.queue_id for r in s.query(DramaEpisode).filter_by(series_key=req.series_key)}
    with owned(s, qids, plan=True):
        lock_owned(s)
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        row = normal.rights(s, req.series_key)
        if (not row or row.operation_id != req.rights_id or not req.reason.strip()
                or normal.permission_state(s, req.series_key) != req.expected_permission_fingerprint):
            dc.fail('exact current permission/revision and actual reason required')
        result = dc.record(s, req.operation_id, hashed, 'normal_rights_revoke',
            {'revoked_rights_id': row.operation_id, 'queue_changed': False}, rights_id=row.operation_id,
            series_key=req.series_key, reason=req.reason)
        s.commit(); return result


def preview_document(s, req, native):
    if dc.state(s)['fingerprint'] != req.expected_plan_fingerprint:
        dc.fail('active plan changed; refresh rather than overwrite it')
    items = []
    for selected in req.items:
        item = s.get(QueueItem, selected.id, populate_existing=True)
        dc.require_item(s, item); dc.expected(s, item, selected.expected_fingerprint)
        items.append(item)
    return items, normal.prepare_plan(s, req.operation_id, req.lanes, items, native)


@router.post('/plan-preview')
def preview(req: Plan, s: Session = Depends(get_session)):
    native = identity(req)
    _, document = preview_document(s, req, native)
    return {'plan': document, 'preview_sha256': pm.digest(document), 'writes': 0,
        'notice': 'Conditional future plan preview; not an applied release or a delivery certificate'}


@router.post('/plan')
def apply(req: ApplyPlan, s: Session = Depends(get_session)):
    hashed = digest('normal_plan', req)
    if saved := dc.replay(s, req.operation_id, hashed):
        return saved
    native = identity(req)
    previous = dc.state(s)['plan']
    qids = {item.id for item in req.items} | {int(i) for i in (previous or {}).get('assignments', {})}
    with owned(s, qids, plan=True):
        lock_owned(s)
        if saved := dc.replay(s, req.operation_id, hashed):
            s.rollback(); return saved
        before = dc.state(s)
        items, plan = preview_document(s, req, native)
        if pm.digest(plan) != req.acknowledged_preview_sha256:
            dc.fail('permission/source/native identity/slot/plan changed; acknowledge a fresh preview')
        for item in items:
            assignment = plan['assignments'][str(item.id)]
            dc.conditional_update(s, item, scheduled_at=dt.datetime.fromisoformat(assignment['at']), pinned_at=None)
            # Retain rollback fence identity even when a later plan/old runtime
            # removes this item or changes its pipeline. Not a dispatch grant.
            dc.record(s, str(uuid4()), pm.digest(assignment), 'normal_guard', {}, item=item,
                created_at_identity=item.created_at.isoformat(), plan_id=req.operation_id)
        setting = s.get(AppSetting, dc.PLAN_KEY)
        if setting:
            setting.value = plan
        else:
            s.add(AppSetting(key=dc.PLAN_KEY, value=plan))
        response = dc.record(s, req.operation_id, hashed, 'plan',
            {'plan': plan, 'plan_fingerprint': pm.digest(plan), 'publication_dispatched': False}, plan=plan,
            previous_plan_fingerprint=before['fingerprint'])
        s.commit(); return response


@router.get('/items/{qid}/delivery')
def delivery_state(qid:int,s:Session=Depends(get_session)):
    from . import drama_delivery as delivery,drama_native
    item=s.get(QueueItem,qid,populate_existing=True);dc.require_item(s,item)
    _,ready=dc.readiness(s,item);row=dc.latest(s,item,'normal_delivery')
    return {'read_only':True,'writes':0,'queue_identity':journal.identity(item),
        'delivery_verified':delivery.verified(s,item,ready),
        'observation':row.data['document'] if row else None,
        'missing':[] if row else ['no server-observed normal delivery record'],
        'native_read_capability':drama_native.capability()}


@router.post('/items/{qid}/verify-delivery')
def verify_delivery(qid:int,req:VerifyDelivery,s:Session=Depends(get_session)):
    from . import drama_delivery as delivery,drama_media as dm,drama_archive,drive_sync,drama_observed_media
    from .file_use import pin
    from pathlib import Path
    hashed=pm.digest({'version':normal.VERSION,'kind':'normal_delivery','qid':qid,
        'request':req.model_dump(exclude={'operation_id'})})
    if saved:=dc.replay(s,req.operation_id,hashed):return saved
    with owned(s,[qid]):
        if saved:=dc.replay(s,req.operation_id,hashed):s.rollback();return saved
        item=s.get(QueueItem,qid,populate_existing=True);dc.require_item(s,item)
        dc.expected(s,item,req.expected_fingerprint);evidence,ready=dc.readiness(s,item)
        if ready.operation_id!=req.ready_id:dc.fail('Ready changed before native delivery observation')
        if journal.unresolved_late_receipt(s,item):dc.fail('uncertain retained receipts must be reconciled without reposting')
        frozen=delivery.binding(s,item,ready);known=delivery.requests_for(s,item,ready)
        requests_sha=pm.digest(known);selected=dm.selected(s,item)
        proof=dict(evidence.data);temporary=None
        path=selected['path'] if selected else None
        target=frozen['target_identity'];payload=dc.payload(s,item)
        s.rollback()
        try:
            if known:
                actual=normal.target_identity(asyncio.run(upload_post.canary_target_identity(target['facebook_page_id'])))
                if actual!=target:dc.fail('native Page/X connection changed before verification')
                drama_archive.verify_dispatch(proof)
                if path is None:temporary=path=drive_sync.download_drive_file(proof['drive_file_id'])
                with pin([path],session=s):
                    if dm.file_hash(path)!=frozen['transport']['artifact_sha256']:dc.fail('actual reviewed artifact changed before native comparison')
                    observations=asyncio.run(delivery.collect(known,target,payload,path))
                    if dm.file_hash(path)!=frozen['transport']['artifact_sha256']:dc.fail('actual reviewed artifact changed during native comparison')
                actual=normal.target_identity(asyncio.run(upload_post.canary_target_identity(target['facebook_page_id'])))
                if actual!=target:dc.fail('native Page/X connection changed during verification')
                drama_archive.verify_dispatch(proof)
            else:
                observations={account:{'state':'held','verified':False,
                    'missing':['no retained normal destination request; no live post/read invented']}
                    for account in frozen['targets']}
            s.rollback();lock_owned(s)
            item=s.get(QueueItem,qid,populate_existing=True);dc.expected(s,item,req.expected_fingerprint)
            _,ready=dc.readiness(s,item)
            if (delivery.binding(s,item,ready)!=frozen
                    or pm.digest(delivery.requests_for(s,item,ready))!=requests_sha):
                dc.fail('rights/source/review/requests changed during observation; no stale proof adopted')
            document={'version':delivery.VERSION,'binding':frozen,'observations':observations,
                'observed_at':dc.now(s).isoformat(),'policy_sha256':pm.digest(drama_observed_media.POLICY),
                'native_read_settings_unchanged':True,'publication_dispatched':False}
            complete=delivery.verify_document(document,frozen)
            for observation in observations.values():
                rid=observation.get('provider_request_id')
                if rid in known and observation.get('provider_status'):
                    journal.record(s,item,{**known[rid]['entry'],'observed_status':observation['provider_status']},'provider_observation')
            if complete:
                dc.conditional_update(s,item,status='archived',published_at=dc.now(s),
                    notes='Both frozen DramaRama destinations independently observed; bounded media/copy checks passed. No automatic next-episode dispatch.')
            result=dc.record(s,req.operation_id,hashed,'normal_delivery',
                {'delivery_verified':complete,'destination_verified':{a:o.get('verified',False) for a,o in observations.items()},
                 'queue_changed':complete,'fingerprint':pm.fingerprint(s,item),'successor_eligible_claim':False},item=item,document=document)
            s.commit();return result
        finally:
            if temporary:Path(temporary).unlink(missing_ok=True)
