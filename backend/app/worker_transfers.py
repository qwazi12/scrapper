"""Claimed, renewable residential transfers. No download or remote IO here."""
from __future__ import annotations
import uuid
from urllib.parse import urlsplit, parse_qs

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import worker_leases as leases
from .auth import require_token
from .db import get_session
from .models import Clip, Status, WorkerTransfer

router=APIRouter(prefix="/api",dependencies=[Depends(require_token)])
TRANSFER_TTL=300


class ClaimIn(BaseModel):
    worker_id: str = Field(min_length=8,max_length=80,pattern=r'^[a-zA-Z0-9_-]+$')


class RenewIn(ClaimIn):
    generation: int = Field(ge=1)


def pending(clip):
    return clip and clip.status==Status.failed and not clip.file_path and (
        (clip.error or '').startswith('Transferred to Mac worker') or 'queued for the home Mac worker' in (clip.error or ''))


def handle(row): return leases.Handle(f"clip:{row.clip_id}",row.id,row.generation)


def get_transfer(s, token, worker_id, generation, *, allow_done=False):
    row=s.get(WorkerTransfer,token)
    if not row or row.worker_id != worker_id or row.generation != generation:
        raise HTTPException(409,"Worker claim does not match; collect a fresh claim")
    if row.status=='done' and allow_done: return row
    if row.status!='claimed': raise HTTPException(409,"Worker claim is no longer active")
    try: leases.guard(s,handle(row))
    except leases.LeaseLost: raise HTTPException(409,"Worker lease expired or was superseded; stale media was not promoted")
    return row


@router.post('/clips/{clip_id}/worker-claim')
def claim(clip_id:int,req:ClaimIn,s:Session=Depends(get_session)):
    token=str(uuid.uuid4())
    try:
        h=leases.acquire(s,f'clip:{clip_id}',token,ttl=TRANSFER_TTL)
        clip=s.get(Clip,clip_id,populate_existing=True)
        if not pending(clip): raise HTTPException(409,'Clip is not awaiting a residential worker')
        for previous in s.query(WorkerTransfer).filter_by(clip_id=clip_id,status='claimed').all(): previous.status='expired'
        row=WorkerTransfer(id=token,clip_id=clip_id,worker_id=req.worker_id,generation=h.generation)
        s.add(row);s.commit()
    except leases.LeaseLost:
        raise HTTPException(409,'Another worker owns this clip; wait for completion or expiry')
    return dict(token=row.id,generation=row.generation,clip_id=clip_id,source_url=clip.source_url,
                protocol=2,renew_after_seconds=30,lease_seconds=TRANSFER_TTL)


@router.post('/worker-transfers/{token}/renew')
def renew(token:str,req:RenewIn,s:Session=Depends(get_session)):
    row=get_transfer(s,token,req.worker_id,req.generation)
    if not leases.renew(s,handle(row),ttl=TRANSFER_TTL): raise HTTPException(409,'Worker claim expired')
    s.commit();return dict(active=True,lease_seconds=TRANSFER_TTL)


@router.get('/worker-transfers/{token}')
def receipt(token:str,s:Session=Depends(get_session)):
    row=s.get(WorkerTransfer,token)
    if not row: raise HTTPException(404,'Worker transfer not found')
    active=row.status=='claimed' and leases.live(f'clip:{row.clip_id}')
    return dict(token=row.id,generation=row.generation,clip_id=row.clip_id,
                status=row.status if row.status!='claimed' or active else 'expired',receipt=row.receipt)


@router.post('/worker-transfers/{token}/release')
def release(token:str,req:RenewIn,s:Session=Depends(get_session)):
    row=get_transfer(s,token,req.worker_id,req.generation)
    row.status='released';leases.release(s,handle(row));s.commit();return dict(released=True)


def same_media(a,b):
    def key(url):
        p=urlsplit(url or '');host=(p.hostname or '').lower().removeprefix('www.')
        if host in ('youtube.com','m.youtube.com','youtu.be'):
            vid=p.path.strip('/').split('/')[-1] if host=='youtu.be' or p.path.startswith(('/shorts/','/embed/')) else parse_qs(p.query).get('v',[''])[0]
            if vid:return ('youtube',vid)
        return (p.scheme,host,p.path.rstrip('/'),p.query)
    return key(a)==key(b)
