"""Owner-only read inspection of queued source evidence; never a submission."""
from datetime import datetime,timezone

from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy.orm import Session

from .auth import require_token
from .db import get_session
from .file_use import pin
from .models import QueueItem,StudioProject,CountdownProject,ExportVersion
from .publication_journal import identity
from . import queue_sources

router=APIRouter(prefix='/api/queue',dependencies=[Depends(require_token)])


def utc(value):return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def source(s,eid):
    row=s.get(ExportVersion,eid) if isinstance(eid,str) else None
    if not row:return None
    snapshot=row.manifest.get('snapshot') or {};inputs=snapshot.get('inputs') or {}
    model=StudioProject if row.kind=='studio' else CountdownProject
    current=s.get(model,row.project_id)
    return {'id':row.id,'kind':row.kind,'title':row.manifest.get('title'),'project_id':row.project_id,
        'current_project_matches':bool(current and current.created_at and identity(current)==row.project_identity),
        'captured_at':utc(row.created_at).isoformat(),'source_created_at':row.manifest.get('project_created_at'),
        'script':inputs.get('script'),'facts':inputs.get('facts'),'approval':snapshot.get('approval'),
        'format':inputs.get('fmt') if isinstance(inputs.get('fmt'),str) else None,
        'requested_duration':inputs.get('duration_intent'),
        'assets':[{key:asset.get(key) for key in ('role','sha256','bytes','available')} for asset in row.manifest.get('assets',[])]}


@router.get('/{qid}/source-context')
def context(qid:int,expected_created_at:datetime|None=None,s:Session=Depends(get_session)):
    item=s.get(QueueItem,qid)
    if not item:raise HTTPException(404,'Queue item not found; retained source journals are preserved separately')
    if not item.created_at:raise HTTPException(409,'Queue creation identity is unconfirmed; recover it before inspection')
    if expected_created_at is not None and utc(expected_created_at)!=utc(item.created_at):
        raise HTTPException(409,'Queue identity changed; refresh the queue list before inspecting its source')
    result={'queue_id':item.id,'queue_created_at':utc(item.created_at).isoformat(),'queue_identity':identity(item),
        'binding':None,'source':None,'segments':[],
        'scope':'Read-only local stored-media/source checks. Fact truth, source completeness and legal permission are not independently certified.'}
    row=queue_sources.head(s,item)
    if not row:
        if (item.research or {}).get('native_export_id'):
            return {**result,'state':'native_contract','message':'This item uses its Native campaign export contract; inspect that exact campaign before release.'}
        try:queue_sources.required(s,item)
        except HTTPException as exc:return {**result,'state':'legacy_unbound','message':str(exc.detail)}
        return {**result,'state':'manual','message':'This manual/imported item has no original-Studio source binding; this view makes no research-verification claim.'}
    try:
        with pin([item.video_path,item.thumb_path],session=s):
            s.expire_all();item=s.get(QueueItem,qid,populate_existing=True)
            if not item or identity(item)!=result['queue_identity']:raise HTTPException(409,'Queue identity changed during inspection')
            row=queue_sources.verify(s,item)
            if not row:raise HTTPException(409,'Source binding changed during inspection')
            state,message='checks_passed','Current local source checks passed. Original narration, media and review evidence are retained; factual truth and rights remain owner-reviewed.'
    except HTTPException as exc:
        if not item or not item.created_at or identity(item)!=result['queue_identity']:
            raise HTTPException(409,'Queue identity changed during inspection; refresh the queue list') from exc
        state,message='blocked',str(exc.detail)
        row=queue_sources.head(s,item) if item else None
    if row:
        result['binding']={'id':row.id,'revision':row.revision,'kind':row.data.get('kind'),'captured_at':utc(row.created_at).isoformat()}
        result['source']=source(s,row.data.get('source_version'))
        result['segments']=[value for eid in (row.data.get('segments') or [])[:15] if (value:=source(s,eid))]
    return {**result,'state':state,'message':message}
