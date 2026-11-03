"""One exact EP1, one durable dispatch, at most one request per reviewed target.

Grant/dispatch are separate explicit owner-token operations. No environment flag,
generic Ready, scheduler tick or success URL turns on the normal Drama plan.
Owner authorization/rights statements are recorded as provided, never certified
by an AI or inferred from rollout approval. Missing delivered fidelity stays held.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import time
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy.orm import Session

from . import drama_controls as dc, pending_metadata as pm, publication_journal as journal
from .models import DramaEpisode, DramaRecord, PublicationEvent
from .social import upload_post

VERSION='drama-ep1-canary-v1'
SCOPE='drama-single-reviewed-ep1-canary-v1'
_current=contextvars.ContextVar('drama_canary_dispatch',default=None)


@dataclass
class Dispatch:
    operation_id: str
    request_hash: str
    grant_id: str
    queue_id: int
    expected_fingerprint: str
    grant_sha256: str
    target_identity: dict
    checked_monotonic: float


def current(): return _current.get()


def transport_state(item):
    # Only status/current transport receipts are changed by this publisher
    # between its atomic claim and individual requests. Everything else stays
    # bound to the owner's exact baseline, including notes/media/targets/slots.
    return pm.digest({column.key:getattr(item,column.key) for column in item.__table__.columns
                      if column.key not in ('status','publish_requests')})


@contextlib.contextmanager
def dispatch_scope(value):
    token=_current.set(value)
    try: yield
    finally: _current.reset(token)


def grants(s):
    return s.query(DramaRecord).filter(DramaRecord.kind=='canary_grant',
        DramaRecord.data['canary_scope'].as_string()==SCOPE).order_by(DramaRecord.id).all()


def grant(s,operation_id):
    row=s.query(DramaRecord).filter_by(operation_id=operation_id,kind='canary_grant').first()
    if not row or row.data.get('canary_scope')!=SCOPE: dc.fail('canary grant not found in this server scope')
    return row


def revoked(s,row):
    return bool(s.query(DramaRecord.id).filter(DramaRecord.kind=='canary_revoke',
        DramaRecord.data['grant_id'].as_string()==row.operation_id).first())


def consumed(s,row):
    return s.query(DramaRecord).filter(DramaRecord.kind=='canary_dispatch',
        DramaRecord.data['grant_id'].as_string()==row.operation_id).first()


def prepared(s,item,req,target_identity,*,require_authorization=False):
    """Pure exact preview under current state; grant writer repeats under mutex."""
    dc.require_item(s,item)
    dc.expected(s,item,req.expected_fingerprint)
    pm.require_pending(s,item)
    if item.status!='ready' or item.scheduled_at or item.pinned_at or item.post_next_at:
        dc.fail('canary needs guarded Ready with no ordinary schedule/pin/Post Next intent')
    if item.thumb_path or item.media_files or item.media_url:
        dc.fail('this one-shot contract covers canonical video and reviewed copy only; no unbound additional thumbnail/media asset')
    evidence,ready=dc.readiness(s,item)
    review=dc.latest(s,item,'review')
    if (evidence.id!=req.evidence_id or ready.operation_id!=req.ready_id or review.operation_id!=req.review_id
            or evidence.episode!=1 or len(req.series)!=2 or len(set(req.series))!=2
            or req.series[0]!=evidence.series_key):
        dc.fail('canary must be the exact reviewed EP1 of the first of the two approved canonical series')
    if not s.query(DramaEpisode.id).filter_by(series_key=req.series[1],episode=1).first():
        dc.fail('the second approved canonical series needs its own EP1 archive evidence')
    if ready.data['targets']!=['Dramarama:facebook','Dramarama:x'] or dc.dispatch_targets(s,item)!=ready.data['targets']:
        dc.fail('canary requires exactly the reviewed Facebook/X pair; no wildcard expansion or additional destination')
    if dc.state(s)['plan'] is not None:
        dc.fail('canary cannot coexist with or activate an ordinary Drama plan')
    dc.require_source_history(s,item,evidence.data)
    payload=dc.payload(s,item)
    if payload['facebook_media_type']!='VIDEO': dc.fail('canary requires the explicit full-length Facebook VIDEO path')
    instant=dc.now(s);slot=dc.aware(req.slot)
    local=slot.astimezone(dc.TZ)
    if (slot<=instant+dt.timedelta(minutes=1) or slot>instant+dt.timedelta(days=2)
            or local.time().replace(tzinfo=None)!=dt.time(10,0)):
        dc.fail('choose an explicitly authorized future A/EP1 10:00 New York slot within the next48hours')
    if (target_identity['facebook_page_id']!=req.targets.facebook_page_id
            or target_identity['x_account_id']!=req.targets.x_account_id
            or target_identity['x_handle']!=req.targets.x_handle):
        dc.fail('requested native Page/X identity differs from the fresh provider read')
    if require_authorization and req.authorization is None:
        dc.fail('specific public-canary authorization and rights confirmation are absent')
    if req.authorization and req.authorization.authorized_public_payload_sha256!=pm.digest(payload):
        dc.fail('specific public-canary authorization must bind this exact visible platform payload')
    if req.authorization and req.authorization.authorized_source_sha256!=evidence.data['sha256']:
        dc.fail('specific public-canary rights/authorization must bind this exact original source checksum')
    canonical={key:evidence.data[key] for key in ('series_key','series_id','season','episode','source_video_id',
        'canonical_video_id','drive_file_id','sha256','file_size','duration','catalog_hash')}
    document={'version':VERSION,'canary_scope':SCOPE,'queue_id':item.id,'queue_identity':journal.identity(item),
        'queue_fingerprint':pm.fingerprint(s,item),'evidence_id':evidence.id,'review_id':review.operation_id,
        'transport_state_sha256':transport_state(item),
        'ready_id':ready.operation_id,'copy_hash':dc.copy_hash(item),'series':req.series,'canonical':canonical,
        'target_identity':target_identity,'targets':ready.data['targets'],'payload':payload,'payload_sha256':pm.digest(payload),
        'slot':slot.isoformat(),'expires_at':(slot+dt.timedelta(minutes=5)).isoformat(),
        'slot_local':local.isoformat(),'timezone':dc.TZ.key,
        'authorization':req.authorization.model_dump() if req.authorization else None,
        'authorization_complete':req.authorization is not None,
        'authorization_status':('Owner statement/reference recorded as supplied; not independently verified rights or delegated agent approval'
            if req.authorization else 'No owner authorization or rights statement supplied; preview only'),
        'active_plan_fingerprint':dc.state(s)['fingerprint'],'automatic_plan_activated':False,
        'public_posts_maximum':2,'episode_limit':1,'next_episode_authorized':False}
    previous=grants(s)
    if previous:
        first=previous[0].data['document']
        frozen=('queue_identity','series','canonical')
        if any(document[key]!=first[key] for key in frozen) or any(
                target_identity[key]!=first['target_identity'][key] for key in ('facebook_page_id','x_account_id','facebook_connection_id')):
            dc.fail('this server canary scope is already bound to another source/series/native destination; no additional canary')
        if any(consumed(s,row) or not revoked(s,row) for row in previous):
            dc.fail('an existing canary grant is active or consumed; inspect it instead of granting another')
    return document


def validate(s,item,*,final=False,media_sha256=None):
    value=current()
    if not value or value.queue_id!=item.id: dc.fail('an explicit dedicated canary dispatch operation is required')
    row=grant(s,value.grant_id);doc=row.data['document']
    if pm.digest(doc)!=value.grant_sha256 or revoked(s,row): dc.fail('canary grant changed or was revoked')
    if consumed(s,row): dc.fail('canary grant already has a durable dispatch; replay its operation or inspect publication evidence without resending')
    if journal.identity(item)!=doc['queue_identity'] or pm.fingerprint(s,item)!=doc['queue_fingerprint']:
        dc.fail('canary row/media/copy/target/protected state changed after specific authorization')
    if value.expected_fingerprint!=doc['queue_fingerprint']: dc.fail('dispatch approval fingerprint differs from its grant')
    evidence,ready=dc.readiness(s,item)
    if evidence.id!=doc['evidence_id'] or ready.operation_id!=doc['ready_id'] or evidence.episode!=1:
        dc.fail('canonical episode or guarded review differs from the canary grant')
    if (dc.state(s)['plan'] is not None or dc.state(s)['fingerprint']!=doc['active_plan_fingerprint']
            or dc.payload(s,item)!=doc['payload'] or dc.dispatch_targets(s,item)!=doc['targets']):
        dc.fail('canary cannot inherit a changed plan, copy or destination')
    instant=dc.now(s);slot=dt.datetime.fromisoformat(doc['slot'])
    if not slot<=instant<dt.datetime.fromisoformat(doc['expires_at']):
        dc.fail('canary slot is future or expired; no catch-up or automatic renewal')
    if value.target_identity!=doc['target_identity'] or time.monotonic()-value.checked_monotonic>30:
        dc.fail('native Page/X identity is stale or changed before canary dispatch')
    pm.require_pending(s,item)
    if item.status!='ready': dc.fail('canary source must remain the exact guarded Ready state')
    dc.require_source_history(s,item,evidence.data)
    if s.query(PublicationEvent.id).filter(PublicationEvent.kind=='batch_intent',
            PublicationEvent.payload['drama_slot'].as_string()==doc['slot']).first():
        dc.fail('the authorized canary slot was already consumed')
    if len(dc.attempted_today(s,None,instant))>=8 or len(dc.attempted_today(s,evidence.series_key,instant))>=4:
        dc.fail('canary cannot exceed the existing Drama episode quota')
    if final and media_sha256!=doc['canonical']['sha256']: dc.fail('canary transport bytes differ from the specifically authorized original')
    return {'drama_ready_id':ready.operation_id,'drama_canary_grant_id':row.operation_id,
        'drama_canary_operation_id':value.operation_id,'drama_series_key':evidence.series_key,
        'drama_episode':1,'drama_slot':doc['slot']}


async def refresh_target_identity():
    value=current()
    if value:
        fresh=await upload_post.canary_target_identity(value.target_identity['facebook_page_id'])
        if fresh!=value.target_identity:
            dc.fail('native Facebook Page/pin or X account changed during canary preparation')
        value.target_identity=fresh;value.checked_monotonic=time.monotonic()


def request_keys(s,item):
    value=current()
    if not value:return None
    doc=grant(s,value.grant_id).data['document']
    return {account:uuid5(UUID(value.operation_id),pm.digest([account,doc['canonical']['sha256'],doc['payload_sha256'],doc['target_identity']])).hex
            for account in doc['targets']}


def payload(s,item,base):
    value=current()
    if not value:return base
    doc=grant(s,value.grant_id).data['document']
    return {**base,'facebook_page_id':doc['target_identity']['facebook_page_id']}


def claim(s,item,batch_key,artifact_key):
    value=current()
    if not value:return
    row=grant(s,value.grant_id)
    if consumed(s,row) or revoked(s,row): dc.fail('canary dispatch already claimed or revoked')
    response={'dispatch_id':value.operation_id,'grant_id':row.operation_id,'attempt_id':batch_key,
        'state':'durable_intent_claimed','transport_acceptance':'unknown_until_original_publication_receipts',
        'publication_evidence_path':f'/api/queue/{item.id}/publication-evidence','fidelity_verified':False,
        'automatic_plan_activated':False,'resend_authorized':False}
    dc.record(s,value.operation_id,value.request_hash,'canary_dispatch',response,item=item,
        grant_id=row.operation_id,canary_scope=SCOPE,artifact_key=artifact_key,request_keys=request_keys(s,item),
        document=row.data['document'])


def guard_destination_intent(s,item,entry):
    value=current()
    if not value:return
    row=grant(s,value.grant_id);doc=row.data['document'];claimed=consumed(s,row)
    account=entry.get('profile','')+':'+(entry.get('platforms') or [''])[0]
    if (not claimed or claimed.operation_id!=value.operation_id or revoked(s,row)
            or item.status!='posting' or transport_state(item)!=doc['transport_state_sha256']
            or account not in doc['targets'] or len(entry.get('platforms',[]))!=1
            or entry.get('client_request_id')!=request_keys(s,item).get(account)
            or time.monotonic()-value.checked_monotonic>30
            or not dt.datetime.fromisoformat(doc['slot'])<=dc.now(s)<dt.datetime.fromisoformat(doc['expires_at'])):
        raise upload_post.PayloadBlocked('Canary source/copy/target/authorization/slot changed at atomic destination intent; no request sent')
    if s.query(PublicationEvent.id).filter_by(queue_id=item.id,queue_identity=journal.identity(item),
            attempt_id=entry['client_request_id'],kind='intent').first():
        raise upload_post.PayloadBlocked('This canary destination already has an intent; inspect it without resending')


async def before_destination(account):
    """Final read just before an individual intent/HTTP call; no DB writer lock."""
    value=current()
    if not value:return
    from .db import SessionLocal
    try:
        await refresh_target_identity()
        with SessionLocal() as s:
            row=grant(s,value.grant_id);doc=row.data['document'];claimed=consumed(s,row)
            if (revoked(s,row) or not claimed or claimed.operation_id!=value.operation_id or account not in doc['targets']
                    or not dt.datetime.fromisoformat(doc['slot'])<=dc.now(s)<dt.datetime.fromisoformat(doc['expires_at'])):
                dc.fail('canary destination/slot/claim is no longer valid; do not resend')
    except Exception as exc:
        raise upload_post.PayloadBlocked('Canary native target/slot could not be reverified before transport; inspect the original operation') from exc
