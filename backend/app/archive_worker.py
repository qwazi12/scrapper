"""Generation-fenced Mac handoff. The Mac sends media, never Drive credentials."""
import datetime as dt
import json
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import channel_archive as archive, channel_sources as source, worker_leases as leases
from .auth import require_token
from .config import settings
from .db import SessionLocal
from .models import ArchiveChannel, ArchiveVideo, ArchiveWorkerClaim
from .worker_transfers import ClaimIn, RenewIn

router=APIRouter(prefix='/api/archive-workers',dependencies=[Depends(require_token)])
TTL=300


class LocalInventoryIn(BaseModel):
    channel_id: str = Field(pattern=r'^UC[a-zA-Z0-9_-]{22}$')
    title: str = Field(min_length=1,max_length=1000)
    handle: str | None = None
    source_url: str
    parent_folder_id: str
    formats: list[str]
    complete: bool
    warnings: list[str] = Field(default_factory=list)
    videos: list[dict] = Field(max_length=100000)


def handle(row):return leases.Handle('archive:'+row.channel_id,row.id,row.generation)


def claim_row(s,token,worker_id,generation):
    row=s.get(ArchiveWorkerClaim,token)
    if not row or row.worker_id!=worker_id or row.generation!=generation or row.status!='claimed':
        raise HTTPException(409,'Archive worker claim no longer matches')
    try:leases.guard(s,handle(row))
    except leases.LeaseLost as exc:raise HTTPException(409,str(exc))
    return row


@router.post('/inventory')
def local_inventory(req:LocalInventoryIn):
    data=req.model_dump(exclude={'parent_folder_id','formats'})
    try:return archive.store_inventory(data,req.parent_folder_id,req.formats)
    except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc))


@router.post('/channels/{cid}/claim')
def claim(cid:str,req:ClaimIn):
    token=str(uuid.uuid4())
    try:
        with SessionLocal() as s:
            h=leases.acquire(s,'archive:'+cid,token,ttl=TTL)
            channel=s.get(ArchiveChannel,cid)
            if not channel:raise HTTPException(404,'Archive channel not found')
            if not channel.inventory.get('complete'):raise HTTPException(409,'Complete inventory required first')
            for old in s.query(ArchiveWorkerClaim).filter_by(channel_id=cid,status='claimed'):old.status='expired'
            row=ArchiveWorkerClaim(id=token,channel_id=cid,worker_id=req.worker_id,generation=h.generation)
            s.add(row);channel.status='awaiting_worker';channel.error=None;s.commit()
            return {'token':token,'generation':h.generation,'channel_id':cid,'max_bytes':settings.max_upload_bytes,
                'renew_after_seconds':30,'lease_seconds':TTL,'protocol':1}
    except leases.LeaseLost as exc:raise HTTPException(409,str(exc))


@router.post('/{token}/renew')
def renew(token:str,req:RenewIn):
    with SessionLocal() as s:
        row=claim_row(s,token,req.worker_id,req.generation)
        if not leases.renew(s,handle(row),ttl=TTL):raise HTTPException(409,'Archive claim expired')
        s.commit();return {'active':True}


@router.get('/{token}')
def receipt(token:str):
    with SessionLocal() as s:
        row=s.get(ArchiveWorkerClaim,token)
        if not row:raise HTTPException(404,'Archive claim not found')
        return {'status':row.status if row.status!='claimed' or leases.live('archive:'+row.channel_id) else 'expired',
            'channel_id':row.channel_id,'generation':row.generation,'receipt':row.receipt}


@router.get('/{token}/pending')
def pending(token:str,worker_id:str,generation:int):
    with SessionLocal() as s:
        row=claim_row(s,token,worker_id,generation);channel=s.get(ArchiveChannel,row.channel_id)
        videos=s.query(ArchiveVideo).filter(ArchiveVideo.channel_id==row.channel_id,
            ArchiveVideo.status.notin_(('uploaded','unavailable','restricted')),
            ArchiveVideo.seen_inventory==channel.inventory['revision']).order_by(ArchiveVideo.video_id).limit(1).all()
        s.commit()
        return {'items':[archive.serialize(v) for v in videos]}


@router.post('/{token}/videos/{vid}')
async def receive(token:str,vid:str,worker_id:str=Form(...),generation:int=Form(...),
                  metadata:str=Form(...),file:UploadFile=File(...)):
    from .file_use import pin
    from .uploads import save_video
    from .drive_sync import get_drive_service,find_or_create_subfolder
    if len(metadata)>65536:raise HTTPException(413,'Worker metadata is too large')
    try:info=json.loads(metadata)
    except ValueError:raise HTTPException(422,'Invalid media metadata')
    with SessionLocal() as s:
        claim=claim_row(s,token,worker_id,generation);h=handle(claim);cid=claim.channel_id
        row=s.get(ArchiveVideo,(cid,vid));channel=s.get(ArchiveChannel,cid)
        if not row or info.get('id')!=vid or info.get('channel_id')!=cid:raise HTTPException(409,'Media identity does not match its archive entry')
        if info.get('is_live') or info.get('live_status') in ('is_live','is_upcoming'):raise HTTPException(409,'Live/upcoming media cannot be archived')
        if row.status=='uploaded':
            return {'verified':True,**archive.serialize(row)}
        folder,parent,title=channel.drive_folder_id,channel.parent_folder_id,channel.title
        old_hash=row.sha256;has_remote=bool(row.drive_file_id or row.status in ('creating','ambiguous'))
        s.commit()
    destination=archive.root()/cid/vid/'residential.mp4'
    with leases.bind(h),pin([destination.parent],exclusive=True):
        service=await run_in_threadpool(get_drive_service)
        cap=await run_in_threadpool(archive.capacity,service,cid)
        if cap['local_free_bytes']<settings.max_upload_bytes+archive.RESERVE:raise HTTPException(507,'Server temporary storage reserve reached')
        # An exclusive unique inbound path cannot overwrite retained unfinished bytes.
        destination.parent.mkdir(parents=True,exist_ok=True)
        incoming=destination.with_name(uuid.uuid4().hex+'.mp4')
        try:
            await save_video(file,incoming)
            sha,_,_=archive.hashes(incoming)
            if has_remote and old_hash and sha!=old_hash:raise HTTPException(409,'Worker bytes differ from a retained remote attempt; preserve both for reconciliation')
            await run_in_threadpool(archive.save_download,cid,vid,incoming,info)
            if not folder:
                import re
                folder=await run_in_threadpool(find_or_create_subfolder,service,re.sub(r'[/\\]','_',title)[:100]+' — '+cid,parent)
                archive.persist(cid,drive_folder_id=folder)
            await run_in_threadpool(archive.upload_one,service,cid,vid,folder)
            with SessionLocal() as s:
                row=s.get(ArchiveVideo,(cid,vid));return {'verified':row.status=='uploaded',**archive.serialize(row)}
        except leases.LeaseLost as exc:raise HTTPException(409,str(exc))
        except Exception as exc:
            # A saved validated local copy remains; failed/unknown remote intent survives.
            try:
                with SessionLocal() as s:state=s.get(ArchiveVideo,(cid,vid)).status
                archive.persist(cid,vid,status=state if state in ('creating','ambiguous') else 'failed',error=str(exc)[-2000:])
                archive.persist(cid,error=str(exc)[-2000:])
            except leases.LeaseLost:pass
            raise


@router.post('/{token}/finish')
def finish(token:str,req:RenewIn):
    from .drive_sync import get_drive_service
    with SessionLocal() as s:
        row=claim_row(s,token,req.worker_id,req.generation);h=handle(row);cid=row.channel_id;s.commit()
    with leases.bind(h):
        result=archive.view(cid)
        archive.persist(cid,status='complete' if result['counts']['remaining']==0 else 'partial')
        folder=result['drive_folder_id']
        if folder:archive.write_manifest(get_drive_service(),cid,folder)
        with SessionLocal() as s:
            row=claim_row(s,token,req.worker_id,req.generation)
            row.status='done';row.receipt=result['counts'];s.flush();leases.release(s,h);s.commit()
    return {'counts':result['counts']}


@router.post('/{token}/release')
def release(token:str,req:RenewIn):
    with SessionLocal() as s:
        row=claim_row(s,token,req.worker_id,req.generation)
        row.status='released';leases.release(s,handle(row))
        channel=s.get(ArchiveChannel,row.channel_id);channel.status='stopped';s.commit()
    return {'released':True}


class FailureIn(RenewIn):
    reason: str = Field(min_length=1,max_length=2000)
    restricted: bool = False
    halt: bool = True


@router.post('/{token}/videos/{vid}/failure')
def failure(token:str,vid:str,req:FailureIn):
    with SessionLocal() as s:
        row=claim_row(s,token,req.worker_id,req.generation)
        video=s.get(ArchiveVideo,(row.channel_id,vid))
        if not video:raise HTTPException(404,'Archive video not found')
        if video.status=='uploaded':raise HTTPException(409,'Verified upload cannot become a failure')
        # Preserve ambiguous remote creation outcomes during any worker failure.
        if video.status not in ('creating','ambiguous'):
            video.status='awaiting_worker' if req.halt and req.restricted else ('restricted' if req.restricted else 'failed')
        video.error=req.reason
        s.get(ArchiveChannel,row.channel_id).error=req.reason
        s.commit();return {'recorded':True}
