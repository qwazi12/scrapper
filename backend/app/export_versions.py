"""Content-addressed retained media plus append-only original-studio snapshots.

The original path is never a retention guarantee: thumbnails and archive paths
are mutable. Independent, hash-verified copies survive project replacement.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .config import settings
from .models import CountdownProject, ExportVersion, StudioProject
from .publication_journal import digest, identity

FIELDS=('script','plan','facts','research','trailer','shots','ranking','segment_ids','target_minutes','fmt')


def kind(project):return 'studio' if isinstance(project,StudioProject) else 'countdown'


def root(project):
    if isinstance(project,StudioProject):return settings.data_path/'studio'/str(project.id)
    return settings.data_path


def data(project,prior=False):
    state=inspect(project)
    def value(name):
        history=state.attrs[name].history
        if prior and history.has_changes():
            return history.deleted[0] if history.deleted else None
        return getattr(project,name)
    render=copy.deepcopy(value('render') or {})
    render.pop('drive',None)  # Transfer progress does not create content versions.
    review=value('review') or {}
    from types import SimpleNamespace
    from .studio.duration_intent import requested
    inputs={k:copy.deepcopy(value(k)) for k in FIELDS if hasattr(project,k)}
    intent=requested(SimpleNamespace(**inputs,review=review))
    if intent is not None:inputs['duration_intent']=intent
    return {'render':render,'inputs':inputs,
            'approval':copy.deepcopy(review.get('export_approval'))}


def _blob(path,allowed):
    base=settings.data_path.resolve()
    path=Path(path).resolve()
    if not path.is_relative_to(Path(allowed).resolve()) or not path.is_file():
        return {'source_path':str(path),'available':False,'reason':'Missing or outside managed storage'}
    store=base/'export_history'/'blobs';store.mkdir(parents=True,exist_ok=True)
    from .studio.media_quality import file_hash
    expected=file_hash(path);blob=store/expected
    if blob.exists():
        if file_hash(blob)!=expected:raise HTTPException(409,'Retained export storage failed its integrity check; no version was replaced')
        return {'source_path':str(path),'blob':str(blob),'available':True,'sha256':expected,'bytes':blob.stat().st_size}
    if shutil.disk_usage(store).free < path.stat().st_size+1024*1024:
        raise HTTPException(409,'Not enough disk space to retain the current export; free cache space before replacing it')
    temp=store/('.copy-'+uuid4().hex)
    try:
        hasher=hashlib.sha256();size=0
        with path.open('rb') as source,temp.open('xb') as dest:
            for chunk in iter(lambda:source.read(1024*1024),b''):
                dest.write(chunk);hasher.update(chunk);size+=len(chunk)
            dest.flush();os.fsync(dest.fileno())
        sha=hasher.hexdigest()
        if sha!=expected or file_hash(path)!=sha:
            raise HTTPException(409,'Export changed while retaining its history; current project was preserved')
        try:os.link(temp,blob)  # Atomic no-replace, including another process.
        except FileExistsError:
            if file_hash(blob)!=sha:raise HTTPException(409,'Retained export storage failed its integrity check; no version was replaced')
        directory=os.open(store,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        return {'source_path':str(path),'blob':str(blob),'available':True,'sha256':sha,'bytes':size}
    finally:temp.unlink(missing_ok=True)


def retain(s,project,payload=None):
    if not project.id or not project.created_at:return None
    payload=payload if payload is not None else data(project)
    if not payload['render']:return None
    ident=identity(project);pk=digest([kind(project),ident,payload])
    if s.get(ExportVersion,pk) or any(isinstance(r,ExportVersion) and r.id==pk for r in s.new):return pk
    from .budget_reservations import lock
    from . import worker_leases,file_use
    lock(s);worker_leases.guard(s);file_use.assert_current(s)
    if s.get(ExportVersion,pk):return pk
    render=payload['render'];assets=[]
    paths=[('video',render.get('file')),('thumbnail',render.get('thumbnail'))]
    if (render.get('motion') or {}).get('file'):paths.append(('motion',render['motion']['file']))
    allowed=root(project)
    if isinstance(project,CountdownProject):
        allowed=settings.data_path/'studio'/'compilations'/str(render.get('compilation_id'))
    for role,raw in paths:
        if raw:assets.append({'role':role,**_blob(root(project)/raw,allowed)})
    worker_leases.guard(s);file_use.assert_current(s)
    manifest={'schema':1,'title':getattr(project,'title',None) or getattr(project,'topic',''),
              'project_created_at':str(project.created_at),'snapshot':payload,'assets':assets}
    s.add(ExportVersion(id=pk,kind=kind(project),project_id=project.id,project_identity=ident,
                        manifest=json.loads(json.dumps(manifest,default=str))))
    return pk


def prepare_studio_deletion(s,project):
    """Preserve settled queue readers; reject every active/uncertain consumer."""
    from .budget_reservations import lock
    from .file_refs import protected
    from .models import QueueItem
    from .social import enqueue
    from . import worker_leases,publication_journal
    lock(s);s.refresh(project)
    base=root(project).resolve();owned=None
    if any(project.id in (c.segment_ids or []) for c in s.query(CountdownProject)):
        raise HTTPException(409,'A countdown still owns this segment; remove that campaign before deleting its source project')
    def inside(raw):
        return bool(raw and '://' not in raw and (settings.data_path/str(raw)).resolve().is_relative_to(base))
    for item in s.query(QueueItem).all():
        direct=[k for k in ('video_path','thumb_path') if inside(getattr(item,k))]
        images=[v for v in item.media_files or [] if inside(v)]
        if item.id==project.queue_item_id and item.status in ('review','ready','retry','error'):
            enqueue.rewrite_version(s,item);owned=item
            continue
        if not direct and not images:continue
        if (item.status not in ('posted','archived') or worker_leases.live(f'publication:{item.id}')
                or publication_journal.unresolved_late_receipt(s,item)
                or any(r.get('state') in ('intent','unknown','accepted') for r in item.publish_requests or [])
                or (item.research or {}).get('native_export_id')):
            raise HTTPException(409,'Another active or unresolved queue item still needs this project media')
        relocation={}
        for raw in [*[getattr(item,k) for k in direct],*images]:
            asset=_blob(settings.data_path/str(raw),base)
            if not asset.get('available'):raise HTTPException(409,'A queued file could not be retained; current project was preserved')
            relocation[raw]=asset
        for key in direct:setattr(item,key,relocation[getattr(item,key)]['blob'])
        if images:item.media_files=[relocation[v]['blob'] if v in relocation else v for v in item.media_files]
        publication_journal.append(s,item.id,'retention','media_relocated',{'assets':relocation})
    ignored=[(StudioProject,project.id)]+([(QueueItem,owned.id)] if owned else [])
    if protected(s,base,ignore_rows=ignored):
        raise HTTPException(409,'Another project or asset still references this media; current project was preserved')
    return owned


@event.listens_for(Session,'before_flush')
def retain_changed_exports(s,context,instances):
    # Dependency snapshots use attribute history, preserving the *old* inputs
    # when a script/research edit invalidates its render in the same transaction.
    for project in tuple(s.dirty)+tuple(s.deleted):
        if not isinstance(project,(StudioProject,CountdownProject)):continue
        before,after=data(project,True),data(project)
        old,new=before['inputs'],after['inputs']
        def spoken(script):
            script=script or {}
            return ([(x.get('paragraph'),x.get('text')) for x in script.get('sentences',[])]
                    if isinstance(project,StudioProject) else [script.get('intro'),script.get('outro')])
        def meaningful(key,value):
            if key=='facts' and isinstance(value,dict):return {k:v for k,v in value.items() if k!='local'}
            if key=='shots' and isinstance(value,list):return [{k:v for k,v in x.items() if k!='look'} for x in value]
            if key=='trailer' and isinstance(value,dict):
                return {k:([{a:b for a,b in x.items() if a not in ('sha256','bytes')} for x in v] if k=='sources' else v)
                        for k,v in value.items() if k not in ('sha256','bytes')}
            return value
        source_changed=any(meaningful(k,old.get(k))!=meaningful(k,new.get(k)) for k in ('facts','research','trailer','shots'))
        old_sources=(old.get('trailer') or {}).get('sources',[])
        new_sources=(new.get('trailer') or {}).get('sources',[])
        source_changed=source_changed or any(a.get('sha256') and b.get('sha256') and a['sha256']!=b['sha256']
                                              for a,b in zip(old_sources,new_sources))
        changed=(source_changed or spoken(old.get('script'))!=spoken(new.get('script')) or any(
            old.get(k)!=new.get(k) for k in ('plan','ranking','segment_ids','target_minutes','fmt','duration_intent')))
        if isinstance(project,StudioProject) and source_changed:
            from .studio.research_gate import invalidate
            if old.get('script')==new.get('script'):
                project.script=invalidate(project.script,'Upstream evidence changed; verify the saved narration again')
            state=inspect(project)
            if (old.get('script') or before['render']) and not state.attrs.plan.history.has_changes():project.plan=None
            if not state.attrs.render.history.has_changes():project.render=None
            project.review={k:v for k,v in (project.review or {}).items() if k!='export_approval'}
            after=data(project)
        if changed and before['render'] and before['render']==after['render']:
            # Catch overlooked internal/undo paths as well as HTTP handlers.
            # A new input cannot inherit the previous rendered artifact.
            project.render=None
            project.review={k:v for k,v in (project.review or {}).items() if k!='export_approval'}
            after=data(project)
        if before!=after or project in s.deleted:
            if before['render']:retain(s,project,before)
            if after['render'] and project not in s.deleted:retain(s,project,after)
