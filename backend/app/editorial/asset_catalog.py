"""Local authorized media catalog. No external fetching, model calls or publishing."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Literal

from fastapi import APIRouter,Depends,File,Form,HTTPException,UploadFile,Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_token
from ..config import settings
from ..db import get_session
from ..file_response import FileResponse
from ..file_use import pin
from ..models import MediaAsset
from ..studio import media
from ..studio.media_quality import file_hash
from ..worker_leases import clock,scope

router=APIRouter(prefix='/api/editorial/assets',dependencies=[Depends(require_token)])
MAX_SOURCE=50*1024*1024
MAX_OUTPUT=60*1024*1024


def root():
    path=settings.data_path/'editorial'/'assets';path.mkdir(parents=True,exist_ok=True);return path.resolve()


def probe(path):
    try:return json.loads(media.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],timeout=30).stdout)
    except Exception as exc:raise ValueError('Media could not be inspected as a supported local video/audio file') from exc


def cues(raw,seconds):
    try:values=json.loads(raw)
    except Exception as exc:raise ValueError('Caption cues must be a JSON array') from exc
    if not isinstance(values,list) or len(values)>500:raise ValueError('Supply at most500 caption cues')
    result=[];previous=0.0
    for row in values:
        if not isinstance(row,dict) or set(row)!={'start','end','text'}:raise ValueError('Each cue needs only start, end and text')
        if type(row['start']) not in (int,float) or type(row['end']) not in (int,float):raise ValueError('Cue times must be numbers')
        start,end=float(row['start']),float(row['end']);text=row['text']
        if not math.isfinite(start+end) or not 0<=start<end<=seconds+.05 or start<previous:
            raise ValueError('Cue intervals must be ordered, non-overlapping and inside the actual media duration')
        if not isinstance(text,str) or not 1<=len(text.strip())<=500:raise ValueError('Cue text must contain1–500 characters')
        previous=end;result.append({'start':start,'end':end,'text':text.strip()})
    return result


def out(asset):
    provenance={**asset.provenance}
    if 'media' in provenance:
        metadata={**provenance['media']};metadata['caption_count']=len(metadata.pop('caption_cues',[]));provenance['media']=metadata
    return {'id':asset.id,'kind':asset.kind,'sha256':asset.sha256,'tags':asset.tags,'provenance':provenance,
        'caption_basis':'owner_supplied_unverified','identity_basis':'owner_labels_not_face_recognition'}


def verify_existing(asset):
    """A duplicate is reusable only if every retained byte still matches its manifest."""
    try:
        entries=[{'file':asset.file_path,'sha256':asset.sha256},*asset.provenance.get('media',{}).get('frames',[])]
        for item in entries:
            path=Path(item['file']).resolve()
            if not path.is_relative_to(root()) or not path.is_file() or file_hash(path)!=item['sha256']:
                raise ValueError('Catalog bytes or preview changed; duplicate reuse refused. Recover the exact retained asset before reuse')
    except (OSError,ValueError) as exc:raise HTTPException(409,str(exc)) from exc


@router.post('/media')
async def import_media(file:UploadFile=File(...),kind:Literal['video','audio']=Form(...),
    license:Literal['owner_created','licensed']=Form(...),attestation:str=Form(min_length=20,max_length=2000),
    description:str=Form(min_length=10,max_length=2000),tags:str=Form(default='',max_length=2500),
    people:str=Form(default='',max_length=2000),caption_cues:str=Form(default='[]',max_length=100000),
    s:Session=Depends(get_session)):
    base=root()
    if shutil.disk_usage(base).free<180*1024*1024:raise HTTPException(409,'At least180MiB temporary free storage is required for a bounded media import')
    with tempfile.TemporaryDirectory(prefix='.import-',dir=base) as temporary:
        directory=Path(temporary);source=directory/'source.upload';target=directory/('normalized.mp4' if kind=='video' else 'normalized.m4a')
        try:
            with pin([directory]),scope('asset-import:'+directory.name):
                size=0
                with source.open('xb') as stream:
                    while chunk:=await file.read(1024*1024):
                        size+=len(chunk)
                        if size>MAX_SOURCE:raise HTTPException(413,'Media source must be at most50MiB')
                        stream.write(chunk)
                    stream.flush();os.fsync(stream.fileno())
                inspected=probe(source);streams=inspected.get('streams',[])
                seconds=float(inspected.get('format',{}).get('duration',0))
                if not math.isfinite(seconds) or not .25<=seconds<=300:raise ValueError('Media must be0.25–300seconds')
                if not any(row.get('codec_type')==kind for row in streams):raise ValueError('The actual media stream does not match the requested kind')
                annotation=cues(caption_cues,seconds)
                args=['ffmpeg','-v','error','-y','-i',str(source)]
                if kind=='video':args+=['-map','0:v:0','-map','0:a:0?','-vf',"scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",'-r','30','-c:v','libx264','-preset','veryfast','-crf','23','-c:a','aac','-b:a','128k']
                else:args+=['-map','0:a:0','-vn','-c:a','aac','-b:a','128k','-ac','2','-ar','48000']
                media.run([*args,'-sn','-dn','-map_metadata','-1','-t',str(seconds),'-fs',str(MAX_OUTPUT),'-movflags','+faststart',str(target)],timeout=180)
                normalized=probe(target);actual=float(normalized.get('format',{}).get('duration',0))
                if abs(actual-seconds)>max(.25,seconds*.01) or target.stat().st_size>MAX_OUTPUT:
                    raise ValueError('Normalized media was truncated or exceeded its storage bound; no asset was indexed')
                media.run(['ffmpeg','-v','error','-xerror','-i',str(target),'-f','null','-'],timeout=180)
                sha=file_hash(target);existing=s.query(MediaAsset).filter_by(sha256=sha).first()
                if existing:
                    verify_existing(existing)
                    return {**out(existing),'duplicate':True,'provenance_preserved':True}
                final=base/(sha+target.suffix);frames=[]
                from ..studio.stage_plan import dhash
                if kind=='video':
                    for index,at in enumerate(sorted({0.0,round(actual/2,3),max(0.0,round(actual-.1,3))})):
                        frame=directory/f'frame-{index}.jpg'
                        media.run(['ffmpeg','-v','error','-y','-ss',str(at),'-i',str(target),'-frames:v','1','-vf','scale=384:-2','-q:v','4',str(frame)],timeout=30)
                        frames.append({'at':at,'sha256':file_hash(frame),'perceptual_hash':str(dhash(frame)),'temporary':frame})
                with pin([final]):
                    try:os.link(target,final)
                    except FileExistsError:
                        if file_hash(final)!=sha:raise ValueError('Existing catalog bytes failed integrity; no file was replaced')
                    for index,frame in enumerate(frames):
                        dest=base/f'{sha}-frame-{index}.jpg'
                        try:os.link(frame.pop('temporary'),dest)
                        except FileExistsError:
                            if file_hash(dest)!=frame['sha256']:raise ValueError('Existing catalog preview failed integrity')
                        frame['file']=str(dest)
                    row=MediaAsset(sha256=sha,file_path=str(final),kind=kind,
                        tags=[text.strip()[:80] for text in tags.split(',') if text.strip()][:30],
                        provenance={'license':license,'attestation':attestation,'description':description,
                            'people':[text.strip()[:100] for text in people.split(',') if text.strip()][:20],
                            'recorded_at':clock(s).isoformat(),'original_sha256':file_hash(source),
                            'scope':'owner attested; permissions, scene semantics and identities not independently verified',
                            'media':{'schema':1,'seconds':actual,'bytes':final.stat().st_size,
                                'streams':[{key:stream.get(key) for key in ('codec_type','codec_name','width','height','sample_rate','channels')} for stream in normalized.get('streams',[])],
                                'frames':frames,'caption_cues':annotation,'caption_basis':'owner_supplied_unverified'}})
                    s.add(row)
                    try:s.commit()
                    except IntegrityError:
                        s.rollback();existing=s.query(MediaAsset).filter_by(sha256=sha).one()
                        verify_existing(existing)
                        return {**out(existing),'duplicate':True,'provenance_preserved':True}
                    return {**out(row),'duplicate':False,'provenance_preserved':True}
        except HTTPException:raise
        except (ValueError,RuntimeError,OSError) as exc:
            s.rollback();raise HTTPException(400,str(exc)[:300]) from exc


@router.get('/search')
def search(q:str=Query(default='',max_length=200),kind:Literal['image','video','audio']|None=None,
    limit:int=Query(default=30,ge=1,le=100),s:Session=Depends(get_session)):
    total=0
    terms=set(re.findall(r'\w+',q.casefold()));matches=[]
    query=s.query(MediaAsset)
    if kind:query=query.filter_by(kind=kind)
    for asset in query.order_by(MediaAsset.id.desc()).yield_per(100):
        if not asset.provenance.get('attestation'):continue
        labels=' '.join([*asset.tags,*asset.provenance.get('people',[])])
        description=asset.provenance.get('description','')
        captions=asset.provenance.get('media',{}).get('caption_cues',[])
        label_terms=set(re.findall(r'\w+',labels.casefold()));description_terms=set(re.findall(r'\w+',description.casefold()))
        spans=[cue for cue in captions if terms & set(re.findall(r'\w+',cue['text'].casefold()))]
        score=3*len(terms&label_terms)+2*len(terms&description_terms)+len(spans)
        if terms and not score:continue
        total+=1
        matches.append({**out(asset),'score':score,'matching_spans':spans[:20]})
        matches.sort(key=lambda row:(-row['score'],-row['id']))
        if len(matches)>limit:matches.pop()
    return {'method':'lexical_tags_description_owner_captions','semantic_model':'not_run','total':total,'assets':matches}


def asset_file(s,aid):
    asset=s.get(MediaAsset,aid)
    if not asset:raise HTTPException(404,'Asset not found')
    path=Path(asset.file_path).resolve()
    if not path.is_relative_to(root()) or not path.is_file():raise HTTPException(409,'Indexed asset is missing or outside catalog storage')
    return asset,path


@router.get('/{aid}')
def details(aid:int,s:Session=Depends(get_session)):
    asset,_=asset_file(s,aid)
    return {**out(asset),'caption_cues':asset.provenance.get('media',{}).get('caption_cues',[])}


@router.get('/{aid}/media')
def download(aid:int,s:Session=Depends(get_session)):
    asset,path=asset_file(s,aid)
    return FileResponse(path,media_type={'image':'image/png','video':'video/mp4','audio':'audio/mp4'}[asset.kind],expected_sha256=asset.sha256)


@router.get('/{aid}/frame/{index}')
def frame(aid:int,index:int,s:Session=Depends(get_session)):
    asset,_=asset_file(s,aid);frames=asset.provenance.get('media',{}).get('frames',[])
    if not 0<=index<len(frames):raise HTTPException(404,'Indexed frame not found')
    item=frames[index];path=Path(item['file']).resolve()
    if not path.is_relative_to(root()):raise HTTPException(409,'Preview is outside catalog storage')
    return FileResponse(path,media_type='image/jpeg',expected_sha256=item['sha256'])
