"""Private audience evidence and descriptive experiments; never sends replies."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from statistics import median
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from ..auth import require_token
from ..db import get_session
from ..models import EditorialRecord, PerformanceObservation
from .routes import Format, Strict, _utc, digest, out

router = APIRouter(prefix="/api/editorial", dependencies=[Depends(require_token)])


def deidentify(text,*,collapse=True):
    text = re.sub(r"https?://\S+|www\.\S+", "[link removed]", text, flags=re.I)
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[email removed]", text)
    text = re.sub(r"(?<!\w)@[\w.]+", "[handle removed]", text)
    dates={}
    marker="__SC_DATE_"
    while marker in text:marker="_"+marker
    def protect_date(match):
        value=match.group(0)
        try:datetime.strptime(value,'%Y-%m-%d')
        except ValueError:return value
        key=f'{marker}{len(dates)}__';dates[key]=value;return key
    text=re.sub(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',protect_date,text)
    text = re.sub(r"(?<!\w)(?:\+?\d[\d ()-]{5,}\d)(?!\w)", "[number removed]", text)
    for key,value in dates.items():text=text.replace(key,value)
    return " ".join(text.split()) if collapse else text.replace("\r\n","\n").strip()


def classify(text):
    lowered = text.casefold()
    if re.search(r"\b(crypto|giveaway|earn money|click my|dm me)\b", lowered): return "possible_spam"
    if re.search(r"\b(actually|incorrect|wrong|correction|isn't|is not)\b", lowered): return "correction"
    if "?" in text or re.search(r"\b(how|why|when|could you|please explain)\b", lowered): return "question"
    if re.search(r"\b(disagree|overrated|underrated|better than|worst|debate)\b", lowered): return "debate"
    return "feedback"


class CommentImport(Strict):
    platform: Literal["youtube", "instagram", "facebook"]
    content_id: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=5, max_length=1000)
    comments: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded(self):
        if any(not 3 <= len(text.strip()) <= 2000 for text in self.comments): raise ValueError("Each comment must contain 3–2000 characters")
        return self


@router.post("/feedback/import")
def import_feedback(req: CommentImport, s: Session = Depends(get_session)):
    existing = {r.data.get("evidence_key") for r in s.query(EditorialRecord).filter(EditorialRecord.kind == "feedback").all()}
    created = []
    for raw in req.comments:
        text = deidentify(raw)
        category = classify(text)
        key = digest([req.platform, req.content_id, text])
        if key in existing: continue
        existing.add(key)
        reply = {"question": "Thanks for the question. We can check the sources before giving a detailed answer.",
                 "correction": "Thank you for flagging this. Please point us to a source so we can review the correction.",
                 "debate": "Interesting perspective. Which part of the comparison mattered most to you?",
                 "feedback": "Thanks for sharing your feedback. Which detail would you like us to explore further?",
                 "possible_spam": ""}[category]
        row = EditorialRecord(kind="feedback", data={"platform": req.platform, "content_id": req.content_id,
            "source": deidentify(req.source), "text": text, "category": category, "classification": "heuristic_needs_review",
            "evidence_key": key, "reply_draft": reply, "approved_for_manual_use": False,
            "privacy_note": "No author identifiers imported. Common contacts redacted; inspect for names and other personal details before reuse."})
        s.add(row); s.flush(); created.append(row.id)
    s.commit()
    return {"created": created, "delivery": "private_drafts_only"}


from .feedback_review import ReplyDraft


@router.put('/feedback/{fid}/draft')
def edit_reply(fid:int,req:ReplyDraft,s:Session=Depends(get_session)):
    from .feedback_review import save_reply
    return save_reply(s,fid,req)


class ExperimentIn(Strict):
    name: str = Field(min_length=5, max_length=255)
    hypothesis: str = Field(min_length=15, max_length=2000)
    platform: Literal["youtube", "instagram", "facebook"]
    format: Format
    control_ids: list[str] = Field(min_length=1, max_length=100)
    variant_ids: list[str] = Field(min_length=1, max_length=100)
    metric: Literal["views", "engaged_views", "retention_percent", "average_view_seconds", "average_view_percent", "impressions", "click_through_percent"]
    age_hours: Literal[24, 72, 168, 720]
    minimum_per_group: int = Field(default=5, ge=3, le=100)
    controls: str = Field(min_length=10, max_length=2000)
    account_id: str | None = Field(default=None,min_length=3,max_length=128)

    learning_decision_id:str | None = Field(default=None,min_length=64,max_length=64)

    @model_validator(mode="after")
    def distinct(self):
        self.control_ids=[value.strip() for value in self.control_ids]
        self.variant_ids=[value.strip() for value in self.variant_ids]
        if len(self.name.strip())<5 or len(self.hypothesis.strip())<15 or len(self.controls.strip())<10:raise ValueError("Provide a substantive experiment name, hypothesis and controls")
        if set(self.control_ids) & set(self.variant_ids): raise ValueError("Control and variant must use different content IDs")
        if any(not x.strip() or len(x) > 255 for x in self.control_ids + self.variant_ids): raise ValueError("Invalid content ID")
        if len(set(self.control_ids)) != len(self.control_ids) or len(set(self.variant_ids)) != len(self.variant_ids): raise ValueError("Content IDs must be unique per group")
        return self


@router.post("/experiments")
def create_experiment(req: ExperimentIn, s: Session = Depends(get_session)):
    from ..budget_reservations import lock
    from ..worker_leases import clock
    from ..models import LearningDecision
    lock(s);context=None
    request_fingerprint=digest(['experiment',req.model_dump(mode='json')])
    existing=s.query(EditorialRecord).filter(EditorialRecord.kind=='experiment',EditorialRecord.data['request_fingerprint'].as_string()==request_fingerprint).first()
    if existing:return {**out(existing),'created':False}
    if req.learning_decision_id:
        decision=s.get(LearningDecision,req.learning_decision_id)
        if not decision or decision.action!='plan_experiment':raise HTTPException(409,'Choose a reviewed experiment-planning decision')
        latest_decision=s.query(LearningDecision).filter_by(diagnostic_key=decision.diagnostic_key).order_by(LearningDecision.created_at.desc(),LearningDecision.id.desc()).first()
        if latest_decision.id!=decision.id:raise HTTPException(409,'A newer private decision supersedes this plan; review the latest decision before registering')
        diagnostic=decision.data['diagnostic']
        if (req.platform!=diagnostic['platform'] or req.format!=diagnostic['format'] or req.age_hours!=diagnostic['age_hours']
                or (req.account_id or 'unspecified_owner_import')!=diagnostic['account_id']):
            raise HTTPException(409,'Experiment platform, account, format and age must match the reviewed evidence')
        from .performance_diagnostics import evidence_report
        _,items=evidence_report(s,key=decision.diagnostic_key)
        if not items or items[0]['version']!=decision.version:raise HTTPException(409,'Learning evidence changed; record a new reviewed planning decision')
        context={'decision_id':decision.id,'diagnostic_key':decision.diagnostic_key,'version':decision.version}
    row = EditorialRecord(kind="experiment", data={**req.model_dump(mode="json"), "registered_at": clock(s).isoformat(),
        'learning_context':context,'request_fingerprint':request_fingerprint})
    s.add(row); s.commit(); s.refresh(row)
    return {**out(row),'created':True}


def experiment_result(data, observations):
    from .performance_diagnostics import measure
    selected = {}
    target = data["age_hours"]
    for row in observations:
        age = (_utc(row.observed_at) - _utc(row.published_at)).total_seconds() / 3600
        if row.provenance.get("account_id") != data.get("account_id"):continue
        if row.platform != data["platform"] or row.format != data["format"] or abs(age - target) > target * .1: continue
        if measure(row,data["metric"]) is None: continue
        previous = selected.get(row.content_id)
        score=(abs(age-target),-_utc(row.observed_at).timestamp(),row.key)
        if previous is None or score<previous[2]: selected[row.content_id] = (row, age,score)
    groups = {}
    retrospective = False
    known_ids=set()
    for name in ("control", "variant"):
        rows = [selected[cid][0] for cid in data[f"{name}_ids"] if cid in selected]
        known_ids.update(row.content_id for row in rows)
        values = [measure(row,data["metric"]) for row in rows]
        groups[name] = {"n": len(values), "median": median(values) if values else None,"observation_keys":sorted(row.key for row in rows)}
        retrospective |= any(_utc(row.published_at) < _utc(datetime.fromisoformat(data["registered_at"])) for row in rows)
    enough = all(g["n"] >= data["minimum_per_group"] for g in groups.values())
    control, variant = groups["control"]["median"], groups["variant"]["median"]
    delta = (variant - control) / control * 100 if enough and control and variant is not None else None
    return {"groups": groups, "status": "descriptive_comparison" if enough else "insufficient_comparable_data",
            "relative_median_change_percent": round(delta, 2) if delta is not None else None,
            "design": "retrospective" if retrospective else "prospectively_registered" if known_ids==set(data["control_ids"]+data["variant_ids"]) else "publication_timing_unverified",
            "winner": None, "next_action": "Inspect confounders and replicate before changing recommendations" if enough else
                "Collect more observations of this format at the selected publication age",
            "limitations": ["Observational grouping does not establish causation", "Reporting latency and topic/packaging differences may confound the result",
                            "No statistical significance or automated winning treatment is claimed"]}


@router.get("/learning")
def learning(s: Session = Depends(get_session)):
    feedback = s.query(EditorialRecord).filter(EditorialRecord.kind == "feedback").order_by(EditorialRecord.id.desc()).limit(500).all()
    from .content_lineage import effective_observations
    observations = effective_observations(s)
    experiments = s.query(EditorialRecord).filter(EditorialRecord.kind == "experiment").order_by(EditorialRecord.id.desc()).all()
    questions = [r for r in feedback if r.data["category"] in ("question", "correction")]
    # Conservative recurring-question buckets. These are token groups for manual
    # review, not an invented semantic model or audience consensus.
    clusters = {}
    stopwords = {"what", "when", "where", "which", "would", "could", "about", "this", "that", "with", "your", "please", "there", "have", "does"}
    for row in questions:
        tokens = [t for t in re.findall(r"[a-z]{4,}", row.data["text"].lower()) if t not in stopwords]
        topic = tokens[0] if tokens else "unclassified"
        clusters.setdefault(topic, []).append(row)
    from .feedback_review import enriched
    manual=s.query(EditorialRecord).filter(EditorialRecord.kind=="feedback",EditorialRecord.data["latest_evidence_id"].as_string().is_(None)).order_by(EditorialRecord.id.desc()).limit(500).all()
    return {"feedback": [enriched(s,r) for r in manual], "experiments": [{**out(r), "result": experiment_result(r.data, observations)} for r in experiments],
            "question_groups": [{"topic_token": key, "n": len(rows), "feedback_ids": [r.id for r in rows],
                                "suggestion": f"Research an original follow-up addressing {key}", "confidence": "manual_review"}
                               for key, rows in clusters.items()],
            "delivery": "private_drafts_only", "live_comment_ingestion": "explicit_read_only_youtube_pages; actual account/provider verification unexecuted"}
