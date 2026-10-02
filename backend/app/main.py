"""Scrapper API — FastAPI app.

Sections:  ingest (paste links) · clips (storyboard table) · compile (Extract)
           · compilations/download (Export) · logs + stats + events (observability)
"""

from __future__ import annotations

import datetime
import json
import pathlib
import queue
import time

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from ..core import engine
from . import cleanup, logbus, rescan, worker
from .auth import require_token
from .config import settings
from .db import SessionLocal, get_session, init_db
from .models import Clip, Compilation, IngestJob, LogEntry, QueueItem, SocialPost, Status
from .schemas import (
    ChannelIngestRequest,
    ClipOut,
    CompilationOut,
    CompileRequest,
    DriveSyncRequest,
    IngestRequest,
    LogOut,
    MetadataGenerateRequest,
    MetadataGenerateResponse,
    QueueBulkAction,
    QueueItemCreate,
    QueueItemOut,
    QueueItemUpdate,
    QueueShuffleRequest,
    ScheduleConfigIn,
    SelectRequest,
    SocialPostOut,
    SocialPublishRequest,
)
from .social import ai_bulk, metadata as social_metadata, queue_manager, upload_post

app = FastAPI(title="Scrapper API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AUTH = [Depends(require_token)]

# LongForm Studio: importing the stage modules registers them with the runner.
from .studio import routes as studio_routes, stage_gather, stage_plan, stage_render, stage_script, stage_shots, stage_trailer  # noqa: E402,F401

app.include_router(studio_routes.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    # Self-heal: rebuild any missing clip/compilation rows from files on the
    # volume, so a lost or rolled-back DB recovers automatically.
    try:
        with SessionLocal() as s:
            rescan.rescan_library(s)
    except Exception as exc:  # never block startup on recovery
        logbus.log("error", "startup_rescan_failed", str(exc))
    if settings.worker_mode != "web_only":
        worker.start_background()
        queue_manager.start_scheduler_thread()
    logbus.log("info", "startup", f"API up (worker_mode={settings.worker_mode})")


def _clip_out(c: Clip) -> ClipOut:
    return ClipOut(
        id=c.id, job_id=c.job_id, source_url=c.source_url, platform=c.platform,
        uploader=c.uploader, title=c.title, duration=c.duration, width=c.width,
        height=c.height, size_bytes=c.size_bytes, status=c.status.value,
        error=c.error, selected=c.selected, has_thumb=bool(c.thumb_path),
        created_at=c.created_at,
    )


# --- health / stats ----------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, **engine.health()}


@app.get("/api/stats", dependencies=_AUTH)
def stats(s: Session = Depends(get_session)) -> dict:
    clips = s.query(Clip).all()
    comps = s.query(Compilation).all()
    return {
        "clips_total": len(clips),
        "clips_selected": sum(1 for c in clips if c.selected and c.status == Status.done),
        "clips_done": sum(1 for c in clips if c.status == Status.done),
        "clips_failed": sum(1 for c in clips if c.status == Status.failed),
        "compilations": len(comps),
        "storage_bytes": sum((c.size_bytes or 0) for c in clips)
        + sum((c.size_bytes or 0) for c in comps),
        "retention_days": settings.retention_days,
        "sources": logbus.source_stats(),
        "engine": engine.health(),
    }


# --- ingest ------------------------------------------------------------------
@app.post("/api/ingest", dependencies=_AUTH)
def ingest(req: IngestRequest, s: Session = Depends(get_session)) -> dict:
    urls = [u.strip() for u in req.urls if u.strip()]
    if not urls:
        raise HTTPException(400, "no urls provided")
    job = IngestJob(urls=urls, total=len(urls))
    s.add(job)
    s.commit()
    logbus.log("info", "ingest_queued", f"job {job.id}: {len(urls)} url(s)")
    return {"job_id": job.id, "count": len(urls)}


@app.get("/api/jobs/{job_id}", dependencies=_AUTH)
def job_status(job_id: int, s: Session = Depends(get_session)) -> dict:
    job = s.get(IngestJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {
        "id": job.id, "status": job.status.value, "total": job.total,
        "done": job.done_count, "failed": job.failed_count,
        "clips": [_clip_out(c) for c in job.clips],
    }


# --- clips (storyboard) ------------------------------------------------------
@app.get("/api/clips", response_model=list[ClipOut], dependencies=_AUTH)
def list_clips(s: Session = Depends(get_session)) -> list[ClipOut]:
    clips = s.query(Clip).order_by(Clip.id).all()
    return [_clip_out(c) for c in clips]


@app.post("/api/clips/{clip_id}/select", dependencies=_AUTH)
def set_selected(clip_id: int, req: SelectRequest, s: Session = Depends(get_session)) -> dict:
    clip = s.get(Clip, clip_id)
    if not clip:
        raise HTTPException(404, "clip not found")
    clip.selected = req.selected
    s.commit()
    return {"id": clip_id, "selected": clip.selected}


@app.delete("/api/clips/{clip_id}", dependencies=_AUTH)
def delete_clip(clip_id: int, s: Session = Depends(get_session)) -> dict:
    clip = s.get(Clip, clip_id)
    if not clip:
        raise HTTPException(404, "clip not found")
    cleanup.remove_clip_files(clip)
    s.delete(clip)
    s.commit()
    return {"deleted": clip_id}


@app.post("/api/rescan", dependencies=_AUTH)
def rescan_now(s: Session = Depends(get_session)) -> dict:
    """Rebuild clip + compilation rows from the video files on the volume."""
    return rescan.rescan_library(s)


# --- hybrid local worker (Reliability Layer 5) -------------------------------
# YouTube/TikTok refuse this container's datacenter IP outright. A worker on a
# residential connection (the user's Mac) claims those blocked URLs, downloads
# them at home, and uploads the result back here.
@app.get("/api/clips/blocked", dependencies=_AUTH)
def blocked_clips(s: Session = Depends(get_session)) -> list[dict]:
    """URLs this server could not fetch, for a local worker to pick up."""
    rows = (
        s.query(Clip)
        .filter(Clip.status == Status.failed, Clip.file_path.is_(None))
        .order_by(Clip.id)
        .all()
    )
    return [
        {"id": c.id, "source_url": c.source_url, "platform": c.platform, "error": c.error}
        for c in rows
    ]


@app.post("/api/clips/{clip_id}/upload", dependencies=_AUTH)
async def upload_clip(
    clip_id: int,
    file: UploadFile = File(...),
    thumb: UploadFile | None = File(default=None),
    # Must be Form(...) — a bare `str` default is read as a query param, which
    # silently drops the multipart field and loses all the metadata.
    meta: str = Form(default="{}"),
    s: Session = Depends(get_session),
) -> dict:
    """Accept a video downloaded by a local worker and attach it to its clip."""
    clip = s.get(Clip, clip_id)
    if not clip:
        raise HTTPException(404, "clip not found")

    try:
        info = json.loads(meta) if meta else {}
    except json.JSONDecodeError:
        info = {}

    dest_dir = settings.downloads_path / (clip.platform or info.get("extractor") or "other")
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = pathlib.Path(file.filename or f"clip_{clip_id}.mp4").name
    dest = dest_dir / safe_name

    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            fh.write(chunk)
            size += len(chunk)

    if thumb is not None:
        thumb_dest = dest.with_suffix(".jpg")
        thumb_dest.write_bytes(await thumb.read())
        clip.thumb_path = str(thumb_dest)

    if info:
        dest.with_suffix(".info.json").write_text(json.dumps(info))

    clip.file_path = str(dest)
    clip.size_bytes = size
    clip.video_id = info.get("id") or clip.video_id
    clip.uploader = info.get("uploader") or info.get("uploader_id") or clip.uploader
    clip.title = info.get("title") or clip.title
    clip.duration = info.get("duration") or clip.duration
    clip.width = info.get("width") or clip.width
    clip.height = info.get("height") or clip.height
    clip.status = Status.done
    clip.error = None
    clip.selected = True
    s.commit()

    logbus.log("info", "local_upload",
               f"{clip.title or clip.source_url} ({round(size / 1e6)} MB) via local worker",
               source=clip.platform)
    return {"clip_id": clip_id, "size_bytes": size, "status": "done"}


@app.get("/api/debug/storage", dependencies=_AUTH)
def debug_storage() -> dict:
    """Ops helper: what's actually on the volume, and where."""
    root = settings.data_path
    mp4s, biggest = [], []
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                sz = p.stat().st_size
                total += sz
                if p.suffix == ".mp4":
                    mp4s.append(str(p))
                biggest.append((sz, str(p)))
        except Exception:
            pass
    biggest.sort(reverse=True)
    return {
        "root": str(root),
        "data_dir_env": settings.data_dir,
        "downloads_path": str(settings.downloads_path),
        "compilations_path": str(settings.compilations_path),
        "top_level": sorted(x.name for x in root.iterdir()),
        "total_mb": round(total / 1e6, 1),
        "mp4_count": len(mp4s),
        "mp4_sample": mp4s[:20],
        "biggest_10": [{"mb": round(s / 1e6, 1), "path": p} for s, p in biggest[:10]],
    }


@app.get("/api/clips/{clip_id}/thumb", dependencies=_AUTH)
def clip_thumb(clip_id: int, s: Session = Depends(get_session)):
    clip = s.get(Clip, clip_id)
    if not clip or not clip.thumb_path or not pathlib.Path(clip.thumb_path).exists():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(clip.thumb_path, media_type="image/jpeg")


@app.get("/api/clips/{clip_id}/download", dependencies=_AUTH)
def download_clip(clip_id: int, s: Session = Depends(get_session)):
    clip = s.get(Clip, clip_id)
    if not clip or not clip.file_path or not pathlib.Path(clip.file_path).exists():
        raise HTTPException(404, "clip file not found on disk")
    filename = pathlib.Path(clip.file_path).name
    if clip.title:
        safe_title = "".join(c for c in clip.title if c.isalnum() or c in (" ", "-", "_")).strip()
        if safe_title:
            filename = f"{safe_title}.mp4"
    return FileResponse(clip.file_path, media_type="video/mp4", filename=filename)


# --- compile (Extract) -------------------------------------------------------
@app.post("/api/compile", dependencies=_AUTH)
def compile_selected(req: CompileRequest, s: Session = Depends(get_session)) -> dict:
    if req.clip_ids:
        clip_ids = req.clip_ids
    else:
        clip_ids = [
            c.id for c in s.query(Clip)
            .filter(Clip.selected.is_(True), Clip.status == Status.done)
            .order_by(Clip.id).all()
        ]
    if not clip_ids:
        raise HTTPException(400, "no clips selected to compile")
    comp = Compilation(clip_ids=clip_ids, orientation=req.orientation)
    s.add(comp)
    s.commit()
    logbus.log("info", "compile_queued", f"compilation {comp.id}: {len(clip_ids)} clips")
    return {"compilation_id": comp.id, "count": len(clip_ids)}


# --- compilations / download (Export) ---------------------------------------
@app.get("/api/compilations", response_model=list[CompilationOut], dependencies=_AUTH)
def list_compilations(s: Session = Depends(get_session)) -> list[Compilation]:
    return s.query(Compilation).order_by(desc(Compilation.id)).all()


@app.get("/api/compilations/{comp_id}/download", dependencies=_AUTH)
def download_compilation(comp_id: int, s: Session = Depends(get_session)):
    comp = s.get(Compilation, comp_id)
    if not comp or not comp.output_path or not pathlib.Path(comp.output_path).exists():
        raise HTTPException(404, "compilation not ready")
    return FileResponse(comp.output_path, media_type="video/mp4",
                        filename=pathlib.Path(comp.output_path).name)


@app.delete("/api/compilations/{comp_id}", dependencies=_AUTH)
def delete_compilation(comp_id: int, s: Session = Depends(get_session)) -> dict:
    comp = s.get(Compilation, comp_id)
    if not comp:
        raise HTTPException(404, "not found")
    cleanup.remove_compilation_files(comp)
    s.delete(comp)
    s.commit()
    return {"deleted": comp_id}


# --- cookies upload (Reliability Layer 2) -----------------------------------
@app.post("/api/cookies", dependencies=_AUTH)
async def upload_cookies(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    settings.cookies_path.write_bytes(data)
    logbus.log("info", "cookies_uploaded", f"{len(data)} bytes")
    return {"ok": True, "bytes": len(data)}


@app.get("/api/cookies", dependencies=_AUTH)
def cookies_status() -> dict:
    p = settings.cookies_path
    return {"present": p.exists(), "bytes": p.stat().st_size if p.exists() else 0}


# --- logs + live events (observability) -------------------------------------
@app.get("/api/logs", response_model=list[LogOut], dependencies=_AUTH)
def get_logs(limit: int = 200, since: int = 0, s: Session = Depends(get_session)) -> list[LogEntry]:
    q = s.query(LogEntry)
    if since:
        q = q.filter(LogEntry.id > since)
    return list(reversed(q.order_by(desc(LogEntry.id)).limit(limit).all()))


@app.get("/api/events", dependencies=_AUTH)
def events():
    """Server-Sent Events stream of live log lines for the Logs panel."""
    def gen():
        q = logbus.subscribe()
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                try:
                    payload = q.get(timeout=15)
                    yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            logbus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --- social publishing (Upload-Post) & AI metadata (Gemini) ----------------
@app.get("/api/social/accounts", dependencies=_AUTH)
async def list_social_accounts() -> dict:
    if not upload_post.configured():
        return {"configured": False, "accounts": [], "provider": "Upload-Post",
                "manage_url": upload_post.MANAGE_URL,
                "message": "UPLOADPOST_API_KEY is not set — publishing is blocked"}
    try:
        accounts = await upload_post.list_accounts()
        profiles = await upload_post.list_profiles()
        return {"configured": True, "accounts": accounts, "profiles": profiles, "provider": "Upload-Post",
                "manage_url": upload_post.MANAGE_URL, "privacy": settings.publish_privacy}
    except Exception as exc:
        raise HTTPException(502, f"Upload-Post API error: {exc}")


@app.get("/api/social/connect-url", dependencies=_AUTH)
async def social_connect_url(profile: str) -> dict:
    """Hosted Upload-Post page to connect channels to a profile."""
    try:
        return {"url": await upload_post.connect_url(profile)}
    except Exception as exc:
        raise HTTPException(502, f"Upload-Post API error: {exc}")


@app.post("/api/social/generate-metadata", response_model=MetadataGenerateResponse, dependencies=_AUTH)
async def generate_metadata(req: MetadataGenerateRequest, s: Session = Depends(get_session)):
    comp = s.get(Compilation, req.compilation_id)
    if not comp:
        raise HTTPException(404, "compilation not found")

    clip_titles = []
    if comp.clip_ids:
        clips = s.query(Clip).filter(Clip.id.in_(comp.clip_ids)).all()
        clip_titles = [c.title for c in clips if c.title]

    res = await social_metadata.generate_social_metadata(
        clip_titles=clip_titles,
        user_prompt=req.prompt,
    )
    return MetadataGenerateResponse(
        title=res.get("title", "Compilation"),
        caption=res.get("caption", ""),
        hashtags=res.get("hashtags", []),
        full_text=res.get("full_text", ""),
        model=res.get("model", "template"),
    )


@app.post("/api/social/publish", response_model=SocialPostOut, dependencies=_AUTH)
async def publish_social_post(req: SocialPublishRequest, s: Session = Depends(get_session)):
    comp = s.get(Compilation, req.compilation_id)
    if not comp or not comp.output_path or not pathlib.Path(comp.output_path).exists():
        raise HTTPException(404, "compilation video not found or not finished")
    if not req.account_ids:
        raise HTTPException(400, "pick at least one account")

    video_path = pathlib.Path(comp.output_path)
    title, _, rest = req.content.strip().partition("\n")
    logbus.log("info", "social_upload_start",
               f"Uploading compilation {comp.id} to Upload-Post for {', '.join(req.account_ids)}")
    try:
        entries = await queue_manager.submit_upload(
            video_path, req.account_ids, title=title or f"Compilation #{comp.id}",
            description=req.content.strip(), tags=[], scheduled_date=req.scheduled_at,
        )
    except upload_post.UploadPostError as exc:
        raise HTTPException(exc.status_code if exc.status_code < 500 else 502, exc.message)
    if not any(e.get("request_id") for e in entries):
        raise HTTPException(502, "Upload-Post rejected the upload: " + "; ".join(e.get("error", "") for e in entries))

    sched_dt = None
    if req.scheduled_at:
        try:
            sched_dt = datetime.datetime.fromisoformat(req.scheduled_at.replace("Z", "+00:00"))
        except Exception:
            pass

    # "submitted" until the scheduler reads the real platform result.
    post_record = SocialPost(
        compilation_id=comp.id,
        publish_requests=entries,
        accounts=req.account_ids,
        content=req.content,
        scheduled_at=sched_dt,
        status="submitted",
    )
    s.add(post_record)
    s.commit()
    s.refresh(post_record)
    logbus.log("info", "social_submitted", f"Compilation {comp.id} submitted to Upload-Post (post {post_record.id})")
    return post_record


@app.get("/api/social/posts", response_model=list[SocialPostOut], dependencies=_AUTH)
def list_social_posts(s: Session = Depends(get_session)) -> list[SocialPost]:
    return s.query(SocialPost).order_by(desc(SocialPost.id)).limit(50).all()


MOVIE_CLIPS_CHANNELS = queue_manager.MOVIE_CLIPS_CHANNELS


def _mark_published(it: QueueItem, status: str) -> None:
    # Posted/archived items start their ARCHIVE_DELETE_DAYS countdown now.
    if status in ("posted", "archived") and it.published_at is None:
        it.published_at = datetime.datetime.now(datetime.timezone.utc)

@app.get("/api/queue", response_model=list[QueueItemOut], dependencies=_AUTH)
def list_queue(
    pipeline: str | None = None,
    status: str | None = None,
    s: Session = Depends(get_session),
) -> list[QueueItem]:
    q = s.query(QueueItem)
    if pipeline and pipeline != "all":
        if pipeline.lower() in ("movie clips", "movie_clips"):
            q = q.filter(
                or_(
                    QueueItem.pipeline.in_(MOVIE_CLIPS_CHANNELS),
                    QueueItem.source.ilike("%Movie Clips%"),
                    QueueItem.pipeline == "Movie Clips",
                )
            )
        else:
            q = q.filter(QueueItem.pipeline == pipeline)

    if status and status != "all":
        if status in ("error", "retry"):
            q = q.filter(QueueItem.status.in_(["error", "retry"]))
        else:
            q = q.filter(QueueItem.status == status)

    # Posting order (next to post first); the UI can re-sort either direction.
    return q.order_by(func.coalesce(QueueItem.position, QueueItem.id), QueueItem.id).all()


@app.get("/api/queue/counts", dependencies=_AUTH)
def get_queue_counts(
    pipeline: str | None = None,
    s: Session = Depends(get_session),
) -> dict:
    q = s.query(QueueItem.status, func.count(QueueItem.id))
    if pipeline and pipeline != "all":
        if pipeline.lower() in ("movie clips", "movie_clips"):
            q = q.filter(
                or_(
                    QueueItem.pipeline.in_(MOVIE_CLIPS_CHANNELS),
                    QueueItem.source.ilike("%Movie Clips%"),
                    QueueItem.pipeline == "Movie Clips",
                )
            )
        else:
            q = q.filter(QueueItem.pipeline == pipeline)

    rows = q.group_by(QueueItem.status).all()
    counts = {
        "all": 0,
        "review": 0,
        "ready": 0,
        "posting": 0,
        "posted": 0,
        "retry": 0,
        "error": 0,
        "archived": 0,
        "errors_total": 0,
    }
    for status_val, count_val in rows:
        st = (status_val or "").lower()
        counts[st] = count_val
        counts["all"] += count_val

    counts["errors_total"] = counts.get("error", 0) + counts.get("retry", 0)
    return counts



@app.post("/api/queue", response_model=QueueItemOut, dependencies=_AUTH)
async def create_queue_item(req: QueueItemCreate, s: Session = Depends(get_session)):
    video_path = None
    thumb_path = None
    video_name = req.title or "Untitled"
    source = req.source

    if req.compilation_id:
        comp = s.get(Compilation, req.compilation_id)
        if not comp:
            raise HTTPException(404, "compilation not found")
        video_path = comp.output_path
        video_name = f"Compilation #{comp.id}"
        if not source:
            source = f"Compilation ({comp.orientation}, {len(comp.clip_ids)} clips)"
        if not req.title:
            clip_titles = []
            if comp.clip_ids:
                clips = s.query(Clip).filter(Clip.id.in_(comp.clip_ids)).all()
                clip_titles = [c.title for c in clips if c.title]
            ai_res = await social_metadata.generate_social_metadata(clip_titles)
            req.title = ai_res.get("title", video_name)
            if not req.description:
                req.description = ai_res.get("caption", "")
            if not req.tags:
                req.tags = " ".join(ai_res.get("hashtags", []))

    elif req.clip_id:
        clip = s.get(Clip, req.clip_id)
        if not clip:
            raise HTTPException(404, "clip not found")
        video_path = clip.file_path
        thumb_path = clip.thumb_path
        video_name = clip.title or f"Clip #{clip.id}"
        if not source:
            source = clip.uploader or clip.platform or "Clip"
        if not req.title:
            req.title = clip.title or video_name

    sched_dt = None
    if req.scheduled_at:
        try:
            sched_dt = datetime.datetime.fromisoformat(req.scheduled_at.replace("Z", "+00:00"))
        except Exception:
            pass

    # Idempotency guard: prevent duplicate queue items
    existing = None
    if req.drive_link:
        existing = s.query(QueueItem).filter(QueueItem.drive_link == req.drive_link).first()
    elif video_name and req.pipeline:
        existing = s.query(QueueItem).filter(
            QueueItem.video_name == video_name,
            QueueItem.pipeline == (req.pipeline or "default")
        ).first()

    if existing:
        return existing

    item = QueueItem(
        compilation_id=req.compilation_id,
        clip_id=req.clip_id,
        pipeline=req.pipeline or "default",
        video_name=video_name,
        video_path=video_path,
        thumb_path=thumb_path,
        drive_link=req.drive_link,
        source=source,
        title=req.title or video_name,
        description=req.description,
        tags=req.tags,
        accounts=req.accounts,
        status=req.status or "review",
        scheduled_at=sched_dt,
        position=(s.query(func.max(QueueItem.position)).scalar() or 0) + 1,
    )
    s.add(item)
    s.commit()
    s.refresh(item)
    logbus.log("info", "queue_created", f"Queue item #{item.id} ('{item.title[:40]}') [{item.status}]")
    return item


@app.patch("/api/queue/{item_id}", response_model=QueueItemOut, dependencies=_AUTH)
def update_queue_item(item_id: int, req: QueueItemUpdate, s: Session = Depends(get_session)):
    item = s.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "queue item not found")

    if req.title is not None:
        item.title = req.title
    if req.description is not None:
        item.description = req.description
    if req.tags is not None:
        item.tags = req.tags
    if req.source is not None:
        item.source = req.source
    if req.drive_link is not None:
        item.drive_link = req.drive_link
    if req.pipeline is not None:
        item.pipeline = req.pipeline
    if req.status is not None:
        item.status = req.status
        _mark_published(item, req.status)
    if req.accounts is not None:
        item.accounts = req.accounts
    if req.notes is not None:
        item.notes = req.notes
    if req.scheduled_at is not None:
        if req.scheduled_at == "":
            item.scheduled_at = None
        else:
            try:
                item.scheduled_at = datetime.datetime.fromisoformat(req.scheduled_at.replace("Z", "+00:00"))
            except Exception:
                pass

    s.commit()
    s.refresh(item)
    return item


@app.post("/api/queue/{item_id}/approve", response_model=QueueItemOut, dependencies=_AUTH)
def approve_queue_item(item_id: int, s: Session = Depends(get_session)):
    item = s.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "queue item not found")
    item.status = "ready"
    s.commit()
    s.refresh(item)
    logbus.log("info", "queue_approved", f"Item #{item.id} approved -> ready to post")
    return item


@app.post("/api/queue/{item_id}/publish", response_model=QueueItemOut, dependencies=_AUTH)
async def publish_queue_item_now(item_id: int, s: Session = Depends(get_session)):
    try:
        item = await queue_manager.publish_queue_item(item_id, s)
        return item
    except upload_post.UploadPostError as exc:
        raise HTTPException(exc.status_code if exc.status_code < 500 else 502, f"Publishing failed: {exc.message}")
    except Exception as exc:
        raise HTTPException(502, f"Publishing failed: {exc}")


@app.post("/api/queue/{item_id}/generate-ai", response_model=QueueItemOut, dependencies=_AUTH)
async def generate_queue_item_ai(item_id: int, s: Session = Depends(get_session)):
    item = s.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "queue item not found")

    clip_titles = ai_bulk.ai_inputs(item, s)

    try:
        res = await social_metadata.generate_social_metadata(clip_titles, strict=True)
    except social_metadata.MetadataError as exc:
        raise HTTPException(502, f"AI generation failed: {exc}")
    ai_bulk.apply_ai(item, res)
    s.commit()
    s.refresh(item)
    return item


@app.delete("/api/queue/{item_id}", dependencies=_AUTH)
def delete_queue_item(item_id: int, s: Session = Depends(get_session)) -> dict:
    item = s.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "queue item not found")
    s.delete(item)
    s.commit()
    return {"deleted": item_id}


@app.post("/api/queue/bulk-action", dependencies=_AUTH)
def bulk_queue_action(req: QueueBulkAction, s: Session = Depends(get_session)) -> dict:
    items = s.query(QueueItem).filter(QueueItem.id.in_(req.ids)).all()
    count = len(items)

    if req.action == "approve":
        for it in items:
            it.status = "ready"
        s.commit()
        logbus.log("info", "queue_bulk_approved", f"Approved {count} items -> ready to post")
    elif req.action == "review":
        for it in items:
            it.status = "review"
        s.commit()
        logbus.log("info", "queue_bulk_review", f"Set {count} items -> review")
    elif req.action == "archive":
        for it in items:
            it.status = "archived"
            _mark_published(it, "archived")
        s.commit()
        logbus.log("info", "queue_bulk_archived", f"Archived {count} items")
    elif req.action == "posted":
        for it in items:
            it.status = "posted"
            _mark_published(it, "posted")
        s.commit()
        logbus.log("info", "queue_bulk_posted", f"Set {count} items -> posted")
    elif req.action == "change_status" and req.target_status:
        for it in items:
            it.status = req.target_status
            _mark_published(it, req.target_status)
        s.commit()
        logbus.log("info", "queue_bulk_status", f"Changed {count} items to {req.target_status}")
    elif req.action == "set_accounts" and req.accounts is not None:
        for it in items:
            it.accounts = req.accounts
        s.commit()
        logbus.log("info", "queue_bulk_accounts", f"Assigned {len(req.accounts)} account(s) to {count} items")
    elif req.action == "edit":
        # Mass edit: only fields that were sent are changed.
        fields = {k: v for k, v in (
            ("title", req.title), ("description", req.description),
            ("tags", req.tags), ("pipeline", req.pipeline),
        ) if v is not None}
        if not fields:
            raise HTTPException(400, "edit needs at least one of title, description, tags, pipeline")
        for it in items:
            for k, v in fields.items():
                setattr(it, k, v)
            # Keep the "<pipeline> / @channel" label in step with a pipeline move.
            if "pipeline" in fields and it.source and " / " in it.source:
                it.source = f"{fields['pipeline']} / {it.source.split(' / ', 1)[1]}"
        s.commit()
        logbus.log("info", "queue_bulk_edit", f"Edited {', '.join(fields)} on {count} items",
                   ids=[it.id for it in items])
    elif req.action == "delete":
        for it in items:
            s.delete(it)
        s.commit()
        logbus.log("info", "queue_bulk_deleted", f"Deleted {count} items")
    else:
        raise HTTPException(400, f"Unknown action: {req.action}")

    return {"ok": True, "count": count, "action": req.action}


@app.post("/api/queue/shuffle", dependencies=_AUTH)
def shuffle_queue(req: QueueShuffleRequest, s: Session = Depends(get_session)) -> dict:
    """
    Mix & shuffle queue items:
    - 'round_robin': Interleave items across all channels (prevents back-to-back channel posts).
    - 'random': Random Fisher-Yates shuffle.
    - 'by_channel': Shuffles within each channel.
    """
    import random
    from collections import defaultdict

    q = s.query(QueueItem)
    if req.pipeline and req.pipeline != "all":
        if req.pipeline.lower() in ("movie clips", "movie_clips"):
            q = q.filter(
                or_(
                    QueueItem.pipeline.in_(MOVIE_CLIPS_CHANNELS),
                    QueueItem.source.ilike("%Movie Clips%"),
                    QueueItem.pipeline == "Movie Clips",
                )
            )
        else:
            q = q.filter(QueueItem.pipeline == req.pipeline)
    if req.status and req.status != "all":
        q = q.filter(QueueItem.status == req.status)

    items = q.all()
    if not items:
        return {"ok": True, "count": 0, "message": "No items to shuffle"}

    if req.mode == "round_robin":
        # Group by channel/pipeline
        grouped = defaultdict(list)
        for it in items:
            grouped[it.pipeline].append(it)
        for ch in grouped:
            random.shuffle(grouped[ch])

        interleaved = []
        max_len = max(len(v) for v in grouped.values())
        channels = list(grouped.keys())
        random.shuffle(channels)
        for idx in range(max_len):
            for ch in channels:
                if idx < len(grouped[ch]):
                    interleaved.append(grouped[ch][idx])
        items = interleaved

    elif req.mode == "random":
        random.shuffle(items)

    elif req.mode == "by_channel":
        grouped = defaultdict(list)
        for it in items:
            grouped[it.pipeline].append(it)
        items = []
        for ch in sorted(grouped.keys()):
            bucket = grouped[ch]
            random.shuffle(bucket)
            items.extend(bucket)

    # Persist the new order: the shuffled items take over the same set of
    # positions they held, so items outside the filter keep their places.
    # The scheduler re-plans Ready items' slots from this order on its next tick.
    slots = sorted(it.position if it.position is not None else it.id for it in items)
    for pos, it in zip(slots, items):
        it.position = pos

    s.commit()
    logbus.log("info", "queue_shuffled", f"Shuffled {len(items)} items using mode '{req.mode}'")
    return {"ok": True, "count": len(items), "mode": req.mode}


@app.put("/api/schedule/config", dependencies=_AUTH)
def update_schedule_config(req: ScheduleConfigIn, s: Session = Depends(get_session)) -> dict:
    """Change posting times from the Settings page; Ready items re-plan now."""
    try:
        queue_manager.save_schedule(s, None if req.reset else req.model_dump(exclude={"reset"}))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return get_schedule(s)


@app.post("/api/queue/bulk-ai", dependencies=_AUTH)
def start_bulk_ai(req: QueueBulkAction, s: Session = Depends(get_session)) -> dict:
    if not settings.gemini_api_key:
        raise HTTPException(400, "GEMINI_API_KEY is not set on the server")
    ids = [i for (i,) in s.query(QueueItem.id).filter(QueueItem.id.in_(req.ids)).all()]
    if not ids:
        raise HTTPException(400, "No matching queue items")
    try:
        return ai_bulk.start(ids)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/queue/bulk-ai", dependencies=_AUTH)
def bulk_ai_status() -> dict:
    return dict(ai_bulk.status)


@app.post("/api/social/ai-check", dependencies=_AUTH)
async def ai_check() -> dict:
    """Live Gemini call on a sample title. Changes nothing; reports the real error."""
    try:
        res = await social_metadata.generate_social_metadata(
            ["Superman's son gets too excited with his new powers"], strict=True)
    except social_metadata.MetadataError as exc:
        raise HTTPException(502, f"AI check failed: {exc}")
    return {"ok": True, "model": res.get("model"), "title": res.get("title"), "hashtags": res.get("hashtags")}


@app.get("/api/schedule", dependencies=_AUTH)
def get_schedule(s: Session = Depends(get_session)) -> dict:
    """Posting schedule config + what is lined up next, per pipeline."""
    now = datetime.datetime.now(datetime.timezone.utc)
    queue_manager.plan_schedule(s, now)  # so the answer matches what will post
    ready = (
        s.query(QueueItem)
        .filter(QueueItem.status == "ready")
        .order_by(QueueItem.scheduled_at, QueueItem.id)
        .all()
    )
    no_accounts = sum(1 for it in ready if not it.accounts)
    pipelines: dict[str, dict] = {}
    for it in ready:
        if not it.accounts:
            continue  # never scheduled until the owner picks accounts
        p = pipelines.setdefault(queue_manager.pipeline_group(it), {"ready": 0, "next": []})
        p["ready"] += 1
        if len(p["next"]) < 5:
            p["next"].append({"id": it.id, "title": it.title, "channel": it.pipeline,
                              "scheduled_at": queue_manager._aware(it.scheduled_at)})
    cfg = queue_manager.load_schedule(s)
    per_day = queue_manager.slots_per_day()
    return {
        "timezone": cfg["timezone"],
        "start_hour": cfg["start_hour"],
        "end_hour": cfg["end_hour"],
        "interval_hours": cfg["interval_hours"],
        "defaults": queue_manager.default_schedule(),
        "customized": cfg != queue_manager.default_schedule(),
        "slots_per_day": per_day,
        "next_slots": queue_manager.slots_after(now, per_day),
        "archive_delete_days": settings.archive_delete_days,
        "ready_without_accounts": no_accounts,
        "pipelines": pipelines,
        "ai": {"configured": bool(settings.gemini_api_key), "model": settings.gemini_model},
        "scheduler": {**queue_manager.scheduler_status, "tick_seconds": queue_manager.TICK_SECONDS,
                      "enabled": settings.worker_mode != "web_only"},
        "publisher": {"name": "Upload-Post", "configured": upload_post.configured(),
                      "privacy": settings.publish_privacy},
    }


@app.post("/api/social/ingest-channel", dependencies=_AUTH)
def ingest_channel(req: ChannelIngestRequest, s: Session = Depends(get_session)):
    """
    Bulk scrape videos from a YouTube channel/playlist or single video,
    automatically label them, upload directly to the channel subfolder in Google Drive,
    and add them to the SocialPilot posting queue. Zero local storage footprint.
    """
    from .drive_sync import ingest_channel_to_drive

    try:
        res = ingest_channel_to_drive(
            url=req.url,
            parent_folder_id=req.parent_folder_id,
            parent_folder_name=req.parent_folder_name,
            channel_name=req.channel_name,
            max_videos=req.max_videos,
            auto_approve=req.auto_approve,
            db_session=s,
        )
        return res
    except Exception as exc:
        logbus.log("error", "channel_ingest_failed", f"Channel ingest failed for {req.url}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/drive/sync", dependencies=_AUTH)
def sync_drive_folder(req: DriveSyncRequest, s: Session = Depends(get_session)):
    """
    Sync video files from a Google Drive folder into the channel's posting queue.
    Automatically handles channel subfolders (e.g. @VynixAE, @PixelDrift-f3c).
    """
    from .drive_sync import sync_drive_to_queue

    try:
        folder_target = req.folder_url or req.folder_id
        res = sync_drive_to_queue(
            folder_url_or_id=folder_target,
            default_pipeline=req.pipeline,
            auto_approve=req.auto_approve,
            db_session=s,
        )
        return res
    except Exception as exc:
        logbus.log("error", "drive_sync_failed", f"Drive sync failed for {req.folder_id}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
