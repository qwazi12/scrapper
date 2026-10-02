"""Database models: clips, ingest jobs, compilations, and log entries."""

from __future__ import annotations

import datetime
import enum

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
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
    published_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outstand_post_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # history (pre-Upload-Post)
    publish_requests: Mapped[list | None] = mapped_column(JSON, nullable=True)  # Upload-Post request_ids
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class AppSetting(Base):
    """Owner-editable settings that override env defaults (e.g. the posting
    schedule set on the Settings page). One JSON value per key."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


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
