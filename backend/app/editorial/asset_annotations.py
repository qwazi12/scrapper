"""Append-only owner metadata/use decisions; no file replacement or publication."""
from __future__ import annotations
from copy import deepcopy
from itertools import islice
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException
from pydantic import Field,model_validator
from sqlalchemy import and_,func
from sqlalchemy.orm import Session

from ..auth import require_token
from ..budget_reservations import lock
from ..db import get_session
from ..file_use import pin
from ..models import AssetAnnotation,MediaAsset
from ..worker_leases import clock
from .routes import Strict,digest,_utc

router=APIRouter(prefix='/api/editorial/assets',dependencies=[Depends(require_token)])
POLICY='owner-asset-annotation-v1'


def identity(asset):
    return digest([asset.id,asset.sha256,asset.kind,_utc(asset.created_at).isoformat() if asset.created_at else None])


def base_version(asset):return digest({'identity':identity(asset),'tags':asset.tags,'provenance':asset.provenance})


def latest_for(s,assets):
    keys=[identity(asset) for asset in assets]
    if not keys:return {}
    heads=s.query(AssetAnnotation.asset_identity,func.max(AssetAnnotation.revision).label('revision')).filter(
        AssetAnnotation.asset_identity.in_(keys)).group_by(AssetAnnotation.asset_identity).subquery()
    return {row.asset_identity:row for row in s.query(AssetAnnotation).join(heads,and_(
        AssetAnnotation.asset_identity==heads.c.asset_identity,AssetAnnotation.revision==heads.c.revision))}


def current_version(asset,head):return digest([POLICY,base_version(asset),head.id if head else None])


def effective(asset,head):
    provenance=deepcopy(asset.provenance);tags=list(asset.tags or [])
    if head:
        if head.data['base_version']!=base_version(asset):
            provenance.update(use_status='blocked',metadata_review_state='Original catalog metadata changed; review the current source again')
        else:
            values=head.data['decision']
            provenance.update({key:values[key] for key in ('license','attestation','description','people','use_status')})
            if 'media' in provenance:provenance['media']['caption_cues']=deepcopy(values['caption_cues'])
            tags=list(values['tags'])
            provenance['metadata_review']={'id':head.id,'revision':head.revision,'recorded_at':_utc(head.created_at).isoformat(),
                'basis':'owner_attested; permissions, identity and caption alignment not independently verified'}
    return tags,provenance


def entries(s,query):
    stream=iter(query.yield_per(100))
    while batch:=list(islice(stream,100)):
        heads=latest_for(s,batch)
        for asset in batch:yield asset,heads.get(identity(asset))


def get(s,aid):
    asset=s.get(MediaAsset,aid)
    if not asset:raise HTTPException(404,'Asset not found')
    return asset


def view(s,asset):
    head=latest_for(s,[asset]).get(identity(asset));tags,provenance=effective(asset,head)
    history=s.query(AssetAnnotation).filter_by(asset_identity=identity(asset)).order_by(AssetAnnotation.revision.desc())
    return {'asset_id':asset.id,'asset_identity':identity(asset),'sha256':asset.sha256,'kind':asset.kind,
        'version':current_version(asset,head),'identity_known':asset.created_at is not None,
        'metadata':{'license':provenance['license'],'attestation':provenance['attestation'],
            'description':provenance.get('description',''),'people':provenance.get('people',[]),'tags':tags,
            'caption_cues':provenance.get('media',{}).get('caption_cues',[]),'use_status':provenance.get('use_status','permitted')},
        'seconds':provenance.get('media',{}).get('seconds'),
        'original':{'tags':deepcopy(asset.tags),'provenance':deepcopy(asset.provenance)},
        'history_count':history.count(),'history':[{'id':row.id,'revision':row.revision,'created_at':row.created_at,'data':row.data} for row in history.limit(20)],
        'scope':'Private owner annotations/use decisions; original catalog bytes/provenance retained; no automatic license/identity verification'}


class AnnotationIn(Strict):
    expected_version:str=Field(min_length=64,max_length=64)
    license:Literal['owner_created','licensed']
    attestation:str=Field(min_length=20,max_length=2000)
    description:str=Field(min_length=10,max_length=2000)
    tags:list[str]=Field(default_factory=list,max_length=30)
    people:list[str]=Field(default_factory=list,max_length=20)
    caption_cues:list[dict]=Field(default_factory=list,max_length=500)
    use_status:Literal['permitted','blocked']
    rationale:str=Field(min_length=20,max_length=4000)
    reviewed:Literal[True]

    @model_validator(mode='after')
    def text_bounds(self):
        self.attestation=self.attestation.strip();self.description=self.description.strip();self.rationale=self.rationale.strip()
        if len(self.attestation)<20 or len(self.description)<10 or len(self.rationale)<20:
            raise ValueError('Describe the source, permission and reason for this exact metadata/use review')
        for key,bound in [('tags',80),('people',100)]:
            values=[text.strip() for text in getattr(self,key)]
            if any(not 1<=len(text)<=bound for text in values) or len({text.casefold() for text in values})!=len(values):
                raise ValueError(f'{key} must contain distinct nonempty bounded labels')
            setattr(self,key,values)
        return self


@router.get('/{aid}/metadata-review')
def metadata_review(aid:int,s:Session=Depends(get_session)):return view(s,get(s,aid))


@router.post('/{aid}/metadata-review')
def save_metadata(aid:int,req:AnnotationIn,s:Session=Depends(get_session)):
    from .asset_catalog import cues,verify_existing
    import json
    asset=get(s,aid)
    if asset.created_at is None:raise HTTPException(409,'Unknown legacy asset identity; restore its original catalog identity before review')
    with pin([asset.file_path],exclusive=True,session=s):
        lock(s);s.expire_all();asset=get(s,aid)
        source_identity=identity(asset);head=latest_for(s,[asset]).get(source_identity)
        decision=req.model_dump(mode='json',exclude={'expected_version','reviewed'})
        try:
            if asset.kind=='image' and req.caption_cues:raise ValueError('Still images cannot carry timed caption intervals')
            decision['caption_cues']=cues(json.dumps(req.caption_cues),asset.provenance.get('media',{}).get('seconds',0)) if req.caption_cues else []
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
        receipt=digest([POLICY,source_identity,req.expected_version,decision])
        saved=s.get(AssetAnnotation,receipt)
        if saved:
            s.commit()  # Release the serialization writer before file-claim cleanup.
            return {'id':saved.id,'created':False,'is_current':head is not None and head.id==saved.id,'view':view(s,asset)}
        if req.expected_version!=current_version(asset,head):raise HTTPException(409,'Asset metadata changed; reload the current source and review before saving')
        verify_existing(asset)
        record=AssetAnnotation(id=receipt,asset_id=asset.id,asset_identity=source_identity,revision=(head.revision+1) if head else 1,
            data={'policy':POLICY,'base_version':base_version(asset),'expected_version':req.expected_version,'decision':decision,
                'permission_basis':'owner_attested','permission_verified_by_app':False},created_at=clock(s))
        s.add(record);s.commit()
        return {'id':record.id,'created':True,'is_current':True,'view':view(s,asset)}
