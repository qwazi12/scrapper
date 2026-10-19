"""Resumable archive ledger. No QueueItem, destination, schedule or publication.

Every external create has durable intent. Unknown creates are reconciled by
appProperties; no-match ambiguity fails closed instead of making another copy.
"""
from __future__ import annotations
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

from . import channel_sources as source, control, worker_leases as leases
from .config import settings
from .db import SessionLocal
from .models import ArchiveChannel, ArchiveVideo

ALL={'supportsAllDrives':True}
ROOT_NAME='channel_archive'
RESERVE=256*1024*1024


def root():
    p=Path(settings.data_dir)/ROOT_NAME;p.mkdir(parents=True,exist_ok=True);return p.resolve()


def persist(cid, vid=None, **patch):
    with SessionLocal() as s:
        row=s.get(ArchiveVideo,(cid,vid)) if vid else s.get(ArchiveChannel,cid)
        if not row: raise ValueError('Archive entry not found')
        for key,value in patch.items():setattr(row,key,value)
        s.commit()


def view(cid, offset=0, limit=100):
    with SessionLocal() as s:
        c=s.get(ArchiveChannel,cid)
        if not c: raise ValueError('Archive channel not found')
        rows=s.query(ArchiveVideo).filter_by(channel_id=cid).order_by(ArchiveVideo.video_id).all()
        counts=Counter(r.status for r in rows)
        total=len(rows); verified=counts['uploaded']
        return {'channel_id':cid,'title':c.title,'handle':c.handle,'source_url':c.source_url,
            'formats':c.formats,'status':c.status,'error':c.error,'inventory':c.inventory,
            'drive_folder_id':c.drive_folder_id,'manifest_id':c.manifest_id,
            'active_job':next(({k:j.public()[k] for k in ('id','status','message')} for j in control.running('archive',cid)),None),
            'active_lease':leases.live('archive:'+cid),
            'counts':{'discovered':total,'downloaded':sum(bool(r.sha256) for r in rows),'uploaded':verified,
                'skipped':counts['restricted']+counts['unavailable'],'failed':counts['failed']+counts['ambiguous'],
                'remaining':total-verified-counts['restricted']-counts['unavailable'],
                'missing_from_latest_inventory':sum(r.seen_inventory!=(c.inventory or {}).get('revision') for r in rows)},
            'uploaded_bytes':sum(r.file_size or 0 for r in rows if r.status=='uploaded'),
            'items':[serialize(r) for r in rows[offset:offset+limit]],'has_more':total>offset+limit}


def serialize(row):
    return {k:getattr(row,k) for k in ('channel_id','video_id','original_url','title','media_kind','upload_date',
        'duration','status','file_size','sha256','md5','file_format','drive_file_id','error','attempts')} | {
            'verified_at':row.verified_at.isoformat() if row.verified_at else None}


def discover(url, parent, kinds):
    from .drive_sync import extract_folder_id
    parent=extract_folder_id(parent)
    if not re.fullmatch(r'[\w-]+',parent,re.ASCII):raise ValueError('Invalid Drive parent folder')
    data=source.inventory(url,kinds);cid=data['channel_id'];revision=uuid.uuid4().hex
    with leases.scope('archive:'+cid), SessionLocal() as s:
        c=s.get(ArchiveChannel,cid)
        if c and c.parent_folder_id!=parent:raise ValueError('Channel already has an archive parent; moving archives requires explicit reconciliation')
        if not c:
            c=ArchiveChannel(channel_id=cid,parent_folder_id=parent);s.add(c)
        c.title=data['title'];c.handle=data.get('handle');c.source_url=data['source_url'];c.formats=kinds
        c.status='inventoried' if data['complete'] else 'inventory_incomplete';c.error='; '.join(data.get('warnings',[])) or None
        c.inventory={'revision':revision,'completed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'complete':data['complete'],'warnings':data.get('warnings',[]),'discovered':len(data['videos']),
            'estimated_bytes':sum(v.get('estimated_bytes') or 0 for v in data['videos']),
            'unknown_size_count':sum(not v.get('estimated_bytes') for v in data['videos'])}
        for v in data['videos']:
            row=s.get(ArchiveVideo,(cid,v['video_id']))
            if not row:
                row=ArchiveVideo(channel_id=cid,video_id=v['video_id'],status='discovered');s.add(row)
            row.title=v['title'];row.original_url=v['original_url'];row.media_kind=v['media_kind']
            # Renames update descriptive metadata only. Successful bytes/IDs stay untouched.
            row.upload_date=v.get('upload_date') or row.upload_date;row.duration=v.get('duration') or row.duration
            row.metadata_snapshot=v;row.seen_inventory=revision
        s.commit()
    return view(cid)


def capacity(service,cid):
    with SessionLocal() as s:
        c=s.get(ArchiveChannel,cid)
        if not c:raise ValueError('Archive channel not found')
        meta=service.files().get(fileId=c.parent_folder_id,
            fields='id,name,mimeType,driveId,capabilities(canAddChildren)',**ALL).execute()
        if meta.get('mimeType')!='application/vnd.google-apps.folder' or not meta.get('capabilities',{}).get('canAddChildren'):
            raise ValueError('Archive parent is not a writable Drive folder')
        quota=service.about().get(fields='storageQuota').execute().get('storageQuota',{})
        limit=int(quota['limit']) if quota.get('limit') else None
        usage=int(quota.get('usage',0));available=max(0,limit-usage) if limit is not None else None
        if meta.get('driveId'):
            # about() is user quota, not pooled Shared Drive capacity.
            available=None
        pending=s.query(ArchiveVideo).filter(ArchiveVideo.channel_id==cid,ArchiveVideo.status!='uploaded').all()
        known=sum((r.metadata_snapshot or {}).get('estimated_bytes') or 0 for r in pending)
        unknown=sum(not (r.metadata_snapshot or {}).get('estimated_bytes') for r in pending)
    free=shutil.disk_usage(root()).free
    return {'local_free_bytes':free,'local_reserve_bytes':RESERVE,'drive_limit_bytes':limit,
        'drive_usage_bytes':usage,'drive_available_bytes':available,'shared_drive_id':meta.get('driveId'),
        'estimate_known_bytes':known,'estimate_unknown_count':unknown,
        'quota_note':'Shared Drive capacity is not exposed by user storageQuota; quota errors stop the job' if meta.get('driveId')
            else ('Capacity limit unavailable from Drive' if available is None else None)}


def hashes(path):
    with path.open('rb') as stream:sha=hashlib.file_digest(stream,'sha256').hexdigest()
    with path.open('rb') as stream:md5=hashlib.file_digest(stream,'md5').hexdigest()
    return sha,md5,path.stat().st_size


def local_path(cid,vid):
    with SessionLocal() as s:row=s.get(ArchiveVideo,(cid,vid));raw=row.local_path
    if not raw:return None
    p=Path(raw).resolve()
    if not p.is_relative_to(root()/cid/vid) or p.is_symlink():raise ValueError('Unsafe archive local path')
    return p if p.is_file() else None


def save_download(cid,vid,path,info):
    from .uploads import validate_video
    from .file_use import pin
    p=Path(path).resolve()
    if not p.is_relative_to(root()/cid/vid):raise ValueError('Media is outside its archive workspace')
    with pin([p]):
        validate_video(p);sha,md5,size=hashes(p)
        with SessionLocal() as s: previous=s.get(ArchiveVideo,(cid,vid)).status
        status=previous if previous in ('creating','ambiguous') else 'downloaded'
        persist(cid,vid,local_path=str(p),sha256=sha,md5=md5,file_size=size,file_format=p.suffix.lstrip('.'),
            upload_date=info.get('upload_date'),duration=info.get('duration'),status=status,error=None)


def _find(service,folder,cid,vid):
    q=(f"'{folder}' in parents and trashed=false and appProperties has {{ key='scrapper_channel' and value='{cid}' }} "
       f"and appProperties has {{ key='scrapper_video' and value='{vid}' }}")
    found=[];token=None
    while True:
        response=service.files().list(q=q,pageToken=token,pageSize=100,fields='nextPageToken,files(id)',
            includeItemsFromAllDrives=True,**ALL).execute()
        found.extend(response.get('files',[]));token=response.get('nextPageToken')
        if not token:break
    if len(found)>1:raise ValueError('Multiple remote archive identities; preserve them for owner reconciliation')
    return found[0]['id'] if found else None


def _verify(service,row):
    remote=service.files().get(fileId=row.drive_file_id,
        fields='id,size,md5Checksum,trashed,appProperties,parents',**ALL).execute()
    if remote.get('trashed'):raise ValueError('Drive copy is trashed; local copy retained')
    props=remote.get('appProperties',{})
    if props.get('scrapper_channel')!=row.channel_id or props.get('scrapper_video')!=row.video_id:
        raise ValueError('Remote archive identity does not match the ledger')
    if int(remote.get('size',-1))!=row.file_size or remote.get('md5Checksum')!=row.md5:
        raise ValueError('Drive checksum/size mismatch; local copy retained')
    return remote


def upload_one(service,cid,vid,folder):
    from googleapiclient.http import MediaFileUpload
    from .file_use import pin
    with SessionLocal() as s:row=s.get(ArchiveVideo,(cid,vid));s.expunge(row)
    path=local_path(cid,vid)
    if row.drive_file_id and row.sha256:
        try:
            _verify(service,row)
        except Exception:
            # Only an owned empty placeholder may receive media; never overwrite
            # a different non-empty remote version based on a renamed title.
            remote=service.files().get(fileId=row.drive_file_id,fields='size,trashed',**ALL).execute()
            if remote.get('trashed') or int(remote.get('size',-1))!=0:raise
        else:
            persist(cid,vid,status='uploaded',verified_at=dt.datetime.now(dt.timezone.utc),error=None)
            return
    if not path:raise ValueError('No validated local file is available; resume on its original local worker')
    with pin([path]):
        if hashes(path)!=(row.sha256,row.md5,row.file_size):raise ValueError('Local archive bytes changed')
        if not row.drive_file_id:
            existing=_find(service,folder,cid,vid)
            if existing:
                row.drive_file_id=existing;persist(cid,vid,drive_file_id=existing)
            elif row.status in ('creating','ambiguous'):
                raise ValueError('Unknown Drive creation outcome; no matching remote file yet. Retry reconciliation; a duplicate will not be created')
            else:
                persist(cid,vid,status='creating')
                name=re.sub(r'[^\w .()-]','_',row.title)[:140].strip() or 'video'
                try:
                    created=service.files().create(body={'name':f'{name}_{vid}.{row.file_format}',
                        'parents':[folder],'appProperties':{'scrapper_channel':cid,'scrapper_video':vid}},
                        fields='id',**ALL).execute()
                except BaseException:
                    persist(cid,vid,status='ambiguous',error='Drive metadata creation has an unknown outcome; reconcile before retry')
                    raise
                row.drive_file_id=created['id'];persist(cid,vid,drive_file_id=row.drive_file_id,status='downloaded')
        remote=service.files().get(fileId=row.drive_file_id,fields='size,trashed,parents,appProperties',**ALL).execute()
        if remote.get('trashed'):raise ValueError('Remote file was trashed')
        if folder not in remote.get('parents',[]) or remote.get('appProperties',{}) != {'scrapper_channel':cid,'scrapper_video':vid}:
            raise ValueError('Remote file identity or parent changed; media was not overwritten')
        if int(remote.get('size',-1))>0:
            _verify(service,row)
        else:
            media=MediaFileUpload(str(path),resumable=True,chunksize=8*1024*1024)
            request=service.files().update(fileId=row.drive_file_id,media_body=media,fields='id',**ALL)
            persist(cid,vid,status='uploading')
            response=None
            while response is None:
                control.check();_,response=request.next_chunk()
            if hashes(path)!=(row.sha256,row.md5,row.file_size):raise ValueError('Local file changed during upload')
            _verify(service,row)
        persist(cid,vid,status='uploaded',verified_at=dt.datetime.now(dt.timezone.utc),error=None)
    # Exclusive reclamation only after committed independent remote verification.
    with pin([path],exclusive=True):
        control.check();path.unlink()
    persist(cid,vid,local_path=None)


def manifest(cid):
    with SessionLocal() as s:
        rows=s.query(ArchiveVideo).filter_by(channel_id=cid).order_by(ArchiveVideo.video_id).all()
        channel=view(cid,limit=0)
        channel.pop('active_job',None);channel.pop('active_lease',None)
        return {**channel,'items':[serialize(r) for r in rows],
            'scope':'Archive only; no publishing queue, accounts, scheduling or publication', 'schema_version':1}


def write_manifest(service,cid,folder):
    from googleapiclient.http import MediaIoBaseUpload
    import io
    data=json.dumps(manifest(cid),ensure_ascii=False,indent=2).encode()
    media=MediaIoBaseUpload(io.BytesIO(data),mimetype='application/json',resumable=False)
    with SessionLocal() as s:mid=s.get(ArchiveChannel,cid).manifest_id
    if mid:response=service.files().update(fileId=mid,media_body=media,fields='id,size,md5Checksum',**ALL).execute()
    else:
        # Search makes a completed but unrecorded manifest creation recoverable.
        response=service.files().list(q=f"'{folder}' in parents and name='channel-manifest.json' and trashed=false",
            fields='files(id)',includeItemsFromAllDrives=True,**ALL).execute()
        files=response.get('files',[])
        if len(files)>1:raise ValueError('Multiple manifest files require reconciliation')
        if files:
            response=service.files().update(fileId=files[0]['id'],media_body=media,fields='id,size,md5Checksum',**ALL).execute()
        else:
            response=service.files().create(body={'name':'channel-manifest.json','parents':[folder]},
                media_body=media,fields='id,size,md5Checksum',**ALL).execute()
    if int(response.get('size',-1))!=len(data) or response.get('md5Checksum')!=hashlib.md5(data).hexdigest():
        raise ValueError('Channel manifest upload failed checksum verification')
    persist(cid,manifest_id=response['id'])


def run(cid, retry=False):
    from .drive_sync import get_drive_service, find_or_create_subfolder
    service=get_drive_service()
    with SessionLocal() as s:
        channel=s.get(ArchiveChannel,cid)
        if not channel or not channel.inventory.get('complete'):raise ValueError('Complete channel inventory is required first')
        title,parent,folder,revision=channel.title,channel.parent_folder_id,channel.drive_folder_id,channel.inventory['revision']
    cap=capacity(service,cid)
    if cap['drive_available_bytes'] is not None and cap['estimate_known_bytes']>cap['drive_available_bytes']:
        raise ValueError('Drive quota cannot fit the known remaining estimate')
    if cap['local_free_bytes']<2*settings.max_upload_bytes+RESERVE:raise ValueError('Not enough local temporary space for one bounded transfer')
    if not folder:
        folder=find_or_create_subfolder(service,re.sub(r'[/\\]','_',title)[:100]+' — '+cid,parent)
        persist(cid,drive_folder_id=folder)
    persist(cid,status='running',error=None)
    try:
        with SessionLocal() as s:
            ids=[r.video_id for r in s.query(ArchiveVideo).filter_by(channel_id=cid).order_by(ArchiveVideo.video_id)]
        for n,vid in enumerate(ids,1):
            control.check();control.progress(f'Archiving {n}/{len(ids)}: {vid}')
            with SessionLocal() as s:row=s.get(ArchiveVideo,(cid,vid));s.expunge(row)
            if row.status=='uploaded':continue
            if row.seen_inventory!=revision:continue  # preserve deleted/missing history
            if row.status in ('restricted','unavailable','failed') and not retry:continue
            if (row.metadata_snapshot or {}).get('live_status') in ('is_live','is_upcoming'):
                persist(cid,vid,status='unavailable',error='Live/upcoming broadcast is not an archived upload');continue
            try:
                persist(cid,vid,attempts=row.attempts+1,error=None)
                if not row.sha256 or (not row.drive_file_id and not local_path(cid,vid)):
                    free=shutil.disk_usage(root()).free
                    if free<2*settings.max_upload_bytes+RESERVE:raise ValueError('Local storage reserve reached')
                    path,info=source.download(cid,vid,root()/cid/vid,settings.max_upload_bytes)
                    save_download(cid,vid,path,info)
                upload_one(service,cid,vid,folder)
            except control.Cancelled:raise
            except source.AccessRestricted as exc:
                persist(cid,vid,status='restricted',error=str(exc)[-2000:])
                # Private/removed/age-restricted items are recorded and skipped.
                # Challenges/rate limits stop the channel; no proxy/token evasion.
                low=str(exc).lower()
                if any(x in low for x in ('not a bot','captcha','http error 403','http error 429')):raise
                continue
            except Exception as exc:
                with SessionLocal() as s:current=s.get(ArchiveVideo,(cid,vid)).status
                if current!='ambiguous':persist(cid,vid,status='failed',error=str(exc)[-2000:])
                # A quota/network failure is actionable; don't hammer all remaining videos.
                raise
        result=view(cid)
        persist(cid,status='complete' if result['counts']['remaining']==0 else 'partial')
        write_manifest(service,cid,folder)
        return view(cid)
    except BaseException as exc:
        # State/retained files survive Stop, restart and transfer errors.
        try:
            persist(cid,status='stopped' if isinstance(exc,control.Cancelled) else 'error',error=str(exc)[-2000:])
            write_manifest(service,cid,folder)
        except Exception:pass
        raise
