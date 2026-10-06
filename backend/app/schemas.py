"""Pydantic request/response shapes."""

from __future__ import annotations

import datetime

from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def _as_utc(v: datetime.datetime | None) -> datetime.datetime | None:
    # The DB columns hold UTC without a zone, so values come back naive. Tag
    # them as UTC so the JSON says "...Z"/"+00:00"; a bare time is read by
    # browsers as *local* time (that showed 8pm ET slots as "Fri 12:00 AM").
    if v is not None and v.tzinfo is None:
        return v.replace(tzinfo=datetime.timezone.utc)
    return v


UTCDateTime = Annotated[datetime.datetime, AfterValidator(_as_utc)]


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
    created_at: UTCDateTime

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
    created_at: UTCDateTime
    finished_at: UTCDateTime | None

    class Config:
        from_attributes = True


class LogOut(BaseModel):
    id: int
    level: str
    event: str
    message: str
    context: dict | None
    created_at: UTCDateTime

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
    publish_requests: list | None = None
    accounts: list
    content: str
    scheduled_at: UTCDateTime | None
    status: str
    error: str | None
    created_at: UTCDateTime

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
    pinned_at: str | None = None     # ISO time to lock this video's post time; "" unpins
    notes: str | None = None
    media_url: str | None = None


class QueueItemOut(BaseModel):
    id: int
    compilation_id: int | None
    clip_id: int | None
    pipeline: str
    video_name: str
    video_path: str | None
    thumb_path: str | None
    thumb_version: float | None = None      # file mtime: changes when the thumbnail does (cache-buster)
    drive_link: str | None
    source: str | None
    title: str
    description: str
    tags: str
    accounts: list
    status: str
    notes: str | None
    position: int | None = None
    scheduled_at: UTCDateTime | None
    pinned_at: UTCDateTime | None = None
    published_at: UTCDateTime | None
    publish_requests: list | None = None
    research: dict | None = None
    media_url: str | None = None
    created_at: UTCDateTime

    class Config:
        from_attributes = True


class QueueBulkAction(BaseModel):
    ids: list[int]
    action: str  # "approve", "review", "archive", "posted", "delete", "set_accounts", "change_status", "edit"
    pipeline: str | None = None
    target_status: str | None = None
    accounts: list[str] | None = None
    # "edit": any of these that are set are applied to every selected item.
    title: str | None = None
    description: str | None = None
    tags: str | None = None


class PacingRuleIn(BaseModel):
    posts_per_day: int | None = None
    start_hour: int | None = None
    end_hour: int | None = None
    interval_hours: int | None = None
    times: list[str] | None = None      # exact posting times "HH:MM" (win over posts_per_day)


class ScheduleConfigIn(BaseModel):
    timezone: str = "America/New_York"
    start_hour: int = 8
    end_hour: int = 22
    interval_hours: int = 2
    posts_per_day: int | None = None
    pipelines: dict[str, PacingRuleIn] = Field(default_factory=dict)
    accounts: dict[str, PacingRuleIn] = Field(default_factory=dict)
    pipeline_overrides: dict[str, PacingRuleIn] = Field(default_factory=dict)
    account_overrides: dict[str, PacingRuleIn] = Field(default_factory=dict)
    reset: bool = False  # true = drop the override, back to env defaults


class QueueShuffleRequest(BaseModel):
    mode: str = "round_robin"  # "round_robin", "random", "by_channel"
    pipeline: str | None = None
    status: str | None = None


class ChannelIngestRequest(BaseModel):
    url: str
    parent_folder_id: str = "1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo"  # Movie Clips
    parent_folder_name: str = "Movie Clips"
    channel_name: str | None = None
    max_videos: int = 25
    auto_approve: bool = False
    upload_to_drive: bool = True


class DriveSyncRequest(BaseModel):
    folder_url: str | None = None
    folder_id: str
    pipeline: str = "Movie Clips"
    auto_approve: bool = False


class TTSGenerateRequest(BaseModel):
    text: str
    voice: str = "Puck"
    style: str | None = None
    model: str = "gemini-3.8-flash-tts"


class TTSGenerateResponse(BaseModel):
    ok: bool
    audio_url: str
    filename: str
    duration_seconds: float
    voice: str
    model: str
    text: str


class HookVoiceoverRequest(BaseModel):
    voice: str = "Puck"
    style: str = "high energy and enthusiastic"
    model: str = "gemini-3.8-flash-tts"


