"""Exact-file editorial acceptance; measurements never certify rights or relevance."""
from __future__ import annotations
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field

from ..publication_journal import digest
from .media_quality import export_fingerprint

CHECKS={
    'captions':'I inspected caption wording, timing, contrast, clipping and safe areas, or explicitly chose a treatment without captions.',
    'visuals':'I inspected every scene for relevance, identity, pacing and distracting repetitions.',
    'rights':'I reviewed permission and intended use for footage, images, music, voice and other assets.',
    'narration':'I listened to the complete narration and reviewed facts, audio, story, title and thumbnail honesty.',
}


class AcceptanceIn(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_fingerprint: str=Field(min_length=64,max_length=64)
    checks: list[str]=Field(min_length=4,max_length=4)
    warning_decisions: dict[str,str]=Field(default_factory=dict,max_length=1000)
    rights_rationale: str=Field(min_length=20,max_length=4000)
    reviewed: Literal[True]


def findings(project):
    quality=(project.render or {}).get('quality') or {}
    result=[{'kind':'measurement_warning','message':message} for message in quality.get('warnings',[])]
    from .stage_plan import plan_issues
    plan=getattr(project,'plan',None) or []
    groups={shot['id']:shot.get('look') or shot['id'] for shot in (getattr(project,'shots',None) or []) if isinstance(shot,dict) and 'id' in shot}
    for issue in plan_issues(plan,groups):
        result.append({'kind':'visual_repetition','message':f"Visual slot {issue['slot']} repeats slot {issue['with_slot']} ({issue['kind']}); inspect the actual frames."})
    # Include measured spans in the warning identity: changing inspection cannot reuse a decision.
    for span in quality.get('black_spans',[]):
        result.append({'kind':'black_span','message':f"Black frame interval {span['start']:.3f}–{span['end']:.3f}s; confirm intentional transition."})
    for span in quality.get('silence_spans',[]):
        if span['end']-span['start']>3:
            result.append({'kind':'silence_span','message':f"Silent interval {span['start']:.3f}–{span['end']:.3f}s; confirm intentional pause."})
    unique={digest(row):row for row in result}
    return [{'id':key,**row} for key,row in unique.items()]


def view(project,root):
    return {'fingerprint':export_fingerprint(project,root),'checks':CHECKS,'findings':findings(project),
        'measurements':(project.render or {}).get('quality'),
        'rights_status':'owner_attestation_required; automated measurements do not prove permission',
        'source':getattr(project,'trailer',None)}


def accept(project,root,request:AcceptanceIn,at):
    return accept_view(view(project,root),request,at)


def accept_view(current,request:AcceptanceIn,at):
    if request.expected_fingerprint!=current['fingerprint']:
        raise RuntimeError('Export or inspection changed; reload and review the current files before approval')
    if set(request.checks)!=set(current['checks']) or len(request.checks)!=len(current['checks']):
        raise RuntimeError('Complete each editorial acceptance check exactly once')
    required={row['id'] for row in current['findings']}
    if set(request.warning_decisions)!=required:
        raise RuntimeError('Record an intentional editorial decision for every current finding; stale decisions are not accepted')
    if any(not 20<=len(text.strip())<=4000 for text in request.warning_decisions.values()):
        raise RuntimeError('Each finding needs a substantive decision of 20–4000 characters')
    if len(request.rights_rationale.strip())<20:raise RuntimeError('Describe the reviewed rights and intended use')
    return {'schema':2,'fingerprint':current['fingerprint'],'approved_at':at.isoformat(),
        'checks':request.checks,'warning_decisions':request.warning_decisions,'rights_rationale':request.rights_rationale.strip(),
        'rights_basis':'owner_attested','rights_verified_by_app':False}


def require_acceptance(project,root):
    approval=(project.review or {}).get('export_approval') or {}
    return require_view_acceptance(view(project,root),approval)


def require_view_acceptance(current,approval):
    if approval.get('schema')!=2:raise RuntimeError('Review and approve this exact export first; complete editorial acceptance')
    try:
        request=AcceptanceIn(expected_fingerprint=approval.get('fingerprint'),checks=approval.get('checks'),
            warning_decisions=approval.get('warning_decisions'),rights_rationale=approval.get('rights_rationale'),reviewed=True)
        from datetime import datetime,timezone
        accept_view(current,request,datetime.now(timezone.utc))
    except (ValueError,TypeError,RuntimeError) as exc:raise RuntimeError('Review and approve this exact export first; acceptance evidence is incomplete') from exc
