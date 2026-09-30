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


class MetadataGenerateRequest(BaseModel):
    compilation_id: int
    prompt: str | None = None


class MetadataGenerateResponse(BaseModel):
    title: str
    caption: str
    hashtags: list[str]
    full_text: str
    model: str


class SocialPublishRequest(BaseModel):
    compilation_id: int
    account_ids: list[str]
    content: str
    scheduled_at: str | None = None


class SocialPostOut(BaseModel):
    id: int
    compilation_id: int | None
    outstand_post_id: str | None
    accounts: list
    content: str
    media_url: str | None
    scheduled_at: datetime.datetime | None
    status: str
    error: str | None
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class QueueItemCreate(BaseModel):
    compilation_id: int | None = None
    clip_id: int | None = None
    pipeline: str = "default"
    title: str = ""
    description: str = ""
    tags: str = ""
    source: str | None = None
    drive_link: str | None = None
    accounts: list[str] = []
    status: str = "review"
    scheduled_at: str | None = None


class QueueItemUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: str | None = None
    source: str | None = None
    drive_link: str | None = None
    pipeline: str | None = None
    status: str | None = None
    accounts: list[str] | None = None
    scheduled_at: str | None = None
    notes: str | None = None


class QueueItemOut(BaseModel):
    id: int
    compilation_id: int | None
    clip_id: int | None
    pipeline: str
    video_name: str
    video_path: str | None
    thumb_path: str | None
    drive_link: str | None
    source: str | None
    title: str
    description: str
    tags: str
    accounts: list
    status: str
    notes: str | None
    scheduled_at: datetime.datetime | None
    published_at: datetime.datetime | None
    outstand_post_id: str | None
    media_url: str | None
    created_at: datetime.datetime

    class Config:
        from_attributes = True


class QueueBulkAction(BaseModel):
    ids: list[int]
    action: str  # "approve", "archive", "delete", "generate_ai"
    pipeline: str | None = None


class DriveSyncRequest(BaseModel):
    folder_url: str | None = None
    folder_id: str
    pipeline: str = "Movie Clips"
    auto_approve: bool = False

