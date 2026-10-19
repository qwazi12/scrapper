"""Descriptive peer comparisons and recorded cost context; no execution authority."""
from __future__ import annotations

import math
from statistics import median

from .routes import _utc,digest

POLICY={'version':'descriptive-peers-v1','minimum_other_items':5,'below_peer_median_ratio':.75,
    'mode':'owner_review_only','can_spend':False,'can_publish':False,'can_send_reply':False,
    'can_change_automation':False,'can_select_winner':False}


def number(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None


def selected_cohorts(rows):
    groups={}
    for row in rows:
        age=(_utc(row.observed_at)-_utc(row.published_at)).total_seconds()/3600
        window=next((n for n in (24,72,168,720) if abs(age-n)<=n*.1),None)
        if window is None:continue
        key=(row.platform,row.provenance.get('account_id') or 'unspecified_owner_import',row.format,window)
        choices=groups.setdefault(key,{})
        # Match age first; a later reporting boundary breaks exact-distance ties.
        score=(abs(age-window),-_utc(row.observed_at).timestamp(),row.key)
        if row.content_id not in choices or score<choices[row.content_id][1]:choices[row.content_id]=(row,score)
    return {key:{cid:value[0] for cid,value in choices.items()} for key,choices in groups.items()}


def measure(row,name):
    metrics=row.metrics
    if name=='click_through_percent':
        impressions,clicks=number(metrics.get('impressions')),number(metrics.get('clicks'))
        return clicks/impressions*100 if impressions and clicks is not None and clicks<=impressions else None
    return number(metrics.get(name))


def comparison(row,others,name):
    value=measure(row,name)
    pairs=[(peer,measure(peer,name)) for peer in others]
    pairs=[pair for pair in pairs if pair[1] is not None]
    baseline=median([pair[1] for pair in pairs]) if pairs else None
    status=('missing_measure' if value is None else 'insufficient_comparable_peers' if len(pairs)<POLICY['minimum_other_items']
        else 'zero_peer_baseline' if not baseline else 'observed_below_peer_median' if value/baseline<POLICY['below_peer_median_ratio'] else 'no_heuristic_flag')
    return {'metric':name,'value':value,'peer_n':len(pairs),'peer_median':baseline,'status':status,
        'ratio':value/baseline if value is not None and baseline else None,
        'peer_observation_keys':sorted(peer.key for peer,_ in pairs)}


def recorded_cost(row,binding):
    result={'status':'unattributed','amount_usd':None,'per_1000':None,'denominator':None,
        'scope':'Recorded mapping-time variable-cost snapshot; fixed costs excluded, not current billing or profit',
        'limitation':'Reconcile current usage and unsettled requests before any budget decision'}
    if not binding:return result
    snapshot=binding.data.get('cost_snapshot') or {}
    result.update({'as_of':snapshot.get('as_of'),'basis':snapshot.get('by_basis',{}),'source_status':snapshot.get('status')})
    amount=number(snapshot.get('amount_usd'))
    if snapshot.get('unsettled_request_ids'):result['status']='unsettled_at_snapshot';return result
    if amount is None:result['status']='no_reliable_recorded_cost';return result
    if not snapshot.get('by_basis') or set(snapshot['by_basis'])-{'configured_usage_estimate','owner_attested_adjustment','estimate_after_attestation'}:result['status']='unknown_cost_basis';return result
    result['amount_usd']=amount
    for name in ('engaged_views','views'):
        value=measure(row,name)
        if value:
            result.update({'status':'recorded_estimate_only','per_1000':amount/value*1000,'denominator':name});return result
    result['status']='no_positive_view_denominator'
    return result


def diagnose(row,others,cohort,binding=None):
    platform,account,fmt,age=cohort
    others=[peer for peer in others if peer.content_id!=row.content_id]
    measures=[comparison(row,others,name) for name in ('impressions','click_through_percent','retention_percent','average_view_percent','engaged_views','views')]
    actions=[]
    templates={
        'impressions':('topic_timing','Compare evidence-led topic timing in a matched publication-age experiment; a low impression count does not establish why exposure differs.'),
        'click_through_percent':('packaging','Test one truthful title/thumbnail concept while holding subject, format and publication timing comparable.'),
        'retention_percent':('viewing','Review the complete video and detailed retention before testing one pacing change; an aggregate cannot locate an opening drop-off.'),
        'average_view_percent':('viewing','Inspect the complete opening/payoff and detailed retention before testing one clarity change; aggregate viewed percentage is not a drop-off location.'),
    }
    used=set()
    for item in measures:
        if item['status']=='observed_below_peer_median' and item['metric'] in templates:
            kind,text=templates[item['metric']]
            if kind not in used:actions.append({'kind':kind,'text':text,'support_metric':item['metric']});used.add(kind)
    if not actions:actions=[{'kind':'collect_or_replicate','text':'Collect missing comparable measures or replicate a pre-registered creative experiment before changing recommendations.','support_metric':None}]
    data={'key':digest(['diagnostic',*cohort,row.content_id]),'platform':platform,'account_id':account,'format':fmt,
        'age_hours':age,'content_id':row.content_id,'observation_key':row.key,'published_at':_utc(row.published_at).isoformat(),
        'observed_at':_utc(row.observed_at).isoformat(),'lineage_id':binding.id if binding else None,
        'source_title':(binding.data.get('source_manifest',{}).get('title') or binding.data.get('source_manifest',{}).get('inputs',{}).get('brief',{}).get('title')) if binding else None,
        'measures':measures,'cost':recorded_cost(row,binding),'suggestions':actions,
        'unknowns':['Opening drop-off and repeat-viewer behavior require their own measures; neither is inferred here',
            'Topic, duration, packaging, publication timing and reporting latency remain possible confounders'],
        'limitations':['Leave-one-out peers: same platform/account/format and cumulative age within ±10%; metric-specific missing values excluded',
            'The 0.75 ratio and five-peer minimum are review heuristics, not significance tests or causal estimates',
            'No action grants spend, publishing, replies or automatic creative-policy changes'],
        'policy':POLICY}
    version_input={'diagnostic':data,'peers':sorted((peer.key,_utc(peer.published_at).isoformat(),_utc(peer.observed_at).isoformat(),peer.metrics,peer.provenance) for peer in others),
        'mapping_fingerprint':binding.data.get('source_fingerprint') if binding else None,'observation_provenance':row.provenance}
    return {**data,'version':digest(version_input)}

# The report and decisions operate exclusively on already retained local evidence.
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import Field,model_validator
from sqlalchemy.orm import Session
from ..auth import require_token
from ..db import get_session
from ..models import LearningDecision
from ..worker_leases import clock
from .routes import Strict

router=APIRouter(prefix='/api/editorial/learning-diagnostics',dependencies=[Depends(require_token)])


def evidence_report(s,*,limit=20,offset=0,key=None,q=""):
    from .content_lineage import latest,effective_observations
    bindings={binding.id:binding for binding in latest(s).values()}
    grouped=selected_cohorts(effective_observations(s));contexts=[]
    for cohort,choices in sorted(grouped.items()):
        for cid,row in sorted(choices.items()):
            if not q or q.casefold() in cid.casefold():contexts.append((cohort,choices,cid,row))
    # Build expensive peer comparisons only for the requested page or review.
    selected=([context for context in contexts if digest(['diagnostic',*context[0],context[2]])==key][:1]
        if key else contexts[offset:offset+limit])
    result=[]
    for cohort,choices,cid,row in selected:
        binding=bindings.get(row.provenance.get('lineage_id'))
        result.append(diagnose(row,[other for peer_id,other in choices.items() if peer_id!=cid],cohort,binding))
    return len(contexts),result


def decision_view(row,current_version=None):
    return {'id':row.id,'action':row.action,'version':row.version,'data':row.data,'created_at':row.created_at,
        'matches_current_evidence':row.version==current_version if current_version else None}


@router.get('')
def report(limit:int=Query(20,ge=1,le=50),offset:int=Query(0,ge=0),q:str=Query('',max_length=200),s:Session=Depends(get_session)):
    total,selected=evidence_report(s,limit=limit,offset=offset,q=q)
    for item in selected:
        latest=s.query(LearningDecision).filter_by(diagnostic_key=item['key']).order_by(LearningDecision.created_at.desc(),LearningDecision.id.desc()).first()
        item['last_decision']=decision_view(latest,item['version']) if latest else None
    return {'total':total,'offset':offset,'limit':limit,'items':selected,'policy':POLICY,
        'claim':'Descriptive observations and private owner decisions; no causal winner or automatic execution'}


@router.get('/{key}/history')
def history(key:str,limit:int=Query(20,ge=1,le=50),offset:int=Query(0,ge=0),s:Session=Depends(get_session)):
    query=s.query(LearningDecision).filter_by(diagnostic_key=key)
    return {'total':query.count(),'offset':offset,'limit':limit,'decisions':[decision_view(row) for row in
        query.order_by(LearningDecision.created_at.desc(),LearningDecision.id.desc()).offset(offset).limit(limit)]}


class ReviewIn(Strict):
    expected_version:str=Field(min_length=64,max_length=64)
    action:Literal['plan_experiment','defer','reject']
    rationale:str=Field(min_length=20,max_length=4000)
    reviewed:Literal[True]

    @model_validator(mode='after')
    def substantive(self):
        if len(self.rationale.strip())<20:raise ValueError('Explain the evidence and confounders in this decision')
        return self


@router.post('/{key}/review')
def review(key:str,req:ReviewIn,s:Session=Depends(get_session)):
    from ..budget_reservations import lock
    from .learning import deidentify
    lock(s)
    identifier=digest(['learning-decision',key,req.model_dump(mode='json')])
    existing=s.get(LearningDecision,identifier)
    if existing:return {**decision_view(existing),'created':False,'execution':'none'}
    _,items=evidence_report(s,key=key)
    item=items[0] if items else None
    if not item:raise HTTPException(409,'Comparable observation context changed; reload the diagnostic')
    if item['version']!=req.expected_version:raise HTTPException(409,'Measures, peers, source mapping or cost context changed; review the current evidence')
    data={'diagnostic':item,'rationale':deidentify(req.rationale,collapse=False),
        'policy':POLICY,'execution':'none; a private planning decision never grants paid work, publication or automation changes'}
    row=LearningDecision(id=identifier,diagnostic_key=key,version=item['version'],action=req.action,data=data,created_at=clock(s))
    s.add(row);s.commit()
    return {**decision_view(row,item['version']),'created':True,'execution':'none'}
