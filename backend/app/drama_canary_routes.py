"""Explicit personal-owner EP1 canary grants; code deployment never grants one."""
import asyncio
import datetime as dt
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from . import drama_canary as canary, drama_controls as dc, pending_metadata as pm
from .auth import require_token
from .db import get_session
from .drama_routes import Operation, ItemOperation, owned, lock_owned
from .models import QueueItem
from .social import upload_post, queue_manager

router=APIRouter(prefix='/api/drama/canary',dependencies=[Depends(require_token)])


class Targets(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    facebook_page_id: str=Field(pattern=r'^\d{5,30}$')
    x_account_id: str=Field(min_length=1,max_length=160)
    x_handle: str=Field(min_length=1,max_length=80)


class Authorization(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    authorizer: str=Field(min_length=1,max_length=200)
    authorization_reference: str=Field(min_length=1,max_length=2000)
    authorized_public_payload_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    authorized_source_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    rights_basis: Literal['owned_original','licensed','explicit_permission']
    rights_reference: str=Field(min_length=1,max_length=4000)
    decision: Literal['I specifically authorize ONE public EP1 canary to the exact reviewed Facebook Page and X account at this future slot, and confirm rights for this exact source and copy']

    @field_validator('authorizer','authorization_reference','rights_reference')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('Record the actual specific owner authorization/rights reference; do not fabricate evidence')
        return value


class GrantPreview(ItemOperation):
    evidence_id: str=Field(pattern=pm.OPERATION_PATTERN)
    review_id: str=Field(pattern=pm.OPERATION_PATTERN)
    ready_id: str=Field(pattern=pm.OPERATION_PATTERN)
    series: list[str]=Field(min_length=2,max_length=2)
    slot: dt.datetime
    targets: Targets
    authorization: Authorization | None=None

    @field_validator('slot',mode='before')
    @classmethod
    def explicit_timezone(cls,value):
        if not isinstance(value,str):raise ValueError('Use an explicit ISO8601 slot string, not a timestamp/boolean')
        value=dt.datetime.fromisoformat(value)
        if value.tzinfo is None:raise ValueError('Specify the exact future timezone-aware canary slot')
        return value.astimezone(dt.timezone.utc)


class Grant(GrantPreview):
    authorization: Authorization
    acknowledged_fingerprint: str=Field(pattern=r'^[a-f0-9]{64}$')


class Dispatch(ItemOperation):
    grant_id: str=Field(pattern=pm.OPERATION_PATTERN)
    grant_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')


class Revoke(Operation):
    grant_sha256: str=Field(pattern=r'^[a-f0-9]{64}$')
    reason: str=Field(min_length=1,max_length=2000)


def hashed(kind,req,qid=None):
    return pm.digest({'version':canary.VERSION,'kind':kind,'queue_id':qid,
                      'request':req.model_dump(exclude={'operation_id'})})


def target_read(page):
    try:return asyncio.run(upload_post.canary_target_identity(page))
    except upload_post.UploadPostError as exc:
        detail=(exc.message if exc.message.startswith('Canary target evidence unavailable (') else
                'Fresh exact native Facebook Page/pin and X identity are required; no authorization or settings were changed')
        raise HTTPException(409,detail) from exc


@router.get('/contract')
def contract(s:Session=Depends(get_session)):
    rows=canary.grants(s)
    return {'version':canary.VERSION,'path_installed':True,'deployment_grants_authority':False,
        'grant_count':len(rows),'consumed_grant_count':sum(canary.consumed(s,row) is not None for row in rows),
        'revoked_grant_count':sum(canary.revoked(s,row) for row in rows),
        'public_posts_maximum_per_grant':2,'episode_limit':1,'required_episode':1,
        'destinations':['Dramarama:facebook','Dramarama:x'],'youtube_included':False,
        'slot':'one exact future10:00America/New_York slot within48hours; five-minute window; no catch-up',
        'specific_owner_public_authorization_and_rights_required':True,
        'deployment_or_broad_rollout_is_not_authorization':True,
        'dispatch_is_separate_explicit_operation':True,'repeat_dispatch':'historical receipt only; no resend',
        'automatic_plan_activation':False,'fidelity_verified':False,'normal_activation':dc.activation_contract()}


@router.get('/targets')
def inspect_targets(facebook_page_id: str=Query(pattern=r'^\d{5,30}$')):
    # Query() rather than arbitrary provider/profile input; Page selection here
    # is a read, not a pin/account mutation or canary authorization.
    return {'read_only':True,'writes':0,'target_identity':target_read(facebook_page_id),
        'rights_or_publication_authorization':False,'entitlement':'unverified conservative baseline only'}


@router.post('/items/{qid}/preview')
def preview(qid:int,req:GrantPreview,s:Session=Depends(get_session)):
    targets=target_read(req.targets.facebook_page_id)
    doc=canary.prepared(s,s.get(QueueItem,qid,populate_existing=True),req,targets)
    return {'preview_only':True,'writes':0,'document':doc,'acknowledged_fingerprint':pm.digest(doc),
        'authorization_notice':'Deployment/broad rollout is not exact public-canary authorization or rights confirmation; record only actual owner evidence',
        'fidelity_verified':False,'automatic_plan_activated':False}


@router.post('/items/{qid}/grants')
def create_grant(qid:int,req:Grant,s:Session=Depends(get_session)):
    digest=hashed('grant',req,qid)
    if saved:=dc.replay(s,req.operation_id,digest):return saved
    targets=target_read(req.targets.facebook_page_id)
    with owned(s,[qid],plan=True):
        lock_owned(s)
        if saved:=dc.replay(s,req.operation_id,digest):s.rollback();return saved
        item=s.get(QueueItem,qid,populate_existing=True)
        doc=canary.prepared(s,item,req,targets,require_authorization=True)
        if pm.digest(doc)!=req.acknowledged_fingerprint:dc.fail('canary source/copy/target/slot/authorization preview changed; review it again')
        response=dc.record(s,req.operation_id,digest,'canary_grant',{'grant_id':req.operation_id,
            'grant_sha256':pm.digest(doc),'slot':doc['slot'],'expires_at':doc['expires_at'],
            'state':'granted_not_dispatched','fidelity_verified':False,'automatic_plan_activated':False},item=item,
            canary_scope=canary.SCOPE,document=doc)
        s.commit();return response


@router.get('/grants/{grant_id}')
def status(grant_id:str,s:Session=Depends(get_session)):
    row=canary.grant(s,grant_id);doc=row.data['document'];dispatch=canary.consumed(s,row)
    return {'grant_id':grant_id,'grant_sha256':pm.digest(doc),'document':doc,
        'revoked':canary.revoked(s,row),'expired':dc.now(s)>=dt.datetime.fromisoformat(doc['expires_at']),
        'dispatch_id':dispatch.operation_id if dispatch else None,
        'dispatch_receipt':dispatch.data['response'] if dispatch else None,
        'publication_evidence_path':f'/api/queue/{row.queue_id}/publication-evidence',
        'historical_snapshot_only':True,'fidelity_verified':False,'automatic_plan_activated':False}


@router.post('/grants/{grant_id}/revoke')
def revoke(grant_id:str,req:Revoke,s:Session=Depends(get_session)):
    digest=pm.digest({'kind':'canary_revoke','grant_id':grant_id,'request':req.model_dump(exclude={'operation_id'})})
    if saved:=dc.replay(s,req.operation_id,digest):return saved
    row=canary.grant(s,grant_id)
    # Append-only cancellation must remain possible while the publisher holds
    # its row lease. It never rewinds a consumed grant or recalls sent requests.
    with owned(s,[],plan=True):
        lock_owned(s)
        if saved:=dc.replay(s,req.operation_id,digest):s.rollback();return saved
        row=canary.grant(s,grant_id)
        if pm.digest(row.data['document'])!=req.grant_sha256:dc.fail('canary grant version changed')
        response=dc.record(s,req.operation_id,digest,'canary_revoke',{'revoked':grant_id,'no_post_recalled':True},
            grant_id=grant_id,reason=req.reason,canary_scope=canary.SCOPE,
            consumed_dispatch_id=(used.operation_id if (used:=canary.consumed(s,row)) else None))
        s.commit();return response


@router.post('/items/{qid}/dispatch')
def dispatch(qid:int,req:Dispatch,s:Session=Depends(get_session)):
    digest=hashed('dispatch',req,qid)
    if saved:=dc.replay(s,req.operation_id,digest):return saved
    row=canary.grant(s,req.grant_id)
    if row.queue_id!=qid:dc.fail('canary grant belongs to another exact queue identity')
    doc=row.data['document'];target=target_read(doc['target_identity']['facebook_page_id'])
    value=canary.Dispatch(req.operation_id,digest,req.grant_id,qid,req.expected_fingerprint,
        req.grant_sha256,target,time.monotonic())
    s.rollback()
    try:
        with canary.dispatch_scope(value):asyncio.run(queue_manager.publish_queue_item(qid,s))
    except (upload_post.UploadPostError,HTTPException) as exc:
        s.rollback()
        if saved:=dc.replay(s,req.operation_id,digest):return {**saved,'inspect_original_operation':True}
        if isinstance(exc,HTTPException):raise
        raise HTTPException(exc.status_code,exc.message) from exc
    saved=dc.replay(s,req.operation_id,digest)
    if saved is None:dc.fail('no committed canary dispatch receipt; inspect the operation before retrying unchanged')
    return {**saved,'replayed':False,'inspect_original_operation':True}
