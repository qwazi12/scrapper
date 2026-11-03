"""Separate original 120s diagnostic. Never a queue row, episode or schedule."""
import asyncio
import contextlib
import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid5

from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy.orm import Session

from . import drama_media as dm, drama_controls as dc, pending_metadata as pm, worker_leases as leases
from .auth import require_token
from .budget_reservations import lock
from .db import get_session,SessionLocal
from .drama_routes import Operation
from .models import DramaRecord
from .social import upload_post
from .file_use import pin,suspend

SCOPE='original-120s-facebook-calibration-v1'
RECIPE='original-color-tones-120-v1'
router=APIRouter(prefix='/api/drama/calibration',dependencies=[Depends(require_token)])


@contextlib.contextmanager
def owned():
    try:
        with leases.scope('drama:calibration'):yield
    except leases.LeaseLost as exc:
        raise HTTPException(409,'Diagnostic is already owned or its generation changed; inspect the original receipt without resending') from exc


def generate(destination):
    # Entirely generated, no file/URL inputs, third-party content or fonts.
    args=['ffmpeg','-hide_banner','-nostdin','-n','-f','lavfi','-i',
        "color=c=blue:s=1080x1920:r=30:d=120,drawbox=x=0:y=0:w=iw:h=ih:color=green:t=fill:enable='between(t,30,60)',drawbox=x=0:y=0:w=iw:h=ih:color=yellow:t=fill:enable='between(t,60,90)',drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='gte(t,90)'",
        '-f','lavfi','-i',r'aevalsrc=if(lt(mod(t\,10)\,0.25)\,0.2*sin(2*PI*880*t)\,0):s=48000:d=120',
        '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast','-crf','20',
        '-pix_fmt','yuv420p','-threads:v','2','-x264-params','open-gop=0','-c:a','aac','-profile:a','aac_low',
        '-ar','48000','-ac','2','-b:a','128k','-movflags','+faststart','-fs',str(dm.MAX_BYTES),'{artifact}']
    recipe={'id':RECIPE,'arguments':args,'tools':dm.tool_versions()}
    dm.media.run([str(destination) if x=='{artifact}' else x for x in args],timeout=180)
    return recipe


def validate_asset(path):
    proof=dm.inspect(path)
    if not dm.compatible(proof) or abs(proof['duration']-120)>0.03 or proof['media']['frame_count']!=3600:
        dc.fail('diagnostic requires the complete original120-second generated H264/AAC-LC asset, not an episode')
    colors={}
    for second,expected in ((15,(0,0,255)),(45,(0,128,0)),(75,(255,255,0)),(105,(255,0,0))):
        result=dm.control.run(['ffmpeg','-v','error','-nostdin','-threads','2','-filter_threads','1','-ss',str(second),'-i',str(path),
            '-vf','scale=1:1','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],timeout=30,text=False)
        actual=list(result.stdout)
        if result.returncode or len(actual)!=3 or any(abs(a-b)>4 for a,b in zip(actual,expected)):
            dc.fail('generated original calibration color/tail marker mismatch')
        colors[str(second)]=actual
    result=dm.control.run(['ffmpeg','-v','error','-nostdin','-threads','2','-ss','110','-i',str(path),'-t','0.15',
        '-map','0:a:0','-ac','1','-ar','16000','-f','f32le','-'],timeout=30,text=False)
    from array import array
    samples=array('f');samples.frombytes(result.stdout)
    if result.returncode or not samples or not 0.1<max(abs(x) for x in samples)<0.3:
        dc.fail('generated original calibration late audio pulse is missing or changed')
    return {'proof':proof,'colors':colors,'late_audio_pulse_verified':True}


class Prepare(Operation):
    recipe:Literal['original-color-tones-120-v1']


class Authorization(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    authorizer:str=Field(min_length=1,max_length=200)
    authorization_reference:str=Field(min_length=1,max_length=2000)
    rights_reference:str=Field(min_length=1,max_length=2000)
    authorized_asset_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    authorized_payload_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    decision:Literal['I authorize ONE public 120-second original calibration VIDEO to this exact Facebook Page and future slot, and confirm rights for this exact asset and copy']

    @field_validator('authorizer','authorization_reference','rights_reference')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('Actual specific owner authorization and rights reference required')
        return value


class Preview(Operation):
    asset_id:str=Field(pattern=pm.OPERATION_PATTERN)
    asset_document_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    facebook_page_id:str=Field(pattern=r'^\d{5,30}$')
    title:str=Field(min_length=1,max_length=255)
    description:str=Field(min_length=1,max_length=10000)
    slot:dt.datetime
    authorization:Authorization | None=None

    @field_validator('slot',mode='before')
    @classmethod
    def aware_slot(cls,value):
        if not isinstance(value,str):raise ValueError('An explicit ISO8601 future slot is required')
        value=dt.datetime.fromisoformat(value)
        if value.tzinfo is None:raise ValueError('Timezone is required')
        return value.astimezone(dt.timezone.utc)


class Grant(Preview):
    authorization:Authorization
    acknowledged_fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')


class Dispatch(Operation):
    grant_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


class Revoke(Dispatch):
    reason:str=Field(min_length=1,max_length=2000)


def digest(kind,req,identity=None):return pm.digest({'kind':kind,'scope':SCOPE,'id':identity,'request':req.model_dump(exclude={'operation_id'})})


def rows(s,kind):return s.query(DramaRecord).filter(DramaRecord.kind==kind,DramaRecord.data['scope'].as_string()==SCOPE).order_by(DramaRecord.id).all()


def get(s,operation_id,kind):
    row=s.query(DramaRecord).filter_by(operation_id=operation_id,kind=kind).first()
    if not row or row.data.get('scope')!=SCOPE:dc.fail('diagnostic record not found in this separate scope')
    return row


def consumed(s,row):
    return s.query(DramaRecord).filter(DramaRecord.kind=='calibration_dispatch',DramaRecord.data['grant_id'].as_string()==row.operation_id).first()


def revoked(s,row):
    return bool(s.query(DramaRecord.id).filter(DramaRecord.kind=='calibration_revoke',DramaRecord.data['grant_id'].as_string()==row.operation_id).first())


def target(page):
    try:
        value=asyncio.run(upload_post.facebook_page_identity(page))
        if (value.get('profile')!='Dramarama' or value.get('accounts')!=['Dramarama:facebook']
                or any(value.get(k)!=page for k in ('facebook_page_id','facebook_pinned_page_id','profile_pinned_page_id'))):
            raise upload_post.PayloadBlocked('Exact diagnostic Page/pin snapshot required')
        return value
    except upload_post.UploadPostError as exc:raise HTTPException(409,'Exact current native Facebook Page/pins unavailable; no settings changed') from exc


def asset(s,asset_id,expected):
    row=get(s,asset_id,'calibration_asset');doc=row.data['document'];path=dm.path(asset_id)
    if (pm.digest(doc)!=expected or doc['recipe'].get('id')!=RECIPE or doc.get('external_assets') is not False
            or doc.get('is_episode') is not False or path.is_symlink() or not path.is_file()
            or dm.file_hash(path)!=doc['media']['proof']['sha256']):
        dc.fail('exact generated diagnostic artifact missing or changed')
    return doc,path


def proposal(s,req,identity):
    doc,_=asset(s,req.asset_id,req.asset_document_sha256)
    if not dc.now(s)+dt.timedelta(minutes=1)<req.slot<=dc.now(s)+dt.timedelta(days=2):
        dc.fail('diagnostic needs one explicit future slot within48hours; no catch-up')
    payload=upload_post.prepare_video_fields('Dramarama',['facebook'],req.title,req.description,
        {'facebook_media_type':'VIDEO','facebook_page_id':req.facebook_page_id})
    if req.authorization and (req.authorization.authorized_asset_sha256!=doc['media']['proof']['sha256']
            or req.authorization.authorized_payload_sha256!=pm.digest(payload)):
        dc.fail('diagnostic authorization must bind exact generated asset and visible copy')
    result={'scope':SCOPE,'asset_id':req.asset_id,'asset_document_sha256':req.asset_document_sha256,
        'original_sha256':doc['media']['proof']['sha256'],'artifact_sha256':doc['media']['proof']['sha256'],
        'recipe_sha256':pm.digest(doc['recipe']),'target':identity,'payload':payload,'privacy':'public',
        'slot':req.slot.isoformat(),'expires_at':(req.slot+dt.timedelta(minutes=5)).isoformat(),
        'authorization':req.authorization.model_dump() if req.authorization else None,
        'is_episode':False,'normal_plan_activated':False,'fidelity_verified':False}
    previous=rows(s,'calibration_grant')
    if previous:
        first=previous[0].data['document']
        if first['original_sha256']!=result['original_sha256'] or first['target']!=identity:
            dc.fail('diagnostic scope is already frozen to another original asset/native Page')
        if any(consumed(s,row) or not revoked(s,row) for row in previous):dc.fail('diagnostic already granted or consumed; inspect without resending')
    return result


@router.get('/contract')
def contract(s:Session=Depends(get_session)):
    grants=rows(s,'calibration_grant')
    return {'scope':SCOPE,'deployment_grants_authority':False,'generated_original_seconds':120,
        'grant_count':len(grants),'consumed_count':sum(consumed(s,r) is not None for r in grants),
        'destination':'Dramarama:facebook','mode':'VIDEO','max_public_requests_ever':1,
        'queue_or_episode_created':False,'schedule_changes':False,'fidelity_verified':False,
        'specific_owner_authorization_and_rights_required':True,'normal_activation':dc.activation_contract()}


@router.post('/assets/prepare')
def prepare(req:Prepare,s:Session=Depends(get_session)):
    hashed=digest('calibration_prepare',req)
    if saved:=dc.replay(s,req.operation_id,hashed):return saved
    with owned():
        if saved:=dc.replay(s,req.operation_id,hashed):s.rollback();return saved
        destination=dm.path(req.operation_id)
        if destination.parent.exists():dc.fail('diagnostic artifact exists; never overwrite on retry')
        if shutil.disk_usage(dm.settings.data_path).free<dm.MAX_BYTES*2:dc.fail('insufficient bounded diagnostic disk reserve')
        destination.parent.mkdir(parents=True);s.rollback()
        # Leave generated/uncertain artifacts intact on any later receipt error.
        recipe=generate(destination);observed=validate_asset(destination)
        doc={'recipe':recipe,'media':observed,'external_assets':False,'is_episode':False,
            'publication_authorized':False,'provenance':'server fixed generated colors and mathematical tones only'}
        lock(s);leases.guard(s)
        response=dc.record(s,req.operation_id,hashed,'calibration_asset',{'asset_id':req.operation_id,
            'document_sha256':pm.digest(doc),'document':doc},scope=SCOPE,document=doc)
        s.commit();return response


@router.post('/preview')
def preview(req:Preview,s:Session=Depends(get_session)):
    doc=proposal(s,req,target(req.facebook_page_id))
    return {'document':doc,'acknowledged_fingerprint':pm.digest(doc),'writes':0}


@router.get('/assets/{asset_id}/artifact')
def review_artifact(asset_id:str,s:Session=Depends(get_session)):
    from .file_response import FileResponse
    row=get(s,asset_id,'calibration_asset')
    _,path=asset(s,asset_id,pm.digest(row.data['document']))
    return FileResponse(path,media_type='video/mp4',filename='original-120-second-calibration.mp4',expected_sha256=row.data['document']['media']['proof']['sha256'])


@router.post('/grants')
def grant(req:Grant,s:Session=Depends(get_session)):
    hashed=digest('calibration_grant',req)
    if saved:=dc.replay(s,req.operation_id,hashed):return saved
    identity=target(req.facebook_page_id)
    with owned():
        lock(s);leases.guard(s)
        if saved:=dc.replay(s,req.operation_id,hashed):s.rollback();return saved
        doc=proposal(s,req,identity)
        if pm.digest(doc)!=req.acknowledged_fingerprint:dc.fail('diagnostic source/copy/Page/slot/authorization preview changed')
        response=dc.record(s,req.operation_id,hashed,'calibration_grant',{'grant_id':req.operation_id,
            'grant_sha256':pm.digest(doc),'state':'granted_not_dispatched','fidelity_verified':False},scope=SCOPE,document=doc)
        s.commit();return response


@router.get('/grants/{grant_id}')
def status(grant_id:str,s:Session=Depends(get_session)):
    row=get(s,grant_id,'calibration_grant');claim=consumed(s,row)
    results=[r.data for r in rows(s,'calibration_result') if claim and r.data.get('dispatch_id')==claim.operation_id]
    observations=[r.data for r in rows(s,'calibration_observe') if claim and r.data.get('dispatch_id')==claim.operation_id]
    return {'grant_id':grant_id,'document':row.data['document'],'revoked':revoked(s,row),
        'dispatch':claim.data if claim else None,'results':results,'observations':observations,'fidelity_verified':False,'resend_authorized':False,
        'historical_snapshot_only':True,'notice':'Retained original request evidence only; no episode, auto-activation or resend authority'}


@router.post('/grants/{grant_id}/observe')
def observe(grant_id:str,req:Operation,s:Session=Depends(get_session)):
    hashed=digest('calibration_observe',req,grant_id)
    if saved:=dc.replay(s,req.operation_id,hashed):return saved
    grant=get(s,grant_id,'calibration_grant');claim=consumed(s,grant)
    results=[r for r in rows(s,'calibration_result') if claim and r.data.get('dispatch_id')==claim.operation_id]
    request_ids={r.data['response'].get('request_id') for r in results}-{None}
    if len(request_ids)!=1:dc.fail('original diagnostic request ID is unknown; do not guess or submit again')
    request_id=next(iter(request_ids));s.rollback()
    try:observed=asyncio.run(upload_post.get_status(request_id))
    except upload_post.UploadPostError as exc:raise HTTPException(409,'Original provider observation unavailable; retain uncertainty without resending') from exc
    lock(s)
    if saved:=dc.replay(s,req.operation_id,hashed):s.rollback();return saved
    response=dc.record(s,req.operation_id,hashed,'calibration_observe',{'provider_request_id':request_id,
        'observed_status':observed,'fidelity_verified':False,'resend_authorized':False},scope=SCOPE,
        dispatch_id=claim.operation_id,original_sha256=claim.data['original_sha256'],artifact_sha256=claim.data['artifact_sha256'])
    s.commit();return response


@router.post('/grants/{grant_id}/revoke')
def revoke(grant_id:str,req:Revoke,s:Session=Depends(get_session)):
    hashed=digest('calibration_revoke',req,grant_id)
    if saved:=dc.replay(s,req.operation_id,hashed):return saved
    lock(s)
    if saved:=dc.replay(s,req.operation_id,hashed):s.rollback();return saved
    row=get(s,grant_id,'calibration_grant')
    if pm.digest(row.data['document'])!=req.grant_sha256:dc.fail('grant version changed')
    result=dc.record(s,req.operation_id,hashed,'calibration_revoke',{'revoked':grant_id,'no_post_recalled':True},
        scope=SCOPE,grant_id=grant_id,reason=req.reason)
    s.commit();return result


def check(s,row,req,identity):
    doc=row.data['document']
    if (pm.digest(doc)!=req.grant_sha256 or revoked(s,row) or identity!=doc['target']
            or not dt.datetime.fromisoformat(doc['slot'])<=dc.now(s)<dt.datetime.fromisoformat(doc['expires_at'])):
        dc.fail('diagnostic exact grant/target/slot is stale, revoked, future or expired')
    return asset(s,doc['asset_id'],doc['asset_document_sha256'])[1]


@router.post('/grants/{grant_id}/dispatch')
def dispatch(grant_id:str,req:Dispatch,s:Session=Depends(get_session)):
    hashed=digest('calibration_dispatch',req,grant_id)
    if saved:=dc.replay(s,req.operation_id,hashed):return saved
    row=get(s,grant_id,'calibration_grant');doc=row.data['document']
    with owned():
        identity=target(doc['target']['facebook_page_id'])
        path=check(s,row,req,identity);s.rollback()
        with pin([path],session=s):
            observed=validate_asset(path)
            if observed!=get(s,doc['asset_id'],'calibration_asset').data['document']['media']:dc.fail('actual complete calibration media changed')
            identity=target(doc['target']['facebook_page_id'])
            lock(s);leases.guard(s)
            if saved:=dc.replay(s,req.operation_id,hashed):s.rollback();return saved
            row=get(s,grant_id,'calibration_grant');check(s,row,req,identity)
            if consumed(s,row):dc.fail('one-shot diagnostic was already consumed; no resend')
            request_key=uuid5(UUID(req.operation_id),pm.digest([doc['original_sha256'],doc['payload'],doc['target']])).hex
            response=dc.record(s,req.operation_id,hashed,'calibration_dispatch',{'dispatch_id':req.operation_id,
                'state':'durable_intent_claimed','transport_acceptance':'unknown_until_original_receipt',
                'fidelity_verified':False,'resend_authorized':False},scope=SCOPE,grant_id=grant_id,
                client_request_id=request_key,document=doc,original_sha256=doc['original_sha256'],artifact_sha256=doc['artifact_sha256'])
            s.commit()  # A crash here consumes authority, even if nothing was sent.
            result={'state':'rejected','transport_dispatched':False}
            try:
                identity=target(doc['target']['facebook_page_id'])
                lock(s);leases.guard(s);check(s,get(s,grant_id,'calibration_grant'),req,identity)
                dc.record(s,str(uuid5(UUID(req.operation_id),'send-intent')),pm.digest(doc),'calibration_send_intent',
                    {'client_request_id':request_key},scope=SCOPE,dispatch_id=req.operation_id,document=doc)
                s.commit()
                result={'state':'unknown','transport_dispatched':'unknown'}
                rid=asyncio.run(upload_post.upload_video(path,profile='Dramarama',platforms=['facebook'],
                    title=doc['payload']['facebook_title'],description=doc['payload']['facebook_description'],
                    privacy='public',tags=[],idempotency_key=request_key,platform_overrides=doc['payload']))
                result={'state':'accepted','request_id':rid,'transport_dispatched':True}
            except Exception as exc:
                s.rollback()
                result={'state':'rejected' if isinstance(exc,(HTTPException,upload_post.PayloadBlocked)) else 'unknown',
                    'error_type':type(exc).__name__,'transport_dispatched':result.get('transport_dispatched','unknown')}
            # Preserve a late receipt independently of a lost publisher generation.
            with leases.bind(None),suspend(),SessionLocal() as receipts:
                lock(receipts)
                dc.record(receipts,str(uuid5(UUID(req.operation_id),'transport-result')),pm.digest(result),'calibration_result',
                    result,scope=SCOPE,dispatch_id=req.operation_id,client_request_id=request_key,
                    original_sha256=doc['original_sha256'],artifact_sha256=doc['artifact_sha256'])
                receipts.commit()
            return {**response,'inspect_original_operation':True}
