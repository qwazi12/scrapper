"""Versioned owner mappings. No content delivery or provider requests occur here."""
from __future__ import annotations
import copy
from datetime import datetime,timezone
from typing import Literal

from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import Field,model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session,defer

from ..auth import require_token
from ..budget_reservations import lock
from ..db import get_session
from ..models import ContentLineage,CreativeExport,ExportVersion,DeliveryArchive,UsageEvent,BudgetReservation
from ..worker_leases import clock
from .routes import Strict,Format,digest,_utc

router=APIRouter(prefix='/api/editorial',dependencies=[Depends(require_token)])
Kind=Literal['export_version','native_export','delivery_archive']
MODELS={'export_version':ExportVersion,'native_export':CreativeExport,'delivery_archive':DeliveryArchive}


def source(s,kind,identifier):
    row=s.get(MODELS[kind],identifier)
    if not row:raise HTTPException(404,'Retained source not found')
    manifest=copy.deepcopy(row.manifest)
    identity={'kind':kind,'id':row.id,'manifest':manifest}
    return row,manifest,digest(identity)


def latest(s):
    current={}
    for row in s.query(ContentLineage).options(defer(ContentLineage.data)).order_by(ContentLineage.revision):current[row.binding_key]=row
    return current


def effective_observations(s):
    from ..models import PerformanceObservation
    active={row.id for row in latest(s).values()};effective={}
    for row in s.query(PerformanceObservation):
        if row.provenance.get('lineage_id') and row.provenance['lineage_id'] not in active:continue
        key=(row.platform,row.provenance.get('account_id'),row.content_id,row.format,_utc(row.observed_at))
        fetched=_utc(datetime.fromisoformat(row.provenance['fetched_at'])) if row.provenance.get('fetched_at') else _utc(row.observed_at)
        old=effective.get(key)
        if not old or fetched>old[1]:effective[key]=(row,fetched)
    return [value[0] for value in effective.values()]


def out(row):return {'id':row.id,'binding_key':row.binding_key,'revision':row.revision,'platform':row.platform,
    'account_id':row.account_id,'content_id':row.content_id,'data':{key:value for key,value in row.data.items() if key!='source_manifest'},'created_at':row.created_at}


@router.get('/content-sources')
def sources(limit:int=Query(50,ge=1,le=100),s:Session=Depends(get_session)):
    records=[]
    for kind,model in MODELS.items():
        for row in s.query(model).order_by(model.created_at.desc()).limit(limit):
            _,manifest,version=source(s,kind,row.id)
            preview=None
            if kind=='native_export':
                item=manifest.get('video') or manifest.get('cover')
                if item:preview={'kind':'video' if manifest.get('video') else 'image','file':item['file']}
            else:
                for index,asset in enumerate(manifest.get('assets',[])):
                    if asset.get('available') and asset.get('role') in ('video','thumbnail'):
                        preview={'kind':'video' if asset['role']=='video' else 'image','role':asset['role'],'index':index};break
            records.append({'kind':kind,'id':row.id,'title':manifest.get('title') or manifest.get('inputs',{}).get('brief',{}).get('title') or f'{kind} {row.id[:8]}',
                'fingerprint':version,'created_at':row.created_at,'preview':preview})
    return {'sources':records,'mapping_basis':'owner_attestation; public video bytes not compared'}


@router.get('/content-lineage')
def bindings(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),s:Session=Depends(get_session)):
    current=latest(s)
    ordered=sorted(current.values(),key=lambda row:_utc(row.created_at),reverse=True)
    return {'total':len(ordered),'bindings':[out(row) for row in ordered[offset:offset+limit]],'versions':[out(row) for row in s.query(ContentLineage).order_by(ContentLineage.created_at.desc()).limit(100)]}


@router.get('/content-lineage/{identifier}')
def binding_detail(identifier:str,s:Session=Depends(get_session)):
    row=s.get(ContentLineage,identifier)
    if not row:raise HTTPException(404,'Content mapping not found')
    return {**out(row),'source_manifest':row.data['source_manifest']}


class BindingIn(Strict):
    platform:Literal['youtube','instagram','facebook']
    account_id:str=Field(min_length=3,max_length=128,pattern=r'^[\w.:-]+$')
    content_id:str=Field(min_length=3,max_length=255,pattern=r'^[\w.:-]+$')
    format:Format
    published_at:datetime
    source_kind:Kind
    source_id:str=Field(min_length=32,max_length=64,pattern=r'^[a-f0-9]+$')
    expected_source_fingerprint:str=Field(min_length=64,max_length=64)
    expected_current_id:str|None=Field(default=None,min_length=64,max_length=64)
    mapping_evidence:str=Field(min_length=20,max_length=4000)
    reviewed:Literal[True]

    @model_validator(mode='after')
    def aware(self):
        if not self.published_at.tzinfo:raise ValueError('Publication time needs a timezone')
        return self


def cost_snapshot(s,kind,row,manifest):
    if kind!='export_version':return {'status':'unattributed','amount_usd':None,'reason':'No reliable request-to-original-project cost mapping is retained for this source'}
    start=_utc(datetime.fromisoformat(manifest['project_created_at']))
    end=_utc(row.created_at)
    ref=f'{"studio" if row.kind=="studio" else "countdown"}:{row.project_id}'
    attempts=[r.id for r in s.query(BudgetReservation).filter(BudgetReservation.ref==ref,
        BudgetReservation.created_at>=start,BudgetReservation.created_at<=end)]
    events=s.query(UsageEvent).filter(UsageEvent.ref==ref,or_(UsageEvent.reservation_id.in_(attempts),
        (UsageEvent.created_at>=start)&(UsageEvent.created_at<=end))).all()
    basis={}
    for event in events:basis[event.cost_basis or 'legacy_unknown']=basis.get(event.cost_basis or 'legacy_unknown',0)+(event.cost_usd or 0)
    holds=[r for r in s.query(BudgetReservation).filter(BudgetReservation.id.in_(attempts)) if r.state in ('reserved','dispatched','unknown','breach')]
    return {'status':'local_project_window_snapshot' if events else 'unattributed_no_events','ref':ref,'amount_usd':sum(event.cost_usd or 0 for event in events) if events else None,
        'by_basis':basis,'usage_event_ids':[event.id for event in events],'unsettled_request_ids':[r.id for r in holds],
        'as_of':clock(s).isoformat(),'scope':'Project request window through this retained version, including matched later billing adjustments; not marginal per-cut cost',
        'limitations':['Configured rates and owner attestations; provider invoices not independently verified',
            'Countdown reused-segment costs are not attributed here'] if row.kind=='countdown' else ['Configured rates and owner attestations; provider invoices not independently verified']}


@router.post('/content-lineage')
def bind(req:BindingIn,s:Session=Depends(get_session)):
    lock(s)
    if req.published_at>clock(s):raise HTTPException(422,'Publication time cannot be in the future')
    row,manifest,version=source(s,req.source_kind,req.source_id)
    if req.expected_source_fingerprint!=version:raise HTTPException(409,'Retained source changed; reload its exact manifest before mapping')
    key=digest([req.platform,req.account_id,req.content_id])
    prior=s.query(ContentLineage).filter_by(binding_key=key).order_by(ContentLineage.revision.desc()).first()
    decision=req.model_dump(mode='json',exclude={'expected_current_id'})
    if prior and prior.data['decision']==decision:return {**out(prior),'created':False}
    if (prior.id if prior else None)!=req.expected_current_id:
        raise HTTPException(409,'Mapping changed; review the current mapping before recording a correction')
    revision=(prior.revision+1) if prior else 1
    data={'decision':decision,'source_manifest':manifest,'source_fingerprint':version,
        'cost_snapshot':cost_snapshot(s,req.source_kind,row,manifest),'mapping_basis':'owner_attested','public_bytes_verified':False,
        'supersedes':prior.id if prior else None}
    record=ContentLineage(id=digest([key,revision,data]),binding_key=key,revision=revision,platform=req.platform,
        account_id=req.account_id,content_id=req.content_id,data=data,created_at=clock(s))
    if prior:
        from ..worker_leases import revoke
        revoke(s,'audience-sync:'+prior.id)
    s.add(record);s.commit()
    return {**out(record),'created':True}
