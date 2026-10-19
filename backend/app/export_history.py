"""Owner-facing retained export inspection, downloads and draft recovery."""
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel,ConfigDict,Field,StrictBool

from .auth import require_token
from .config import settings
from .db import SessionLocal
from .export_versions import data, identity, kind, retain
from .file_response import FileResponse
from .models import CountdownProject, ExportVersion, QueueItem, StudioProject
from .publication_journal import digest

router=APIRouter(prefix='/api/export-history',dependencies=[Depends(require_token)])
Kind=Literal['studio','countdown']


def project(s,kind,pid):
    row=s.get(StudioProject if kind=='studio' else CountdownProject,pid)
    if not row:raise HTTPException(404,'Project no longer exists; retained exports remain in the history archive')
    return row


def version(p):return digest([identity(p),data(p),p.queue_item_id])


@contextmanager
def ownership(s,p):
    if isinstance(p,StudioProject):
        from .file_use import pin
        claim=pin([settings.data_path/'studio'/str(p.id)],exclusive=True,session=s)
    else:
        from .studio.countdown import media_use
        claim=media_use(p.id,exclusive=True,session=s)
    with claim:yield


@router.get('')
def versions(kind: Kind | None=None,project_id:int | None=None,limit:int=Query(50,ge=1,le=100)):
    with SessionLocal() as s:
        q=s.query(ExportVersion)
        current=None
        if kind:q=q.filter_by(kind=kind)
        if project_id is not None:
            if not kind:raise HTTPException(422,'Choose the studio for this project')
            current=s.get(StudioProject if kind=='studio' else CountdownProject,project_id)
            q=q.filter_by(project_id=project_id)
            if current:q=q.filter_by(project_identity=identity(current))
        return {'project_version':version(current) if current else None,'versions':[
            {'id':r.id,'kind':r.kind,'project_id':r.project_id,'created_at':r.created_at,
             'title':r.manifest['title'],'project_created_at':r.manifest['project_created_at'],
             'approval_recorded':bool(r.manifest['snapshot'].get('approval')),
             'assets':[{k:v for k,v in a.items() if k not in ('source_path','blob')} for a in r.manifest['assets']]}
            for r in q.order_by(ExportVersion.created_at.desc()).limit(limit)]}


@router.post('/{kind}/{pid}/capture')
def capture(kind:Kind,pid:int):
    with SessionLocal() as s:
        p=project(s,kind,pid)
        with ownership(s,p):
            s.refresh(p);saved=retain(s,p);s.commit()
            return {'id':saved,'project_version':version(p)}


def get(s,eid):
    row=s.get(ExportVersion,eid)
    if not row:raise HTTPException(404,'Retained export not found')
    return row


@router.get('/{eid}')
def detail(eid:str):
    with SessionLocal() as s:
        row=get(s,eid)
        return {'id':row.id,'kind':row.kind,'project_id':row.project_id,'manifest':row.manifest,'created_at':row.created_at}


@router.get('/{eid}/file/{role}')
def retained_file(eid:str,role:Literal['video','thumbnail','motion']):
    from .studio.media_quality import file_hash
    with SessionLocal() as s:
        row=get(s,eid);asset=next((a for a in row.manifest['assets'] if a['role']==role),None)
        if not asset or not asset.get('available'):raise HTTPException(404,'This legacy version has no retained file')
        path=Path(asset['blob']).resolve()
        if (not path.is_relative_to((settings.data_path/'export_history'/'blobs').resolve())
                or not path.is_file() or file_hash(path)!=asset['sha256']):
            raise HTTPException(409,'Retained file failed its integrity check; this version was not served')
    return FileResponse(path,media_type='image/jpeg' if role=='thumbnail' else 'video/mp4')


class RestoreIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_project_version:str=Field(min_length=64,max_length=64)
    reviewed:StrictBool


@router.post('/{eid}/restore-draft')
def restore_draft(eid:str,req:RestoreIn):
    from .budget_reservations import lock
    from .worker_leases import live
    from . import undo,publication_journal
    from .studio.research_gate import invalidate
    with SessionLocal() as s:
        saved=get(s,eid);p=project(s,saved.kind,saved.project_id)
        with ownership(s,p):
            lock(s);s.refresh(p)
            if identity(p)!=saved.project_identity:raise HTTPException(409,'This export belongs to a different project identity')
            if not req.reviewed or version(p)!=req.expected_project_version:
                raise HTTPException(409,'Project changed; compare the retained and current drafts before restoring')
            resources=[f'{saved.kind}:{p.id}',f'drive_save:{p.id}' if saved.kind=='studio' else f'countdown_drive:{p.id}']
            if p.stage_status in ('running','queued','waiting') or any(live(r) for r in resources):
                raise HTTPException(409,'Stop the active project job before restoring a draft')
            queued=s.get(QueueItem,p.queue_item_id) if p.queue_item_id else None
            if queued and (queued.status not in ('posted','archived') or live(f'publication:{queued.id}')
                           or publication_journal.unresolved_late_receipt(s,queued)):
                raise HTTPException(409,'Remove the active queued export before restoring its project draft')
            restored=saved.manifest['snapshot']['inputs'].get('script')
            if not restored:raise HTTPException(409,'This version has no saved script')
            undo.record(s,f'{saved.kind}:{p.id}','Restore retained script draft',rows=[p],model=p.__tablename__)
            p.script=invalidate(restored,'Restored script: verify it against current sources before making new media') if saved.kind=='studio' else restored
            if saved.kind=='studio':p.plan=None
            p.render=None;p.review={k:v for k,v in (p.review or {}).items() if k!='export_approval'}
            p.stage_status='stopped';p.stage_message='Retained script restored as a draft; existing media history is preserved'
            s.commit()
            return {'restored':saved.id,'project_version':version(p)}
