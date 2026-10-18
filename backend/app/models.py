"""Database models: clips, ingest jobs, compilations, and log entries."""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import BigInteger, JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class Status(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    urls: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.queued, index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    clips: Mapped[list["Clip"]] = relationship(back_populates="job")


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_jobs.id"), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1024), index=True)
    platform: Mapped[str | None] = mapped_column(String(64), nullable=True)
    video_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    uploader: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumb_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.queued, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # UI selection state (which clips are ticked for the next compilation).
    selected: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)

    job: Mapped["IngestJob"] = relationship(back_populates="clips")


class Compilation(Base):
    __tablename__ = "compilations"

    id: Mapped[int] = mapped_column(primary_key=True)
    clip_ids: Mapped[list] = mapped_column(JSON, default=list)
    orientation: Mapped[str] = mapped_column(String(16), default="portrait")
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.queued, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)  # 0..1
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    event: Mapped[str] = mapped_column(String(64), default="", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class SocialPost(Base):
    __tablename__ = "social_posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    compilation_id: Mapped[int | None] = mapped_column(ForeignKey("compilations.id"), nullable=True)
    outstand_post_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)  # history
    publish_requests: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Upload-Post request_ids
    accounts: Mapped[list] = mapped_column(JSON, default=list)
    content: Mapped[str] = mapped_column(Text, default="")
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class QueueItem(Base):
    """Posting Queue replacing Google Sheets with a rich relational table.

    Matches the 9-column Google Sheet structure:
    ID | Video Name | Drive Link | Title | Description | Tags | Status | Notes | Source
    Plus Upload-Post publishing, compilations, and the automated scheduler.
    """
    __tablename__ = "queue_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    compilation_id: Mapped[int | None] = mapped_column(ForeignKey("compilations.id"), nullable=True)
    clip_id: Mapped[int | None] = mapped_column(ForeignKey("clips.id"), nullable=True)
    pipeline: Mapped[str] = mapped_column(String(64), default="default", index=True)
    video_name: Mapped[str] = mapped_column(String(255), default="")
    video_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_kind: Mapped[str] = mapped_column(String(16), default="video", server_default="video")
    media_files: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    thumb_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")

    accounts: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="review", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TMDB match + facts used by the AI for this clip (clip_research).
    research: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Posting order within the queue (lower posts first). Mix & Shuffle and
    # manual reordering rewrite it; the scheduler walks Ready items by it.
    position: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    scheduled_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # Owner-pinned posting time: kept as scheduled_at, never re-planned (2026-10-05).
    pinned_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ⏫ Post next: the owner's choice beats the automatic release-date order (audit 2026-10-11).
    post_next_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outstand_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # history (pre-Upload-Post)
    publish_requests: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Upload-Post request_ids
    # Per-platform catch-up (owner, 2026-10-14): {"slots": [slot ISO times it posted in],
    # "done": [[account, url], ...]} while the video still owes a platform whose slot it missed.
    catchup: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AppSetting(Base):
    """Owner-editable settings that override env defaults (e.g. the posting
    schedule set on the Settings page). One JSON value per key."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class EditorialRecord(Base):
    """Private opportunities, experiments and deidentified audience drafts."""
    __tablename__ = "editorial_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class PerformanceObservation(Base):
    """Owner-imported cumulative measurements; missing metrics remain NULL."""
    __tablename__ = "performance_observations"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(String(24), index=True)
    content_id: Mapped[str] = mapped_column(String(255), index=True)
    format: Mapped[str] = mapped_column(String(24), index=True)
    published_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class CreativeCampaign(Base):
    """Approved research package, selected native treatments and immutable renders."""
    __tablename__ = "creative_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("editorial_records.id"))
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    renders: Mapped[dict] = mapped_column(JSON, default=dict)
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    stage_status: Mapped[str] = mapped_column(String(24), default="draft")
    stage_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    file_path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(24))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CreativeExport(Base):
    __tablename__ = "creative_exports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("creative_campaigns.id"), index=True)
    format: Mapped[str] = mapped_column(String(24))
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    approval: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class StudioProject(Base):
    """One LongForm Studio video (a trailer breakdown) and every stage's output.

    Each stage writes its result into its own JSON column (and files under
    data/studio/<id>/), so any stage can be re-run without redoing the others.
    """
    __tablename__ = "studio_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, index=True)
    media_type: Mapped[str] = mapped_column(String(8), default="movie")   # movie | tv
    title: Mapped[str] = mapped_column(String(255), default="")
    target_minutes: Mapped[float] = mapped_column(Float, default=3.0)     # 2–4 min breakdowns

    # Runner state: which stage is running / last ran, and how it went.
    stage: Mapped[str] = mapped_column(String(32), default="new")
    stage_status: Mapped[str] = mapped_column(String(16), default="idle")  # idle|running|done|error
    stage_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # TMDB fact sheet
    research: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # web findings + source URLs
    trailer: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # chosen video, file, origin
    shots: Mapped[list | None] = mapped_column(JSON, nullable=True)      # scene cuts + vision tags
    script: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # sentences, claim checks, metadata
    plan: Mapped[list | None] = mapped_column(JSON, nullable=True)       # sentence -> shot + timing
    render: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # output file + stats
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    drive: Mapped[dict | None] = mapped_column(JSON, nullable=True)      # LongForm Studio Drive copy: links, status
    archive: Mapped[dict | None] = mapped_column(JSON, nullable=True)    # posted_at, archived_at, freed_mb, restored_at
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # auto (started by the automation), reviewed_at

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class CountdownProject(Base):
    """One Countdowns Studio video (Top 5 / 10 / 15), built like a LongForm
    breakdown (owner, 2026-10-12): research → titles → segments → script → render
    → the Countdowns queue. Each title's segment is a StudioProject (segment_ids)."""
    __tablename__ = "countdown_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(255), default="")
    fmt: Mapped[str] = mapped_column(String(8), default="top10")              # top5 | top10 | top15
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)           # extra research instructions
    stage: Mapped[str] = mapped_column(String(32), default="new")            # new|research|titles|segments|script|render
    stage_status: Mapped[str] = mapped_column(String(16), default="idle")    # idle|running|waiting|done|error|stopped
    stage_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ranking: Mapped[dict | None] = mapped_column(JSON, nullable=True)        # researched list: entries, hook, titles…
    segment_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)    # StudioProject ids, lowest rank → #1
    script: Mapped[dict | None] = mapped_column(JSON, nullable=True)         # title, hook, intro, outro, question
    render: Mapped[dict | None] = mapped_column(JSON, nullable=True)         # file, thumbnails, chapters, seconds
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)         # reviewed_at, delete_after, legacy
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UndoEntry(Base):
    """One undo step: the rows as they were before a change (manhwa-style
    snapshot, restored as-is — never an "inverse" operation)."""
    __tablename__ = "undo_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)     # queue | studio:<id> | settings
    label: Mapped[str] = mapped_column(String(255), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ResumableJob(Base):
    """A long background job recorded in the database so a restart or deploy
    can't silently lose it: still "running" at startup = cut off -> resumed."""
    __tablename__ = "resumable_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)       # studio | drive_sync | channel_ingest | bulk_ai | drive_save
    label: Mapped[str] = mapped_column(String(255), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)        # what to run again
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)    # how far it got
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)  # running|done|stopped|failed|budget_paused
    attempts: Mapped[int] = mapped_column(Integer, default=0)       # resumes so far
    owner: Mapped[str | None] = mapped_column(String(40), nullable=True)  # server process running it (heartbeat via updated_at)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class UsageEvent(Base):
    """One paid (or counted) call: which service, what for, how much, est. cost."""
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)          # YYYY-MM (UTC)
    service: Mapped[str] = mapped_column(String(32), index=True)       # gemini | gemini-search | tts | upload-post | tmdb | imdb | drive
    operation: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)   # e.g. studio:1, queue:385
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    chars: Mapped[int] = mapped_column(Integer, default=0)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class ResearchSourceRecord(Base):
    """Immutable independently fetched text; model output never creates these rows."""
    __tablename__ = "research_source_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    url: Mapped[str] = mapped_column(Text)
    requested_url: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    retrieved_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ResearchClaimReview(Base):
    __tablename__ = "research_claim_reviews"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    record_id: Mapped[str] = mapped_column(String(36))
    claim: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSON)
    verdict: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    conflicts: Mapped[list] = mapped_column(JSON, default=list)
    reviewed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BudgetMutex(Base):
    __tablename__ = "budget_mutex"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class BudgetReservation(Base):
    __tablename__ = "budget_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    service: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(64))
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reserved_micros: Mapped[int] = mapped_column(BigInteger)
    actual_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(String(16), index=True)  # reserved/dispatched/unknown/settled/released
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WorkerLease(Base):
    __tablename__ = "worker_leases"
    resource: Mapped[str] = mapped_column(String(160), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(40), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))


class CreditReservation(Base):
    __tablename__ = "credit_reservations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tool: Mapped[str] = mapped_column(String(100))
    credits: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BudgetResolution(Base):
    __tablename__ = "budget_resolutions"
    reservation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actual_micros: Mapped[int] = mapped_column(BigInteger)
    receipt: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now)
