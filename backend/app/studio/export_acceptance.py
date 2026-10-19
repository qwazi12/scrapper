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
    from .duration_intent import comparison
    requested=comparison(project)
    if requested is not None:
        target,actual=requested.get('target_seconds'),requested.get('actual_seconds')
        if not target or not actual:
            result.append({'kind':'requested_duration','message':'Requested or delivered length is unconfirmed; inspect and record the intended duration before accepting.'})
        elif not .75*target<=actual<=1.25*target:
            result.append({'kind':'requested_duration','message':f'Delivered {actual:.1f}s compared with requested approximately {target:g}s ({requested["basis"]}). This exceeds the 25% review band; explain the intentional length or revise. Do not pad or rewrite automatically.'})
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
    from .duration_intent import comparison
    return {'fingerprint':export_fingerprint(project,root),'checks':CHECKS,'findings':findings(project),
        'requested_format':getattr(project,'fmt',None),
        'requested_duration':comparison(project),
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
        'requested_duration':current.get('requested_duration'),
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


# --- one-tap Approve (owner, 2026-10-19) ---------------------------------------------------------
# The owner watches the video, then taps Approve: the four checks are ticked and every minor finding
# gets a standard decision. Serious problems still need the detailed form. The stored approval is the
# same schema-2 record, so require_acceptance() checks it exactly like a detailed one.
QUICK_DECISION = "Watched the export and approved it as is with one-tap Approve (owner)."
RIGHTS_KEY = "export_rights_statement"


def severe(project) -> list[str]:
    """Findings too serious for one tap."""
    out = []
    quality = (project.render or {}).get('quality') or {}
    for span in quality.get('black_spans', []):
        if span['end'] - span['start'] > 3:
            out.append(f"black screen for {span['end'] - span['start']:.1f}s at {span['start']:.0f}s")
    for span in quality.get('silence_spans', []):
        if span['end'] - span['start'] > 8:
            out.append(f"silence for {span['end'] - span['start']:.1f}s at {span['start']:.0f}s")
    from .duration_intent import comparison
    requested = comparison(project)
    if requested:
        target, actual = requested.get('target_seconds'), requested.get('actual_seconds')
        if target and actual and not .5 * target <= actual <= 2 * target:
            out.append(f"length {actual / 60:.1f} min against about {target / 60:.1f} min requested")
    return out


def saved_rights(s) -> str:
    from ..models import AppSetting
    row = s.get(AppSetting, RIGHTS_KEY)
    return ((row.value or {}).get('text') or '') if row else ''


def save_rights(s, text: str) -> None:
    from ..models import AppSetting
    row = s.get(AppSetting, RIGHTS_KEY)
    if row:
        row.value = {'text': text}
    else:
        s.add(AppSetting(key=RIGHTS_KEY, value={'text': text}))


class QuickIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    rights_rationale: str | None = Field(default=None, max_length=4000)


def quick_accept(s, project, root, req: QuickIn, at):
    bad = severe(project)
    if bad:
        raise RuntimeError('Needs your decision first — use Detailed review: ' + '; '.join(bad))
    rights = (req.rights_rationale or '').strip() or saved_rights(s)
    if len(rights) < 20:
        raise RuntimeError('Write your standard rights statement once (20+ characters); it is saved for next time')
    current = view(project, root)
    request = AcceptanceIn(expected_fingerprint=req.expected_fingerprint, checks=list(current['checks']),
                           warning_decisions={row['id']: QUICK_DECISION for row in current['findings']},
                           rights_rationale=rights, reviewed=True)
    approval = accept_view(current, request, at)
    if req.rights_rationale and req.rights_rationale.strip():
        save_rights(s, rights)
    return {**approval, 'quick': True}


def quick_view(s, project) -> dict:
    return {'severe': severe(project), 'saved_rights': saved_rights(s)}
