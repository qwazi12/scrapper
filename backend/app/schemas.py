"""Pydantic request/response shapes."""

from __future__ import annotations

import datetime

from pydantic import BaseModel


class IngestRequest(BaseModel):
    urls: list[str]


class CompileRequest(BaseModel):
    clip_ids: list[int] | None = None   # if omitted, uses all currently-selected clips
    orientation: str = "portrait"


class SelectRequest(BaseModel):
    selected: bool


class ClipOut(BaseModel):
    id: int
    job_id: int | None
    source_url: str
    platform: str | None
    uploader: str | None
    title: str | None
    duration: float | None
    width: int | None
    height: int | None
    size_bytes: int | None
    status: str
    error: str | None
    selected: bool
    has_thumb: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class CompilationOut(BaseModel):
    id: int
    clip_ids: list[int]
    orientation: str
    status: str
    progress: float
    duration: float | None
    size_bytes: int | None
    error: str | None
    created_at: datetime.datetime
    finished_at: datetime.datetime | None

    class Config:
        from_attributes = True


class LogOut(BaseModel):
    id: int
    level: str
    event: str
    message: str
    context: dict | None
    created_at: datetime.datetime

    class Config:
        from_attributes = True
