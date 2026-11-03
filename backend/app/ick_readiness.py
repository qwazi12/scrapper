"""Exact ICK release receipts and future cadence reservations, never bulk approval."""
import asyncio
import datetime as dt
import hmac
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from . import pending_metadata as pm,publication_journal as journal,worker_leases as leases
from .auth import require_token
from .budget_reservations import lock
from .db import get_session
from .models import MetadataMutationReceipt,QueueItem
from .social import queue_manager as qm,queue_payloads,upload_post

VERSION='ick-future-ready-v1'
router=APIRouter(prefix='/api/ick-readiness',dependencies=[Depends(require_token)])


def fail(message):raise HTTPException(409,'ICK Ready: '+message)


@contextmanager
def rollback_on_error(s):
    try:yield
    except BaseException:
        s.rollback()  # Release mutex before file-use/lease cleanup writers run.
        raise


def records(s,item,kind):
    return [r for r in s.query(MetadataMutationReceipt).filter_by(queue_identity=journal.identity(item))
            .order_by(MetadataMutationReceipt.created_at.desc(),MetadataMutationReceipt.operation_id.desc()).all()
            if r.data.get('scope')==kind]


def guard(s,item):
    withdrawn={r.data['release_id'] for r in records(s,item,'ick-ready-withdraw-v1')}
    values=[r for r in records(s,item,VERSION) if r.operation_id not in withdrawn]
    if len(values)>1:fail('multiple retained releases require explicit reconciliation; never resend')
    return values[0] if values else None


def reservations(s):
    return s.query(MetadataMutationReceipt).filter(MetadataMutationReceipt.data['scope'].as_string()==VERSION).all()


def contract(s,item):
    return {'queue':{c.key:getattr(item,c.key) for c in QueueItem.__table__.columns
            if c.key not in ('status','publish_requests','published_at')},'source_binding':pm._binding(s,item)}


def approval(s,item,expected,*,initial=False):
    rows=records(s,item,'pending_public_metadata_only')
    if not rows or rows[0].operation_id!=expected:fail('current exact metadata approval receipt changed or is missing')
    row=rows[0];context=row.data.get('approval_context') or {}
    if context.get('applied_public_copy_sha256')!=pm.digest({k:getattr(item,k) for k in pm.PUBLIC_FIELDS}):
        fail('current public copy is not the exact recorded approved metadata context')
    if initial and row.data['after']['fingerprint']!=pm.fingerprint(s,item):
        fail('protected/media/approval state changed since metadata review; refresh its exact review')
    return row


def pending(s,item,expected):
    pm.require_pending(s,item)
    if item.pipeline!=qm.ICK_ROOM or item.status!='review':fail('only an unscheduled ICK Review item is eligible')
    if item.scheduled_at or item.pinned_at or item.post_next_at:fail('existing schedule/pin/Post Next must remain untouched')
    if item.media_kind!='video' or item.media_url or item.compilation_id or item.clip_id or (item.research or {}).get('native_export_id'):
        fail('use the original Studio/native workflow for this unsupported source type')
    if not item.title or not item.title.strip():fail('review an explicit public title; filename fallback is not approval')
    if not hmac.compare_digest(expected,pm.fingerprint(s,item)):fail('metadata/media/approval/schedule changed; refresh before approval')
    if guard(s,item):fail('an immutable release already exists; inspect its original receipt, not another approval')


def remote(item):
    if not item.drive_link:return None
    from .drive_sync import extract_drive_file_id,get_drive_service
    fid=extract_drive_file_id(item.drive_link)
    if not fid:fail('canonical Drive file identity missing')
    value=get_drive_service().files().get(fileId=fid,supportsAllDrives=True,
        fields='id,size,md5Checksum,sha256Checksum,version,trashed,mimeType').execute(num_retries=0)
    if value.get('id')!=fid or value.get('trashed') or not value.get('md5Checksum') or not value.get('version'):
        fail('current Drive version/checksum is unconfirmed or deleted')
    return value


def verify_remote(item,proof):
    if remote(item)!=proof['remote']:fail('Drive identity/version/checksum changed; inspect the actual media again')


def thumbnail_path(item):
    value=item.thumb_path
    # Match the publisher: HTTP display references are never multipart files.
    # Their unchanged identity is already bound by the full queue fingerprint.
    if not value or value.startswith('http'):return None
    path=Path(value)
    try:valid=not path.is_symlink() and path.is_file()
    except OSError:valid=False
    if not valid:fail('required local transport thumbnail is missing or unsafe; restore its exact file before inspection')
    return path


def thumbnail_hash(item):
    from . import drama_media as dm
    path=thumbnail_path(item)
    if path is None:return None
    try:return dm.file_hash(path)
    except OSError:fail('required local transport thumbnail cannot be read; restore its exact file before inspection')


def verify_thumbnail(item,proof):
    if ('thumbnail_reference_sha256' in proof and proof['thumbnail_reference_sha256']!=pm.digest(item.thumb_path)):
        fail('thumbnail reference changed after inspection')
    if thumbnail_hash(item)!=proof['thumbnail_sha256']:fail('thumbnail bytes changed after inspection')


def verify_local(item,proof):
    from . import drama_media as dm
    if item.video_path and Path(item.video_path).is_file():
        path=Path(item.video_path)
        if path.stat().st_size!=proof['actual']['file_size'] or dm.file_hash(path)!=proof['actual']['sha256']:fail('local media bytes changed after inspection')
    elif proof['remote'] is None:fail('inspected local media is missing')
    verify_thumbnail(item,proof)


def save(s,op,hashed,item,scope,data,response):
    response={**response,'receipt_id':op,'replayed':False,'historical_snapshot_only':True}
    s.add(MetadataMutationReceipt(operation_id=op,queue_id=item.id,queue_identity=journal.identity(item),
        request_hash=hashed,data={'scope':scope,**data,'response':response}));s.flush();return response


class Inspect(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation_id:str=Field(pattern=pm.OPERATION_PATTERN)
    expected_fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')
    approval_receipt_id:str=Field(pattern=pm.OPERATION_PATTERN)


class Preview(Inspect):
    media_receipt_id:str=Field(pattern=pm.OPERATION_PATTERN)
    destination_ids:list[str]=Field(min_length=1,max_length=4)
    facebook_page_id:str=Field(pattern=r'^\d{5,30}$')
    x_variant_receipt_id:str | None=Field(default=None,pattern=pm.OPERATION_PATTERN)


class Ready(Preview):
    acknowledged_preview_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')


class Variant(Inspect):
    text:str=Field(min_length=1,max_length=2000)
    approved_revision:str=Field(min_length=1,max_length=120)
    approved_workbook_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    approved_proposal_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    approved_sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    approval_reference:str=Field(min_length=1,max_length=2000)
    decision:Literal['Accept this exact X-only variant for the unchanged reviewed ICK source and Facebook copy']


class Withdraw(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operation_id:str=Field(pattern=pm.OPERATION_PATTERN)
    expected_fingerprint:str=Field(pattern=r'^[a-f0-9]{64}$')
    release_id:str=Field(pattern=pm.OPERATION_PATTERN)
    reason:str=Field(min_length=1,max_length=2000)
    decision:Literal['Withdraw this unsent ICK reservation without posting or reusing its old slot']


def variant(s,item,receipt_id,reviewed):
    if receipt_id is None:return None
    row=s.get(MetadataMutationReceipt,receipt_id)
    if (not row or row.queue_identity!=journal.identity(item) or row.data.get('scope')!='ick-x-variant-v1'
            or row.data['approval_receipt_id']!=reviewed.operation_id
            or row.data['copy_sha256']!=pm.digest({k:getattr(item,k) for k in pm.PUBLIC_FIELDS})):
        fail('exact owner-reviewed X variant receipt is missing or stale')
    content=row.data['variant']
    if content['approved_sha256']!=pm.digest(content['text']):fail('X variant approval checksum differs')
    return row


@router.post('/items/{qid}/x-variants/review')
def review_variant(qid:int,req:Variant,s:Session=Depends(get_session)):
    hashed=pm.digest(['ick-x-variant-v1',qid,req.model_dump(exclude={'operation_id'})])
    if replay:=pm._replay(s,req.operation_id,hashed):return replay
    s.rollback()
    try:
        with leases.scope(f'publication:{qid}',session=s),rollback_on_error(s):
            lock(s)
            if replay:=pm._replay(s,req.operation_id,hashed):s.rollback();return replay
            item=s.get(QueueItem,qid,populate_existing=True);pending(s,item,req.expected_fingerprint)
            reviewed=approval(s,item,req.approval_receipt_id,initial=True)
            if req.approved_sha256!=pm.digest(req.text) or not req.approved_revision.strip() or not req.approval_reference.strip():
                fail('actual exact X approval reference/revision/checksum required; never fabricate a review')
            try:queue_payloads.preview(item.title,item.description,item.tags,['facebook','x'],ick=True,x_variant_text=req.text)
            except ValueError as exc:fail(str(exc))
            content=req.model_dump(exclude={'operation_id','expected_fingerprint','approval_receipt_id'})
            result=save(s,req.operation_id,hashed,item,'ick-x-variant-v1',
                {'variant':content,'approval_receipt_id':reviewed.operation_id,'copy_sha256':pm.digest({k:getattr(item,k) for k in pm.PUBLIC_FIELDS})},
                {'x_variant_receipt_id':req.operation_id,'approved_text_sha256':req.approved_sha256,'approval_scope':'owner-supplied exact variant statement; no automatic review or source rights certification'})
            s.commit();return result
    except leases.LeaseLost:s.rollback();fail('publication/review already leased; inspect receipt')


@router.get('/contract')
def capabilities():
    return {'version':VERSION,'single_item_only':True,'automatic_approval':False,'cadence_changes':False,
        'future_only':True,'catch_up':False,'dispatch_window_seconds':300,'missing_evidence':'hold',
        'copy_overflow':'needs separately reviewed exact X variant; never truncate',
        'missed_slot_recovery':'Explicit conditional withdrawal to Review, fresh evidence/approval/preview, then new future reservation. No automatic rollover or old-slot reuse.',
        'semantic_rights_verification':'not inferred from technical media or metadata markers'}


@router.get('/receipts/{op}')
def receipt(op:str,s:Session=Depends(get_session)):
    row=s.get(MetadataMutationReceipt,op)
    if not row or row.data.get('scope') not in (VERSION,'ick-media-v1','ick-x-variant-v1','ick-ready-withdraw-v1'):fail('receipt not found')
    state=None
    if row.data.get('scope')==VERSION:
        withdrawn=any(r.data.get('release_id')==op for r in s.query(MetadataMutationReceipt).filter_by(queue_identity=row.queue_identity)
            if r.data.get('scope')=='ick-ready-withdraw-v1')
        state='withdrawn' if withdrawn else 'expired_no_catchup' if leases.clock(s)>=dt.datetime.fromisoformat(row.data['document']['expires_at']) else 'future_or_current_reservation'
    return {'operation_id':op,'queue_identity':row.queue_identity,'reservation_state':state,**row.data}


@router.post('/items/{qid}/withdraw')
def withdraw(qid:int,req:Withdraw,s:Session=Depends(get_session)):
    hashed=pm.digest(['ick-ready-withdraw-v1',qid,req.model_dump(exclude={'operation_id'})])
    if replay:=pm._replay(s,req.operation_id,hashed):return replay
    s.rollback()
    try:
        with leases.scope(f'publication:{qid}',session=s),rollback_on_error(s):
            lock(s)
            if replay:=pm._replay(s,req.operation_id,hashed):s.rollback();return replay
            item=s.get(QueueItem,qid,populate_existing=True);pm.require_pending(s,item)
            released=guard(s,item)
            if (not released or released.operation_id!=req.release_id or item.status!='ready'
                    or pm.fingerprint(s,item)!=req.expected_fingerprint or not req.reason.strip()):
                fail('exact unsent current reservation/fingerprint and actual withdrawal reason required')
            before=pm.view(s,item)
            n=s.execute(update(QueueItem).where(*pm._same_snapshot(item)).values(status='review',scheduled_at=None),
                execution_options={'synchronize_session':False}).rowcount
            if n!=1:fail('reservation changed during withdrawal')
            s.refresh(item);response=save(s,req.operation_id,hashed,item,'ick-ready-withdraw-v1',
                {'release_id':released.operation_id,'before':before,'after':pm.view(s,item),'reason':req.reason},
                {'status':'review','old_slot_reusable':False,'publication_authorized':False})
            s.commit();return response
    except leases.LeaseLost:s.rollback();fail('publisher owns the reservation; no withdrawal or resend')


@router.post('/items/{qid}/inspect')
def inspect(qid:int,req:Inspect,s:Session=Depends(get_session)):
    hashed=pm.digest(['ick-media-v1',qid,req.model_dump(exclude={'operation_id'})])
    if replay:=pm._replay(s,req.operation_id,hashed):return replay
    s.rollback()
    try:
        with leases.scope(f'publication:{qid}',session=s):
            if replay:=pm._replay(s,req.operation_id,hashed):s.rollback();return replay
            item=s.get(QueueItem,qid,populate_existing=True);pending(s,item,req.expected_fingerprint)
            reviewed=approval(s,item,req.approval_receipt_id,initial=True)
            initial=remote(item);temporary=None
            from . import drama_media as dm,drive_sync
            from .file_use import pin
            try:
                path=Path(item.video_path) if item.video_path else None
                if not path or not path.is_file():
                    if not initial:fail('actual video unavailable; no title/topic-only approval')
                    temporary=path=drive_sync.download_drive_file(item.drive_link)
                thumb_path=thumbnail_path(item)
                paths=[path]+([thumb_path] if thumb_path else [])
                with pin(paths),rollback_on_error(s):
                    actual=dm.inspect(path)
                    if not dm.compatible(actual):fail('actual input needs verified MP4/H264/AAC-LC; no silent transcode')
                    thumb=thumbnail_hash(item)
                    if initial:
                        import hashlib
                        if (int(initial['size'])!=actual['file_size'] or hashlib.md5(path.read_bytes()).hexdigest()!=initial['md5Checksum']
                                or initial.get('sha256Checksum',actual['sha256'])!=actual['sha256']):fail('actual source bytes differ from Drive evidence')
                    proof={'remote':initial,'actual':actual,'thumbnail_sha256':thumb,
                        'thumbnail_reference_sha256':pm.digest(item.thumb_path)}
                    verify_remote(item,proof)
                    s.rollback()  # End the read snapshot after independent file-use heartbeats.
                    lock(s);leases.guard(s);s.refresh(item);pending(s,item,req.expected_fingerprint)
                    approval(s,item,req.approval_receipt_id,initial=True)
                    response=save(s,req.operation_id,hashed,item,'ick-media-v1',
                        {'proof':proof,'fingerprint':req.expected_fingerprint,'approval_receipt_id':reviewed.operation_id},
                        {'media_receipt_id':req.operation_id,'media_sha256':actual['sha256'],'media_duration':actual['duration'],'publication_authorized':False})
                    s.commit();return response
            finally:
                if temporary:temporary.unlink(missing_ok=True)
    except leases.LeaseLost as exc:s.rollback();fail('publication/inspection is already leased; inspect the original receipt')


def schedule(s):
    return qm.load_schedule(s)


@router.get('/items/{qid}/copy-preview')
def copy_preview(qid:int,s:Session=Depends(get_session)):
    item=s.get(QueueItem,qid,populate_existing=True);pm.require_pending(s,item)
    if item.pipeline!=qm.ICK_ROOM:fail('this release supports only the ICK queue')
    result={'fingerprint':pm.fingerprint(s,item),'public_copy_sha256':pm.digest({k:getattr(item,k) for k in pm.PUBLIC_FIELDS}),
        'writes':0,'automatic_approval':False,'media_or_rights_verified':False,'x_limit':280}
    try:
        text,appended=queue_payloads.x_text(item.title,item.description,item.tags)
        result.update(x_text=text,x_appended_hashtags=appended,x_weight_upper_bound=queue_payloads.weight_upper_bound(text))
        result['payload']=queue_payloads.preview(item.title,item.description,item.tags,['facebook','x'],ick=True)
        result['errors']=[]
    except ValueError as exc:result['errors']=[str(exc)]
    return result


async def read_destinations(page,targets):
    identity,accounts=await upload_post._native_page_identity(page,profile_name='TheIckRoom')
    native={}
    for target in targets:
        network=target.split(':')[1];value=accounts.get(network)
        if (not isinstance(value,dict) or value.get('reauth_required') is True
                or not isinstance(value.get('username'),str) or not value['username'].strip()):
            fail('fresh native account identity unavailable; no account settings changed')
        native[network]={k:value.get(k) for k in ('username','handle','id','page_id')}
    return {'page':identity,'native_accounts':native}


def destinations(page,targets):return asyncio.run(read_destinations(page,targets))


async def identity_for(s,item):
    row=guard(s,item)
    if row:
        doc=row.data['document']
        return await read_destinations(doc['connected_identities']['page']['facebook_page_id'],doc['targets'])


def document(s,item,req):
    pending(s,item,req.expected_fingerprint);reviewed=approval(s,item,req.approval_receipt_id,initial=True)
    media=s.get(MetadataMutationReceipt,req.media_receipt_id)
    if (not media or media.queue_identity!=journal.identity(item) or media.data.get('scope')!='ick-media-v1'
            or media.data['fingerprint']!=req.expected_fingerprint
            or media.data['approval_receipt_id']!=reviewed.operation_id):fail('exact current inspected media/approval receipt required')
    proof=media.data['proof'];verify_remote(item,proof);verify_local(item,proof)
    connected=asyncio.run(upload_post.list_accounts())
    targets=sorted(upload_post.expand_targets(item.accounts,connected))
    if (targets!=sorted(req.destination_ids) or len(targets)!=len(set(targets))
            or any(a not in {'TheIckRoom:facebook','TheIckRoom:x'} for a in targets)):
        fail('freeze exactly the current supported ICK destinations; no wildcard/YouTube expansion')
    identities=destinations(req.facebook_page_id,targets)
    selected=variant(s,item,req.x_variant_receipt_id,reviewed)
    try:payload=queue_payloads.preview(item.title,item.description,item.tags,[a.split(':')[1] for a in targets],ick=True,
        x_variant_text=selected.data['variant']['text'] if selected else None)
    except ValueError as exc:fail(str(exc))
    cfg=schedule(s);now=leases.clock(s);occupied=[]
    for other in s.query(QueueItem).filter(QueueItem.status.in_(['ready','posting','retry','error','posted','archived'])).all():
        if qm.pipeline_group(other)!=qm.ICK_ROOM:continue
        if other.status=='ready' and not other.scheduled_at:fail('existing ICK Ready items need stable cadence assignments first; no replan performed here')
        if other.scheduled_at:occupied.append(qm._aware(other.scheduled_at))
    occupied += [dt.datetime.fromisoformat(r.data['document']['slot']) for r in reservations(s)]
    after=max([now+dt.timedelta(minutes=1),*occupied])
    candidates=qm.slots_after(after,50,pipeline=qm.ICK_ROOM,accounts=item.accounts)
    slots=[v for v in candidates if v>after and qm.fits_slot(item,qm.ICK_ROOM,v)]
    if not slots:fail('no verified strictly future cadence-compatible slot; existing schedule left unchanged')
    return {'version':VERSION,'queue_identity':journal.identity(item),'fingerprint':req.expected_fingerprint,
        'approval_receipt_id':reviewed.operation_id,'approval_sha256':pm.digest(reviewed.data),
        'x_variant_receipt_id':selected.operation_id if selected else None,'x_variant_receipt_sha256':pm.digest(selected.data) if selected else None,
        'media_receipt_id':media.operation_id,'media_sha256':proof['actual']['sha256'],
        'targets':targets,'connected_identities':identities,'payload':payload,'schedule':cfg,'slot':slots[0].isoformat(),
        'expires_at':(slots[0]+dt.timedelta(minutes=5)).isoformat(),
        'rights_notice':'Technical decode and recorded metadata context do not independently certify rights or full semantic review'}


@router.post('/items/{qid}/preview')
def preview(qid:int,req:Preview,s:Session=Depends(get_session)):
    doc=document(s,s.get(QueueItem,qid,populate_existing=True),req)
    return {'document':doc,'preview_sha256':pm.digest(doc),'writes':0,'automatic_approval':False}


@router.post('/items/{qid}/ready')
def ready(qid:int,req:Ready,s:Session=Depends(get_session)):
    hashed=pm.digest([VERSION,qid,req.model_dump(exclude={'operation_id'})])
    if replay:=pm._replay(s,req.operation_id,hashed):return replay
    s.rollback()
    try:
        with leases.scope(f'publication:{qid}',session=s):
            from .file_use import pin
            item=s.get(QueueItem,qid,populate_existing=True)
            if item is None:fail('queue identity was deleted')
            paths=[Path(item.video_path)] if item.video_path and Path(item.video_path).is_file() else []
            thumb_path=thumbnail_path(item)
            if thumb_path:paths.append(thumb_path)
            s.rollback()
            with pin(paths),rollback_on_error(s):
                lock(s)
                if replay:=pm._replay(s,req.operation_id,hashed):s.rollback();return replay
                item=s.get(QueueItem,qid,populate_existing=True);doc=document(s,item,req)
                if pm.digest(doc)!=req.acknowledged_preview_sha256:fail('copy/media/approval/targets/cadence/reserved slot changed; preview again')
                before=pm.view(s,item);slot=dt.datetime.fromisoformat(doc['slot'])
                n=s.execute(update(QueueItem).where(*pm._same_snapshot(item)).values(status='ready',scheduled_at=slot),
                    execution_options={'synchronize_session':False}).rowcount
                if n!=1:fail('queue changed at atomic Ready transition')
                s.refresh(item);response=save(s,req.operation_id,hashed,item,VERSION,
                    {'document':doc,'before':before,'after':pm.view(s,item),'contract':pm.digest(contract(s,item))},
                    {'status':'ready','scheduled_at':doc['slot'],'future_only':True,'poster_woken':False})
                s.commit();return response
    except leases.LeaseLost:s.rollback();fail('publisher already owns this item; inspect before retrying')


def admit(s,item,path,*,regular,claimed=False,fresh_identity=None):
    row=guard(s,item)
    if not row:return None
    if not claimed:pm.require_pending(s,item)
    doc=row.data['document'];now=leases.clock(s)
    if (not regular or not dt.datetime.fromisoformat(doc['slot'])<=now<dt.datetime.fromisoformat(doc['expires_at'])
            or item.status!=('posting' if claimed else 'ready') or pm.digest(contract(s,item))!=row.data['contract']):
        fail('exact guarded future cadence release is stale, manual, missed or changed; no catch-up')
    if schedule(s)!=doc['schedule']:fail('cadence changed after approval; review the original reservation')
    if (fresh_identity if fresh_identity is not None else destinations(doc['connected_identities']['page']['facebook_page_id'],doc['targets']))!=doc['connected_identities']:
        fail('native destination identity or Page pin changed after approval')
    reviewed=approval(s,item,doc['approval_receipt_id'])
    if pm.digest(reviewed.data)!=doc['approval_sha256']:fail('approval changed')
    from . import drama_media as dm
    media=s.get(MetadataMutationReceipt,doc['media_receipt_id']);proof=media.data['proof']
    verify_remote(item,proof)
    if dm.file_hash(path)!=doc['media_sha256']:fail('transport bytes differ from reviewed source')
    verify_thumbnail(item,proof)
    selected=variant(s,item,doc['x_variant_receipt_id'],reviewed)
    if (pm.digest(selected.data) if selected else None)!=doc['x_variant_receipt_sha256']:fail('X variant approval changed')
    fresh=queue_payloads.preview(item.title,item.description,item.tags,[a.split(':')[1] for a in doc['targets']],ick=True,
        x_variant_text=selected.data['variant']['text'] if selected else None)
    if fresh!=doc['payload']:fail('exact platform payload changed')
    return doc


def guard_intent(s,item,path,entry,*,regular,fresh_identity=None):
    doc=admit(s,item,path,regular=regular,claimed=True,fresh_identity=fresh_identity)
    if not doc:return
    account=entry.get('profile','')+':'+(entry.get('platforms') or [''])[0]
    if account not in doc['targets']:fail('transport destination differs from exact approval')
    prefix='facebook_' if account.endswith(':facebook') else 'x_'
    expected={k:v for k,v in doc['payload']['values'].items() if k.startswith(prefix)}
    if entry.get('visible_payload')!=expected:fail('actual transport copy differs from exact approved platform preview')
