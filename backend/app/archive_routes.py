"""Owner-controlled archival UI API. Archives never enter SocialPilot."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from .auth import require_token
from . import channel_archive as archive, channel_sources, resume
from .db import SessionLocal
from .models import ArchiveChannel

router=APIRouter(prefix='/api/channel-archives',dependencies=[Depends(require_token)])


class InventoryIn(BaseModel):
    url: str
    parent_folder_id: str = '1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo'
    formats: list[Literal['shorts','videos','streams']] = Field(default_factory=lambda:['shorts'],min_length=1)


class RunIn(BaseModel):
    retry_failures: bool = False


def inventory_job(params,rid=None):
    return resume.tracked_thread('archive_inventory','Inventory '+params['url'],'archive',params,
        lambda:archive.discover(params['url'],params['parent_folder_id'],params['formats']),rid=rid)


def archive_job(cid,retry=False,rid=None):
    return resume.tracked_thread('channel_archive','Archive '+cid,'archive',{'channel_id':cid,'retry':retry},
        lambda:archive.run(cid,retry),rid=rid,ref=cid)


resume.launcher('archive_inventory')(lambda p,cp,rid:inventory_job(p,rid))
resume.launcher('channel_archive')(lambda p,cp,rid:archive_job(p['channel_id'],p.get('retry',False),rid))


@router.get('')
def list_channels():
    with SessionLocal() as s:ids=[c.channel_id for c in s.query(ArchiveChannel).order_by(ArchiveChannel.title)]
    return {'channels':[archive.view(cid,limit=0) for cid in ids]}


@router.post('/inventory')
def inventory_channel(req:InventoryIn):
    try:req.url=channel_sources.canonical_input(req.url)
    except ValueError as exc:raise HTTPException(422,str(exc))
    return {'job_id':inventory_job(req.model_dump())}


@router.get('/{cid}')
def detail(cid:str,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500)):
    try:return archive.view(cid,offset,limit)
    except ValueError as exc:raise HTTPException(404,str(exc))


@router.get('/{cid}/capacity')
def capacity(cid:str):
    from .drive_sync import get_drive_service
    try:return archive.capacity(get_drive_service(),cid)
    except ValueError as exc:raise HTTPException(409,str(exc))


@router.get('/{cid}/manifest')
def manifest(cid:str):
    try:return archive.manifest(cid)
    except ValueError as exc:raise HTTPException(404,str(exc))


@router.post('/{cid}/run')
def run(cid:str,req:RunIn):
    try:archive.view(cid,limit=0)
    except ValueError as exc:raise HTTPException(404,str(exc))
    try:return {'job_id':archive_job(cid,req.retry_failures)}
    except resume.leases.LeaseLost as exc:raise HTTPException(409,str(exc))


@router.post('/{cid}/stop')
def stop(cid:str):
    from . import control,worker_leases
    try:archive.view(cid,limit=0)
    except ValueError as exc:raise HTTPException(404,str(exc))
    control.stop_jobs_for('archive',cid)
    with SessionLocal() as s:
        worker_leases.revoke(s,'archive:'+cid);s.commit()
    archive.persist(cid,status='stopped',error='Stopped by owner; verified copies and unfinished local files retained')
    return {'stopped':True}


@router.post('/{cid}/handoff')
def handoff(cid:str):
    try:
        current=archive.view(cid,limit=0)
        if not current['inventory']['complete']:raise HTTPException(409,'Complete inventory required before Mac handoff')
    except ValueError as exc:raise HTTPException(404,str(exc))
    stop(cid)
    archive.persist(cid,status='awaiting_worker',error=None)
    return {'awaiting_worker':True,'channel_id':cid}
