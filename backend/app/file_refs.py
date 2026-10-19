"""Conservative database references that keep shared media out of retention."""
from pathlib import Path
from .models import Clip,Compilation,QueueItem,StudioProject,CountdownProject,CreativeExport,MediaAsset,ExportVersion
from .config import settings


def _paths(value,root):
    if isinstance(value,dict):
        for k,v in value.items():
            if k in ('file','file_path','video_path','thumb_path','thumbnail','still','output_path','blob') and isinstance(v,str) and v and '://' not in v:
                yield (root/v).resolve()
            else:yield from _paths(v,root)
    elif isinstance(value,list):
        for v in value:yield from _paths(v,root)


def protected(s,path,*,ignore=None,ignore_rows=()):
    target=Path(path).resolve()
    def matches(p):return target==p or p.is_relative_to(target)
    for model in (QueueItem,StudioProject,CountdownProject,CreativeExport,MediaAsset,Clip,Compilation,ExportVersion):
        for row in s.query(model):
            if ignore==(model,row.id) or (model,row.id) in ignore_rows:continue
            if model is StudioProject:
                root=settings.data_path/'studio'/str(row.id)
                values=[row.trailer,row.shots,row.plan,row.render]
            elif model is CountdownProject:root=settings.data_path;values=[row.render]
            elif model is CreativeExport:root=settings.data_path;values=[row.manifest]
            elif model is ExportVersion:root=settings.data_path;values=[{'assets':row.manifest.get('assets',[])}]
            elif model is MediaAsset:root=settings.data_path;values=[{'file':row.file_path}]
            elif model is QueueItem:root=settings.data_path;values=[{'file':row.video_path,'thumbnail':row.thumb_path},*[{'file':p} for p in row.media_files or []]]
            elif model is Clip:root=settings.data_path;values=[{'file':row.file_path,'thumbnail':row.thumb_path}]
            else:root=settings.data_path;values=[{'file':row.output_path}]
            if any(matches(p) for value in values for p in _paths(value,root)):return True
    return False


def clip_referenced(s,cid):
    return (s.query(QueueItem).filter(QueueItem.clip_id==cid).first() is not None
        or any(cid in (c.clip_ids or []) for c in s.query(Compilation))
        or any((p.trailer or {}).get('pending_clip_id')==cid for p in s.query(StudioProject)))
