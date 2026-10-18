"""Explicit local records. No account connection or publishing side effects."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from statistics import median
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import require_token
from ..db import get_session
from ..models import EditorialRecord, PerformanceObservation
from ..studio.sources import tier

router = APIRouter(prefix="/api/editorial", tags=["editorial"], dependencies=[Depends(require_token)])
UTC = timezone.utc
Format = Literal["longform", "short", "reel", "carousel", "image", "community"]


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Strict):
    url: str = Field(min_length=12, max_length=2048, pattern=r"^https://[^\s]+$")
    excerpt: str = Field(min_length=20, max_length=4000)
    entity: str = Field(min_length=2, max_length=255)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def timestamp(cls, value):
        if not value.tzinfo or value > datetime.now(UTC):
            raise ValueError("Use a timezone-aware retrieval time that is not in the future")
        return value


class OpportunityIn(Strict):
    title: str = Field(min_length=3, max_length=255)
    angle: str = Field(min_length=15, max_length=2000)
    audience: str = Field(min_length=5, max_length=1000)
    formats: list[Format] = Field(min_length=1, max_length=6)
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)
    demand: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    demand_source: str | None = Field(default=None, max_length=500)
    competition: str = Field(default="Unknown", max_length=1000)
    feasibility: str = Field(default="Needs asset and narration review", max_length=1000)
    planned_for: datetime | None = None
    feedback_refs: list[int] = Field(default_factory=list, max_length=100)

    @field_validator("planned_for")
    @classmethod
    def aware(cls, value):
        if value and not value.tzinfo: raise ValueError("Calendar time needs a timezone")
        return value

    @model_validator(mode="after")
    def source_for_demand(self):
        if self.demand is not None and not self.demand_source:
            raise ValueError("A demand measurement requires its source and period")
        if len(set(self.formats)) != len(self.formats): raise ValueError("Choose each format once")
        return self


def opportunity(s: Session, oid: int):
    row = s.get(EditorialRecord, oid)
    if not row or row.kind != "opportunity": raise HTTPException(404, "Opportunity not found")
    return row


def out(row):
    return {"id": row.id, "kind": row.kind, **row.data, "updated_at": row.updated_at}


def assessment(data):
    evidence = data.get("evidence", [])
    trusted = sum(tier(e["url"]) in ("official", "trusted") for e in evidence)
    fresh = sum(0 <= (datetime.now(UTC) - datetime.fromisoformat(e["retrieved_at"].replace("Z", "+00:00"))).total_seconds() < 30 * 86400 for e in evidence)
    return {"evidence_count": len(evidence), "recognized_sources": trusted, "recent_sources": fresh,
            "confidence": "owner_review_required", "selection": "manual",
            "uncertainty": ["Excerpts supplied by owner; independent retrieval and factual support are not verified",
                            "Search demand and competitor evidence do not predict channel growth"]}


@router.get("/opportunities")
def opportunities(s: Session = Depends(get_session)):
    return [{**out(row), "assessment": assessment(row.data)} for row in s.query(EditorialRecord)
            .filter(EditorialRecord.kind == "opportunity").order_by(EditorialRecord.id.desc()).all()]


@router.post("/opportunities")
def create_opportunity(req: OpportunityIn, s: Session = Depends(get_session)):
    validate_feedback_refs(s, req.feedback_refs)
    row = EditorialRecord(kind="opportunity", data={**req.model_dump(mode="json"), "status": "draft"})
    s.add(row); s.commit(); s.refresh(row)
    return out(row)


@router.put("/opportunities/{oid}")
def edit_opportunity(oid: int, req: OpportunityIn, s: Session = Depends(get_session)):
    validate_feedback_refs(s, req.feedback_refs)
    row = opportunity(s, oid)
    row.data = {**req.model_dump(mode="json"), "status": "draft"}
    s.commit()
    return out(row)


def validate_feedback_refs(s, refs):
    for ref in refs:
        row = s.get(EditorialRecord, ref)
        if not row or row.kind != "feedback": raise HTTPException(409, "Audience evidence reference is missing")


@router.post("/opportunities/{oid}/approve")
def approve_opportunity(oid: int, s: Session = Depends(get_session)):
    row = opportunity(s, oid)
    if not row.data.get("evidence"): raise HTTPException(409, "Attach the research excerpts before approving a campaign brief")
    data = {k: v for k, v in row.data.items() if k not in ("status", "approval")}
    row.data = {**data, "status": "approved", "approval": {"fingerprint": digest(data), "at": datetime.now(UTC).isoformat(),
        "scope": "owner reviewed supplied research; no independent verification implied"}}
    s.commit()
    return out(row)


class Metrics(Strict):
    views: int | None = Field(default=None, ge=0, strict=True)
    engaged_views: int | None = Field(default=None, ge=0, strict=True)
    impressions: int | None = Field(default=None, ge=0, strict=True)
    clicks: int | None = Field(default=None, ge=0, strict=True)
    likes: int | None = Field(default=None, ge=0, strict=True)
    comments: int | None = Field(default=None, ge=0, strict=True)
    average_view_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    retention_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class ObservationIn(Strict):
    platform: Literal["youtube", "instagram", "facebook"]
    content_id: str = Field(min_length=1, max_length=255, pattern=r"^[\w.:-]+$")
    format: Format
    topic: str = Field(default="Unclassified", max_length=255)
    angle: str = Field(default="Unclassified", max_length=1000)
    published_at: datetime
    observed_at: datetime
    metrics: Metrics
    source: str = Field(min_length=5, max_length=1000)
    latency_note: str = Field(default="Provider reporting may be delayed", max_length=1000)

    @model_validator(mode="after")
    def times(self):
        if not self.published_at.tzinfo or not self.observed_at.tzinfo: raise ValueError("Times need timezones")
        if self.observed_at < self.published_at or self.observed_at > datetime.now(UTC): raise ValueError("Invalid observation window")
        if not any(v is not None for v in self.metrics.model_dump().values()): raise ValueError("Supply at least one observed metric")
        if self.metrics.clicks is not None and self.metrics.impressions is not None and self.metrics.clicks > self.metrics.impressions:
            raise ValueError("Clicks cannot exceed impressions")
        return self


@router.post("/observations")
def import_observation(req: ObservationIn, s: Session = Depends(get_session)):
    data = req.model_dump(mode="json")
    key = digest([req.platform, req.content_id, req.observed_at.astimezone(UTC).isoformat()])
    existing = s.get(PerformanceObservation, key)
    if existing:
        if existing.metrics != data["metrics"]: raise HTTPException(409, "Observation already exists with different measurements; retain the original evidence")
        return {"key": key, "created": False}
    row = PerformanceObservation(key=key, platform=req.platform, content_id=req.content_id, format=req.format,
        published_at=req.published_at, observed_at=req.observed_at, metrics=data["metrics"],
        provenance={"source": req.source, "topic": req.topic, "angle": req.angle, "latency_note": req.latency_note,
                    "mode": "owner_import", "private_account_access": "not_verified"})
    s.add(row)
    try: s.commit()
    except IntegrityError:
        s.rollback()
        raise HTTPException(409, "Observation was imported concurrently; reload it")
    return {"key": key, "created": True}


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def cohorts(rows):
    groups = {}
    # Only snapshots within 10% of the same publication age can be compared.
    # One content item contributes at most once to each age cohort.
    for row in rows:
        age = (_utc(row.observed_at) - _utc(row.published_at)).total_seconds() / 3600
        window = next((n for n in (24, 72, 168, 720) if abs(age - n) <= n * .1), None)
        if window is None: continue
        key = (row.platform, row.format, window)
        items = groups.setdefault(key, {})
        old = items.get(row.content_id)
        if old is None or abs(age - window) < abs(old[1] - window): items[row.content_id] = (row, age)
    result = []
    for (platform, fmt, window), items in sorted(groups.items()):
        medians, samples = {}, {}
        for metric in Metrics.model_fields:
            values = [row.metrics.get(metric) for row, age in items.values() if row.metrics.get(metric) is not None]
            medians[metric] = median(values) if values else None
            samples[metric] = len(values)
        result.append({"platform": platform, "format": fmt, "age_hours": window, "n": len(items),
            "confidence": "small_sample" if len(items) < 5 else "descriptive_only", "medians": medians,
            "metric_samples": samples, "content_ids": sorted(items),
            "limitations": "Cumulative snapshots within ±10% of age; observational comparison, not causal evidence"})
    return result


@router.get("/insights")
def insights(s: Session = Depends(get_session)):
    rows = s.query(PerformanceObservation).all()
    return {"adapters": [{"platform": p, "status": "unavailable", "reason": "Private analytics adapter not connected; import an authorized export",
                         "live_metrics": None, "imported_observations": sum(r.platform == p for r in rows)}
                        for p in ("youtube", "instagram", "facebook")],
            "cohorts": cohorts(rows), "observation_count": len(rows),
            "next_actions": ["Import age-matched observations before comparing formats"] if not rows else
                            ["Review small samples and provider latency before choosing an experiment"],
            "claim": "No automated causal or growth claims"}
