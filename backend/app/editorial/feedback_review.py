"""Context-bound private reply decisions and unverified correction proposals."""
from __future__ import annotations
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import Field,model_validator
from sqlalchemy.orm import Session

from ..auth import require_token
from ..budget_reservations import lock
from ..db import get_session
from ..models import CommentEvidence,ContentLineage,EditorialRecord,FeedbackDecision
from ..worker_leases import clock
from ..publication_journal import identity
from .routes import Strict,digest,out
from .content_lineage import latest

router=APIRouter(prefix='/api/editorial/feedback',dependencies=[Depends(require_token)])


def get(s,fid):
    row=s.get(EditorialRecord,fid)
    if not row or row.kind!='feedback':raise HTTPException(404,'Feedback not found')
    return row


def source_claims(binding):
    if not binding:return []
    manifest=binding.data['source_manifest']
    inputs=manifest.get('snapshot',{}).get('inputs',{})
    groups=[('research',(inputs.get('research') or {}).get('claims',[])),
        ('narration',(inputs.get('script') or {}).get('sentences',[])),
        ('native_beat',manifest.get('inputs',{}).get('brief',{}).get('beats',[]))]
    claims=[]
    for kind,values in groups:
        for index,value in enumerate(values):
            text=value.get('text') if isinstance(value,dict) else value if isinstance(value,str) else None
            if text:claims.append({'key':digest([binding.data['source_fingerprint'],kind,index,text]),'kind':kind,'index':index,'text':text})
    return claims


def context(s,row,cache=None):
    data=row.data;binding=s.get(ContentLineage,data['lineage_id']) if data.get('lineage_id') else None
    if binding and cache is not None and 'lineages' not in cache:cache['lineages']=latest(s)
    current=(cache['lineages'] if cache is not None else latest(s)).get(binding.binding_key) if binding else None
    title=None;members=[];coverage=None
    if current:
        manifest=current.data['source_manifest']
        title=manifest.get('title') or manifest.get('inputs',{}).get('brief',{}).get('title') or 'Retained source'
    if data.get('thread_key'):
        from .comments import cursor_view
        records=s.query(EditorialRecord).filter(EditorialRecord.kind=='feedback',EditorialRecord.data['thread_key'].as_string()==data['thread_key']).all()
        parent=None
        for entry in records:
            evidence=s.get(CommentEvidence,entry.data.get('latest_evidence_id')) if entry.data.get('latest_evidence_id') else None
            if not evidence:continue
            members.append({'feedback_id':entry.id,'evidence_id':evidence.id,'text':evidence.data['text'],
                'reply':bool(evidence.parent_key),'provider_updated_at':evidence.data['provider_updated_at']})
            if not evidence.parent_key:parent=evidence
        if parent and current:
            page=cursor_view(s,current.id,parent)
            count=sum(member['reply'] for member in members);reported=parent.data.get('reported_reply_count')
            incomplete=(reported is None and not page['pagination_exhausted']) or (reported is not None and reported!=count)
            coverage={'reported_replies':reported,'retained_replies':count,'pagination_exhausted':page['pagination_exhausted'],
                'requires_decision':incomplete,'reply_cursor_version':page['version'],'parent_evidence_id':parent.id,
                'scope':'Published accessible entries captured at their read times; no private/held/deleted status is inferred'}
    payload={'feedback_identity':identity(row),'text':data['text'],'evidence_id':data.get('latest_evidence_id'),'lineage_id':current.id if current else None,
        'source_fingerprint':current.data['source_fingerprint'] if current else None,
        'members':sorted(members,key=lambda member:member['feedback_id']),'coverage':coverage}
    return {**payload,'fingerprint':digest(payload),'title':title,
        'mapping_superseded':bool(binding and current and binding.id!=current.id),'binding':current,
        'status':'linked_retained_source' if current else 'owner_text_import'}


def enriched(s,row,cache=None):
    ctx=context(s,row,cache);data=row.data
    approved=bool(data.get('approved_for_manual_use') and data.get('reviewed_context')==ctx['fingerprint']
        and data.get('reviewed_draft_sha256')==digest(data.get('reply_draft','')))
    return {**out(row),'feedback_identity':identity(row),'approved_for_manual_use':approved,'reply_version':digest([ctx['fingerprint'],data.get('reply_draft'),data.get('approval_decision_id')]),
        'context_status':ctx['status'],'context_title':ctx['title'],'context_lineage_id':ctx['lineage_id'],
        'context_mapping_superseded':ctx['mapping_superseded'],'coverage':ctx['coverage']}


@router.get('')
def listing(lineage_id:str|None=None,q:str=Query('',max_length=200),limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0),s:Session=Depends(get_session)):
    from sqlalchemy import func
    query=s.query(EditorialRecord).filter(EditorialRecord.kind=='feedback')
    if lineage_id:query=query.filter(EditorialRecord.data['lineage_id'].as_string()==lineage_id)
    if q:query=query.filter(func.lower(EditorialRecord.data['text'].as_string()).contains(q.casefold(),autoescape=True))
    count=query.count();cache={}
    return {'total':count,'offset':offset,'limit':limit,'feedback':[enriched(s,row,cache) for row in query.order_by(EditorialRecord.updated_at.desc(),EditorialRecord.id.desc()).offset(offset).limit(limit)]}


@router.get('/{fid}/context')
def detail(fid:int,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),s:Session=Depends(get_session)):
    row=get(s,fid);ctx=context(s,row);claims=source_claims(ctx['binding'])
    return {**enriched(s,row),'thread_members':ctx['members'][offset:offset+limit],'thread_total':len(ctx['members']),
        'claims':claims[offset:offset+limit],'claim_total':len(claims),'offset':offset,'limit':limit,
        'decisions':[{'id':decision.id,'action':decision.action,'data':decision.data,'at':decision.created_at}
            for decision in s.query(FeedbackDecision).filter_by(feedback_id=fid,feedback_identity=identity(row)).order_by(FeedbackDecision.created_at.desc()).limit(100)]}


class ReplyDraft(Strict):
    text:str=Field(min_length=5,max_length=2000)
    expected_version:str=Field(min_length=64,max_length=64)
    approved_for_manual_use:bool=False
    reviewed:bool=False
    context_lineage_id:str|None=Field(default=None,min_length=64,max_length=64)
    coverage_rationale:str=Field(default='',max_length=4000)


def save_reply(s,fid,req:ReplyDraft):
    from .learning import deidentify
    lock(s);row=get(s,fid);current=enriched(s,row);ctx=context(s,row)
    if current['reply_version']!=req.expected_version:raise HTTPException(409,'Comment, thread, mapping or draft changed; refresh context before saving')
    cleaned=deidentify(req.text,collapse=False)
    if len(cleaned)<5:raise HTTPException(422,'Provide a substantive private draft')
    if req.approved_for_manual_use:
        if not req.reviewed:raise HTTPException(409,'Explicitly review this exact draft, source and thread context')
        if cleaned!=req.text.strip():raise HTTPException(409,'Contact details were redacted; save privately and review the cleaned draft before approval')
        if req.context_lineage_id!=ctx['lineage_id']:raise HTTPException(409,'The current retained source mapping differs from the reviewed context')
        if ctx['coverage'] and ctx['coverage']['requires_decision'] and len(req.coverage_rationale.strip())<20:
            raise HTTPException(409,'Thread replies are incomplete; fetch their available pages or explain the deliberate limited-context review')
    at=clock(s);identifier=digest([fid,uuid4().hex,at.isoformat()])
    decision={'draft':cleaned,'approved_for_manual_use':req.approved_for_manual_use,'context_fingerprint':ctx['fingerprint'],
        'lineage_id':ctx['lineage_id'],'latest_evidence_id':row.data.get('latest_evidence_id'),
        'coverage':ctx['coverage'],'coverage_rationale':req.coverage_rationale.strip(),'delivery':'manual_use_only; no reply was sent'}
    s.add(FeedbackDecision(id=identifier,feedback_id=fid,feedback_identity=identity(row),action='reply_reviewed' if req.approved_for_manual_use else 'private_draft_saved',data=decision,created_at=at))
    row.data={**row.data,'reply_draft':cleaned,'approved_for_manual_use':req.approved_for_manual_use,'approval_decision_id':identifier,
        'reviewed_at':at.isoformat() if req.approved_for_manual_use else None,
        'reviewed_context':ctx['fingerprint'] if req.approved_for_manual_use else None,
        'reviewed_draft_sha256':digest(cleaned) if req.approved_for_manual_use else None}
    s.commit();return enriched(s,row)


class CorrectionIn(Strict):
    expected_version:str=Field(min_length=64,max_length=64)
    claim_key:str=Field(min_length=64,max_length=64)
    proposal:str=Field(min_length=15,max_length=2000)
    evidence_url:str=Field(min_length=12,max_length=2048,pattern=r'^https://[^\s]+$')
    evidence_excerpt:str=Field(min_length=20,max_length=4000)
    rationale:str=Field(min_length=20,max_length=4000)
    create_followup:bool=True
    reviewed:Literal[True]

    @model_validator(mode='after')
    def substantive(self):
        from urllib.parse import urlsplit
        for name,bound in [('proposal',15),('evidence_excerpt',20),('rationale',20)]:
            if len(getattr(self,name).strip())<bound:raise ValueError(f'{name} requires substantive text')
        url=urlsplit(self.evidence_url)
        if not url.hostname or url.username or url.password:raise ValueError('Use a reference URL without embedded account credentials')
        return self


@router.post('/{fid}/correction')
def correction(fid:int,req:CorrectionIn,s:Session=Depends(get_session)):
    from .learning import deidentify
    lock(s);row=get(s,fid)
    identifier=digest(['correction-proposal',identity(row),req.model_dump(mode='json')])
    existing=s.get(FeedbackDecision,identifier)
    if existing:return {'id':identifier,**existing.data,'created':False}
    current=enriched(s,row);ctx=context(s,row)
    if req.expected_version!=current['reply_version']:raise HTTPException(409,'Feedback or retained context changed; reload the exact correction target')
    claim=next((claim for claim in source_claims(ctx['binding']) if claim['key']==req.claim_key),None)
    if not claim:raise HTTPException(409,'Choose a current exact retained claim; no script was changed')
    followup=None
    if req.create_followup:
        followup=EditorialRecord(kind='opportunity',data={'title':f"Review correction: {ctx['title'] or 'retained claim'}"[:255],
            'angle':deidentify(req.proposal),'audience':'Viewers asking about a source-supported correction',
            'formats':['community','short'],'evidence':[],'feedback_refs':[fid],'status':'draft',
            'competition':'Unknown','feasibility':'Requires independent source review and a new inspected treatment',
            'demand':None,'demand_source':None,'planned_for':None})
        s.add(followup);s.flush()
    data={'lineage_id':ctx['lineage_id'],'source_fingerprint':ctx['source_fingerprint'],'claim':claim,
        'comment_evidence_id':row.data.get('latest_evidence_id'),'proposal':deidentify(req.proposal),
        'evidence_url':req.evidence_url,'evidence_excerpt':deidentify(req.evidence_excerpt),'rationale':deidentify(req.rationale),
        'status':'proposal_requires_independent_source_review','followup_opportunity_id':followup.id if followup else None,
        'script_changed':False,'published_content_changed':False}
    s.add(FeedbackDecision(id=identifier,feedback_id=fid,feedback_identity=identity(row),action='correction_proposed',data=data,created_at=clock(s)))
    s.commit();return {'id':identifier,**data,'created':True}
