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
