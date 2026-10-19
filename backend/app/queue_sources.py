"""Immutable original-Studio sources; no provider requests or automatic approvals."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import String,cast,func,or_,select

from .config import settings
from .models import QueueSourceBinding,QueueItem,ExportVersion,StudioProject,CountdownProject
from .publication_journal import digest,identity
from .worker_leases import clock

POLICY='original-studio-source-v1'
SOURCES={'LongForm Studio','Countdowns Studio'}
COPY_FIELDS=('title','description','tags','pipeline','source','research','video_path','thumb_path','media_kind','media_files')


def fail(message):raise HTTPException(409,'Studio source: '+message)


def head(s,item):
    if not item.id or not item.created_at:return None
    return s.query(QueueSourceBinding).filter_by(queue_identity=identity(item)).order_by(QueueSourceBinding.revision.desc()).first()


def controlled(s,item):
    return bool((item.research or {}).get('native_export_id') or head(s,item) or item.source in SOURCES)


def require_copy_editable(s,item):
    if controlled(s,item):fail('content is bound to its reviewed export; revise and resend from the original studio')


def transport(item):return {key:deepcopy(getattr(item,key)) for key in COPY_FIELDS}


def _asset(row,role,required=True):
    found=[asset for asset in row.manifest.get('assets',[]) if asset.get('role')==role]
    if not found and not required:return None
    if len(found)!=1 or not found[0].get('available'):fail(f'retained {role} is missing; recover the exact export before sending')
    asset=found[0];path=Path(asset['blob']).resolve();base=(settings.data_path/'export_history'/'blobs').resolve()
    if not path.is_relative_to(base) or path.name!=asset['sha256'] or not path.is_file():fail(f'retained {role} is missing or outside immutable storage')
    return {**asset,'blob':str(path)}


def retained(s,eid,*,approve):
    from .studio.media_quality import export_fingerprint,file_hash
    from .studio.export_acceptance import require_view_acceptance,view
    from .studio.research_gate import require_checked
    row=s.get(ExportVersion,eid)
    if not row or row.kind not in ('studio','countdown'):fail('retained export identity is missing')
    snapshot=row.manifest.get('snapshot') or {}
    if digest([row.kind,row.project_identity,snapshot])!=row.id:fail('retained input identity failed verification')
    inputs=deepcopy(snapshot.get('inputs') or {});render=deepcopy(snapshot.get('render') or {})
    if not row.manifest.get('project_created_at'):fail('legacy source identity is unconfirmed')
    try:
        source_stamp=datetime.fromisoformat(row.manifest['project_created_at'])
        if identity(SimpleNamespace(id=row.project_id,created_at=source_stamp))!=row.project_identity:fail('retained project identity changed')
    except (TypeError,ValueError):fail('retained project creation identity is unconfirmed')
    if row.kind=='studio':require_checked(inputs.get('script'),inputs.get('facts'))
    video=_asset(row,'video');cover=_asset(row,'thumbnail',required=bool(render.get('thumbnail')))
    if (render.get('quality') or {}).get('sha256')!=video['sha256']:fail('video inspection does not match retained media')
    render['file']=Path(video['blob']).name
    if cover:
        if file_hash(Path(cover['blob']))!=cover['sha256']:fail('retained cover checksum changed')
        render['thumbnail']=Path(cover['blob']).name
    project=SimpleNamespace(**inputs,render=render,review={'export_approval':snapshot.get('approval')},
        created_at=row.manifest['project_created_at'],topic=row.manifest.get('title',''))
    root=Path(video['blob']).parent
    fingerprint=export_fingerprint(project,root)
    if approve:require_view_acceptance(view(project,root),snapshot.get('approval') or {})
    return row,project,{'video':video,'cover':cover},fingerprint


def _countdown(s,project,segments):
    from .studio import countdown_eligibility as eligibility
    ranking=project.ranking or {};proof=ranking.get('eligibility') or {}
    if ranking.get('contract_version')!=1:fail('legacy countdown needs current title eligibility before a new delivery')
    rule=eligibility.criteria(project.topic,settings.studio_region)
    try:
        checked=datetime.fromisoformat(proof.get('checked_at',''))
        if checked.tzinfo is None:raise ValueError('unknown evidence timezone')
        age=(clock(s)-checked.astimezone(timezone.utc)).total_seconds()
    except (TypeError,ValueError):age=-1
    if proof.get('criteria')!=rule or not 0<=age<=(86400 if rule['providers'] else 7*86400):fail('countdown category evidence expired or changed; recheck eligibility')
    records={(r.get('media_type'),r.get('tmdb_id')):r for r in proof.get('records',[])}
    if set(map(str,project.segment_ids or []))!=set(segments):fail('countdown segment identities are incomplete')
    expected=(project.render or {}).get('segment_fingerprints') or {}
    if set(expected)!=set(segments):fail('final countdown does not bind every retained segment')
    entries=ranking.get('entries') or []
    if {str(e.get('segment_id')) for e in entries}!=set(segments):fail('countdown ranking does not bind every segment')
    for entry in entries:
        key=str(entry['segment_id']);row,p,media,fingerprint=segments[key]
        if row.kind!='studio' or str(row.project_id)!=key or fingerprint!=expected[key]:fail('retained segment differs from the accepted final countdown')
        record=records.get((entry.get('media_type') or 'movie',entry.get('tmdb_id')))
        if not record:fail('countdown category record is missing')
        issues=eligibility.evaluate(entry,p.facts or {},rule,record.get('providers'))
        if issues:fail('countdown eligibility no longer matches: '+'; '.join(issues[:5]))


def bundle(s,main_id,segment_ids):
    main=retained(s,main_id,approve=True)
    segments={}
    for eid in segment_ids:
        value=retained(s,eid,approve=False);key=str(value[0].project_id)
        if key in segments:fail('duplicate retained segment identity')
        segments[key]=value
    if main[0].kind=='countdown':_countdown(s,main[1],segments)
    elif segments:fail('unexpected countdown segments in a LongForm source')
    return main,segments



def prepare_send(s,project,item):
    """Do not adopt an identity-unknown numeric legacy queue link."""
    if not item:return None
    from .social.enqueue import rewrite_version
    from .export_versions import kind
    rewrite_version(s,item)
    previous=head(s,item)
    if previous:
        source=s.get(ExportVersion,previous.data.get('source_version'))
        if not source or source.kind!=kind(project) or source.project_identity!=identity(project):
            fail('queue link belongs to another source identity; preserve both records and review the link')
        return item
    if item.source in SOURCES:
        item.status='review';item.scheduled_at=None
        item.notes=((item.notes or '')+'\nLegacy source is unbound; original words retained while a reviewed replacement is sent.').strip()
    project.queue_item_id=None;s.flush()
    return None


def bind(s,item,project):
    """Called only by a reviewed Studio send, in its existing transaction/claims."""
    from .budget_reservations import lock
    from .export_versions import retain
    lock(s);s.flush()
    main_id=retain(s,project)
    if not main_id:fail('retain a complete reviewed export before sending')
    segments=[]
    if isinstance(project,CountdownProject):
        for pid in project.segment_ids or []:
            p=s.get(StudioProject,pid)
            if not p:fail('a countdown source segment is missing')
            eid=retain(s,p)
            if not eid:fail('a countdown source segment has no retained export')
            segments.append(eid)
    s.flush()
    try:main,_=bundle(s,main_id,segments)
    except RuntimeError as exc:fail(str(exc))
    item.video_path=main[2]['video']['blob'];item.thumb_path=main[2]['cover']['blob'] if main[2]['cover'] else None
    item.media_kind='video';item.media_files=[]
    previous=head(s,item)
    if previous:
        source=s.get(ExportVersion,previous.data.get('source_version'))
        if not source or source.kind!=main[0].kind or source.project_identity!=main[0].project_identity:fail('queue source identity changed; preserve the prior binding')
    payload={'policy':POLICY,'kind':main[0].kind,'source_version':main_id,'segments':segments,'transport':transport(item)}
    if previous and previous.data==payload:return previous
    revision=previous.revision+1 if previous else 1;who=identity(item)
    row=QueueSourceBinding(id=digest([who,revision,payload]),queue_id=item.id,queue_identity=who,revision=revision,data=payload,created_at=clock(s))
    s.add(row);s.flush();return row


def required(s,item):
    """Cheap admission before any fallback download; Native keeps its own gate."""
    row=head(s,item)
    if row:
        if row.queue_id!=item.id or digest([row.queue_identity,row.revision,row.data])!=row.id:fail('binding identity failed verification; preserve the evidence and review it')
        if row.data.get('policy')!=POLICY:fail('this binding requires a compatible source-policy version')
        if row.data.get('transport')!=transport(item):fail('queue copy or media changed; resend the reviewed source from its studio')
        path=Path(item.video_path or '').resolve();base=(settings.data_path/'export_history'/'blobs').resolve()
        if not path.is_relative_to(base) or not path.is_file():fail('retained video is missing; recover its exact bytes before dispatch')
        return row
    if (item.research or {}).get('native_export_id'):return None
    linked=(s.query(StudioProject.id).filter_by(queue_item_id=item.id).first()
        or s.query(CountdownProject.id).filter_by(queue_item_id=item.id).first())
    if item.source in SOURCES or linked:fail('unbound legacy row; return to its original studio, recheck and send a reviewed version')
    return None


def verify(s,item):
    row=required(s,item)
    if not row:return None
    try:
        main,_=bundle(s,row.data['source_version'],row.data['segments'])
        if main[0].kind!=row.data['kind'] or item.video_path!=main[2]['video']['blob'] or item.thumb_path!=(main[2]['cover']['blob'] if main[2]['cover'] else None):
            fail('bound media differs from its retained source')
    except (KeyError,TypeError):fail('retained proof fields are incomplete; recover and review the original source')
    except RuntimeError as exc:fail(str(exc))
    return row


def admission_clause(item,expected):
    """The same final SQL claim must reject a source revision after validation."""
    import json
    latest=select(func.max(QueueSourceBinding.revision)).where(QueueSourceBinding.queue_identity==identity(item)).scalar_subquery()
    revision=latest==expected.revision if expected else latest.is_(None)
    research=cast(QueueItem.research,String)==json.dumps(item.research)
    if item.research is None:research=or_(QueueItem.research.is_(None),research)
    return revision & (QueueItem.source==item.source) & (QueueItem.pipeline==item.pipeline) & research
