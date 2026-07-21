"""Scrapper API — FastAPI app.

Sections:  ingest (paste links) · clips (storyboard table) · compile (Extract)
           · compilations/download (Export) · logs + stats + events (observability)
"""

from __future__ import annotations

import json
import pathlib
import queue
import time

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from ..core import engine
from . import logbus, rescan, worker
from .auth import require_token
from .config import settings
from .db import SessionLocal, get_session, init_db
from .models import Clip, Compilation, IngestJob, LogEntry, Status
from .schemas import (
    ClipOut,
    CompilationOut,
    CompileRequest,
    IngestRequest,
    LogOut,
    SelectRequest,
)

app = FastAPI(title="Scrapper API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_AUTH = [Depends(require_token)]


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
    for attr in ("file_path", "thumb_path"):
        p = getattr(clip, attr)
        if p and pathlib.Path(p).exists():
            pathlib.Path(p).unlink(missing_ok=True)
    # Also drop the sidecar .info.json so a full rescan won't re-adopt it,
    # and clear the video id from the dedup archive so it can be re-downloaded.
    if clip.file_path:
        pathlib.Path(clip.file_path).with_suffix(".info.json").unlink(missing_ok=True)
    if clip.video_id:
        _forget_in_archive(clip.video_id)
    s.delete(clip)
    s.commit()
    return {"deleted": clip_id}


def _forget_in_archive(video_id: str) -> None:
    """Remove a video id from archive.txt so yt-dlp will re-download it later."""
    arc = settings.archive_path
    if not arc.exists():
        return
    try:
        kept = [ln for ln in arc.read_text().splitlines() if video_id not in ln]
        arc.write_text("\n".join(kept) + ("\n" if kept else ""))
    except Exception as exc:  # non-fatal
        logbus.log("warning", "archive_cleanup_failed", str(exc), video_id=video_id)


@app.post("/api/rescan", dependencies=_AUTH)
def rescan_now(s: Session = Depends(get_session)) -> dict:
    """Rebuild clip + compilation rows from the video files on the volume."""
    return rescan.rescan_library(s)


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
    if comp.output_path and pathlib.Path(comp.output_path).exists():
        pathlib.Path(comp.output_path).unlink(missing_ok=True)
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
