"""Background worker: drains ingest + compile jobs from the DB.

Runs in-process by default (worker_mode="web"). Can also run standalone via
`python -m app.worker` on a machine with a residential IP (Reliability Layer 5,
the hybrid worker) — it just needs the same DATABASE_URL and DATA_DIR.

Reliability Layer 3 lives here: retries with exponential backoff and a small
self-imposed delay between clips so we don't trip rate limits.
"""

from __future__ import annotations

import datetime
import threading
import time

from ..core import compiler, engine
from ..core.scraper import platform_of, scrape
from . import cleanup
from .config import settings
from .db import SessionLocal, init_db
from .logbus import log, record_outcome
from .models import Clip, Compilation, IngestJob, Status

_stop = threading.Event()
_last_update_day: int | None = None


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


# --- Layer 1: nightly self-update -------------------------------------------
def _maybe_update_engine() -> None:
    global _last_update_day
    if not settings.auto_update_ytdlp:
        return
    now = datetime.datetime.now()
    if now.hour == settings.ytdlp_update_hour and _last_update_day != now.day:
        _last_update_day = now.day
        ok, msg = engine.update_ytdlp()
        log("info" if ok else "warning", "engine_update", msg)


def _process_ingest(job_id: int) -> None:
    with SessionLocal() as s:
        job = s.get(IngestJob, job_id)
        if not job:
            return
        job.status = Status.running
        job.total = len(job.urls)
        s.commit()
        urls = list(job.urls)

    for url in urls:
        _scrape_one(job_id, url)
        time.sleep(settings.per_clip_delay_seconds)  # Layer 3: self-rate-limit

    with SessionLocal() as s:
        job = s.get(IngestJob, job_id)
        job.status = Status.done
        job.finished_at = _now()
        s.commit()
        log("info", "ingest_done", f"job {job_id}: {job.done_count} ok, {job.failed_count} failed")


def _scrape_one(job_id: int, url: str) -> None:
    src = platform_of(url)
    with SessionLocal() as s:
        clip = Clip(job_id=job_id, source_url=url, platform=src, status=Status.running)
        s.add(clip)
        s.commit()
        clip_id = clip.id
    log("info", "scrape_start", url, source=src)

    result = None
    for attempt in range(1, settings.max_retries + 1):
        result = scrape(
            url,
            downloads_dir=settings.downloads_path,
            archive_path=settings.archive_path,
            cookies_path=settings.cookies_path,
            proxy=settings.proxy_url or None,
            sleep_preset=settings.ytdlp_sleep_preset,
            cookies_from_browser=settings.cookies_from_browser or None,
        )
        if result.ok:
            break
        wait = settings.retry_backoff_seconds * (2 ** (attempt - 1))
        log("warning", "scrape_retry", f"{url} attempt {attempt} failed: {result.error}",
            source=src, wait=wait)
        if attempt < settings.max_retries:
            time.sleep(wait)

    with SessionLocal() as s:
        job = s.get(IngestJob, job_id)
        clip = s.get(Clip, clip_id)
        if result and result.ok and result.clips:
            # First clip fills the placeholder row; extras become their own rows.
            first, *rest = result.clips
            _apply(clip, first)
            clip.status = Status.done
            for extra in rest:
                c = Clip(job_id=job_id, status=Status.done)
                _apply(c, extra)
                s.add(c)
            job.done_count += 1
            record_outcome(src, True)
            log("info", "scrape_done", clip.title or url, source=src)
        elif result and result.already:
            clip.status = Status.skipped
            clip.title = clip.title or "already downloaded"
            job.done_count += 1
            record_outcome(src, True)
            log("info", "scrape_skip", f"{url} already in archive", source=src)
        else:
            clip.status = Status.failed
            clip.error = (result.error if result else "unknown")[:500]
            job.failed_count += 1
            record_outcome(src, False)
            log("error", "scrape_fail", f"{url}: {clip.error}", source=src)
        s.commit()


def _apply(clip: Clip, meta: dict) -> None:
    for k in ("source_url", "platform", "video_id", "uploader", "title",
              "duration", "width", "height", "file_path", "thumb_path", "size_bytes"):
        if meta.get(k) is not None:
            setattr(clip, k, meta[k])


def _process_compile(comp_id: int) -> None:
    with SessionLocal() as s:
        comp = s.get(Compilation, comp_id)
        if not comp:
            return
        comp.status = Status.running
        s.commit()
        clip_ids, orientation = list(comp.clip_ids), comp.orientation
        clips = [s.get(Clip, cid) for cid in clip_ids]
        files = [c.file_path for c in clips if c and c.file_path]

    if len(files) < 1:
        _fail_compile(comp_id, "no valid clips selected")
        return

    stamp = _now().strftime("%Y-%m-%d_%H%M%S")
    suffix = "_landscape" if orientation == "landscape" else ""
    out = settings.compilations_path / f"compilation_{stamp}{suffix}.mp4"
    log("info", "compile_start", f"{len(files)} clips -> {out.name}", orientation=orientation)

    def progress(p: float) -> None:
        with SessionLocal() as s:
            c = s.get(Compilation, comp_id)
            if c:
                c.progress = p
                s.commit()

    try:
        compiler.compile_videos(
            files, out, orientation=orientation,
            encoder=settings.video_encoder, bitrate=settings.video_bitrate,
            fps=settings.encode_fps, on_progress=progress,
        )
    except Exception as exc:  # noqa: BLE001
        _fail_compile(comp_id, str(exc))
        return

    with SessionLocal() as s:
        comp = s.get(Compilation, comp_id)
        comp.status = Status.done
        comp.progress = 1.0
        comp.output_path = str(out)
        comp.size_bytes = out.stat().st_size if out.exists() else None
        comp.duration = compiler._probe_duration(out)
        comp.finished_at = _now()
        s.commit()
    log("info", "compile_done", f"{out.name} ({(out.stat().st_size/1e6):.0f} MB)")


def _fail_compile(comp_id: int, msg: str) -> None:
    with SessionLocal() as s:
        comp = s.get(Compilation, comp_id)
        if comp:
            comp.status = Status.failed
            comp.error = msg[:500]
            comp.finished_at = _now()
            s.commit()
    log("error", "compile_fail", msg)


def _drain_once() -> bool:
    """Process one queued job (ingest first, then compile). Returns True if it did work."""
    with SessionLocal() as s:
        job = s.query(IngestJob).filter(IngestJob.status == Status.queued).order_by(IngestJob.id).first()
        job_id = job.id if job else None
    if job_id is not None:
        _process_ingest(job_id)
        return True

    with SessionLocal() as s:
        comp = s.query(Compilation).filter(Compilation.status == Status.queued).order_by(Compilation.id).first()
        comp_id = comp.id if comp else None
    if comp_id is not None:
        _process_compile(comp_id)
        return True
    return False


_last_purge = 0.0


def _maybe_purge_expired() -> None:
    """Retention sweep on its own cadence, independent of job traffic."""
    global _last_purge
    if settings.retention_days <= 0:
        return
    now = time.time()
    if now - _last_purge < settings.retention_sweep_minutes * 60:
        return
    _last_purge = now
    with SessionLocal() as s:
        cleanup.purge_expired(s)


def run_loop() -> None:
    log("info", "worker_start", f"worker loop up (mode={settings.worker_mode})")
    while not _stop.is_set():
        try:
            _maybe_update_engine()
            _maybe_purge_expired()
            did = _drain_once()
        except Exception as exc:  # keep the loop alive no matter what
            log("error", "worker_error", str(exc))
            did = False
        if not did:
            time.sleep(settings.worker_poll_seconds)


def start_background() -> threading.Thread:
    t = threading.Thread(target=run_loop, name="scrapper-worker", daemon=True)
    t.start()
    return t


def stop() -> None:
    _stop.set()


if __name__ == "__main__":
    # Standalone worker (Layer 5 hybrid home-IP box).
    init_db()
    if settings.auto_update_ytdlp:
        ok, msg = engine.update_ytdlp()
        log("info" if ok else "warning", "engine_update", msg)
    run_loop()
