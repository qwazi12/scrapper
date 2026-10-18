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
from . import cleanup, control, worker_leases as leases
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
        # A no-op daily check is not news — keep it at debug so the log stays
        # readable. Only an actual version change (or a failure) is worth info.
        changed = "->" in msg
        level = "info" if changed else "debug"
        log(level if ok else "warning", "engine_update", msg)


def _sleep(seconds: float) -> None:
    """time.sleep that a Stop interrupts."""
    end = time.time() + seconds
    while time.time() < end:
        control.check()
        time.sleep(min(0.5, end - time.time()) if end > time.time() else 0)


def _owned(kind, model, ident, fn) -> bool:
    """Lease before reading queue state; a stale scanned candidate is rechecked."""
    try:
        with leases.scope(f"{kind}:{ident}"):
            with SessionLocal() as s:
                row=s.get(model,ident)
                if not row or row.status != Status.queued: return False
            fn(ident)
            return True
    except leases.LeaseLost:
        return False


def _process_ingest(job_id: int) -> bool:
    return _owned('ingest',IngestJob,job_id,_ingest_owned)


def _process_compile(comp_id: int) -> bool:
    return _owned('compile',Compilation,comp_id,_compile_owned)


def _attempt_root(base):
    import hashlib
    h=leases.current()
    if not h: raise leases.LeaseLost('Worker output requires a claimed resource')
    leases.guard()
    key=hashlib.sha256(h.resource.encode()).hexdigest()[:16]
    path=base / '_attempts' / f'{key}-{h.owner}-{h.generation}'
    path.mkdir(parents=True,exist_ok=True)
    return path


def _ingest_owned(job_id: int) -> None:
    with SessionLocal() as s:
        job = s.get(IngestJob, job_id)
        if not job:
            return
        job.status = Status.running
        job.total = len(job.urls)
        # Check which URLs in job.urls have already finished successfully
        done_urls = {
            c.source_url for c in s.query(Clip).filter(Clip.job_id == job_id, Clip.status == Status.done).all()
        }
        s.commit()
        urls = list(job.urls)

    try:
        with control.job("ingest", f"Scrape job #{job_id} ({len(urls)} link{'s' if len(urls) != 1 else ''})",
                         scope="scraper", ref=job_id):
            for n, url in enumerate(urls, 1):
                if url in done_urls:
                    continue
                control.check()
                control.progress(f"link {n} of {len(urls)}")
                _scrape_one(job_id, url)
                if n < len(urls):
                    _sleep(settings.per_clip_delay_seconds)  # Layer 3: self-rate-limit
    except leases.LeaseLost:
        raise
    except control.Cancelled:
        with SessionLocal() as s:
            job = s.get(IngestJob, job_id)
            job.status = Status.done
            job.finished_at = _now()
            s.commit()
        log("warning", "ingest_stopped", f"job {job_id}: stopped by user "
            f"({job.done_count} ok, {len(urls) - job.done_count - job.failed_count} not started)")
        return

    with SessionLocal() as s:
        job = s.get(IngestJob, job_id)
        job.status = Status.done
        job.finished_at = _now()
        s.commit()

        # Check if any clips in this job are waiting for the residential local worker
        clips = s.query(Clip).filter(Clip.job_id == job_id).all()
        queued_count = sum(1 for c in clips if c.status == Status.failed and c.file_path is None and "Mac worker" in (c.error or ""))
        other_failed = job.failed_count - queued_count

        parts = []
        if job.done_count > 0:
            parts.append(f"{job.done_count} ok")
        if queued_count > 0:
            parts.append(f"{queued_count} queued for Mac worker")
        if other_failed > 0:
            parts.append(f"{other_failed} failed")
        if not parts:
            parts.append("0 ok")
        log("info", "ingest_done", f"job {job_id}: {', '.join(parts)}")


def _scrape_call(url: str):
    """One yt-dlp run; Stop kills it (control.run)."""
    import shutil
    root=_attempt_root(settings.downloads_path)
    archive=root / 'archive.txt'
    if not archive.exists() and settings.archive_path.is_file():
        shutil.copy2(settings.archive_path,archive)  # snapshot; stale workers cannot edit shared history
    return scrape(
        url,
        downloads_dir=root,
        archive_path=archive,
        cookies_path=settings.cookies_path,
        proxy=settings.proxy_url or None,
        sleep_preset=settings.ytdlp_sleep_preset,
        cookies_from_browser=settings.cookies_from_browser or None,
        po_token=settings.ytdlp_po_token or None,
        runner=control.run,
    )


def _scrape_one(job_id: int, url: str) -> None:
    src = platform_of(url)
    with SessionLocal() as s:
        clip = Clip(job_id=job_id, source_url=url, platform=src, status=Status.running)
        s.add(clip)
        s.commit()
        clip_id = clip.id
    log("info", "scrape_start", url, source=src)

    try:
        cleanup.check_disk_space()
    except cleanup.DiskFullError as exc:
        with SessionLocal() as s:
            clip = s.get(Clip, clip_id)
            if clip:
                clip.status = Status.failed
                clip.error = str(exc)
            job = s.get(IngestJob, job_id)
            if job:
                job.failed_count += 1
            s.commit()
        log("error", "scrape_disk_full", str(exc), url=url)
        return

    result = None
    is_bot_blocked = False
    for attempt in range(1, settings.max_retries + 1):
        try:
            result = _scrape_call(url)
        except control.Cancelled:
            with SessionLocal() as s:
                clip = s.get(Clip, clip_id)
                # skipped, not failed: a failed clip with no file is what the
                # home Mac worker picks up, and a stopped link must stay stopped.
                clip.status = Status.skipped
                clip.error = "stopped by user"
                clip.title = clip.title or "stopped by user"
                s.commit()
            raise
        if result.ok:
            break
        if result.permanent:
            # Check if this failure is YouTube/platform blocking datacenter IP
            err_lower = (result.error or "").lower()
            if any(k in err_lower for k in ("blocked as a bot", "datacenter ip", "403", "bot check")):
                is_bot_blocked = True
            else:
                log("warning", "scrape_blocked", f"{url}: {result.error}", source=src)
            break
        wait = settings.retry_backoff_seconds * (2 ** (attempt - 1))
        log("warning", "scrape_retry", f"{url} attempt {attempt} failed: {result.error}",
            source=src, wait=wait)
        if attempt < settings.max_retries:
            _sleep(wait)

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
            if is_bot_blocked:
                clip.error = "Transferred to Mac worker (residential download)"
                log("info", "queued_for_local_worker",
                    "Railway blocked by YouTube — transferred to your Mac worker for residential download.",
                    source=src)
            else:
                log("error", "scrape_fail", f"{url}: {clip.error}", source=src)
        s.commit()


def _apply(clip: Clip, meta: dict) -> None:
    for k in ("source_url", "platform", "video_id", "uploader", "title",
              "duration", "width", "height", "file_path", "thumb_path", "size_bytes"):
        if meta.get(k) is not None:
            setattr(clip, k, meta[k])


def _compile_owned(comp_id: int) -> None:
    with SessionLocal() as s:
        comp = s.get(Compilation, comp_id)
        if not comp:
            return
        comp.status = Status.running
        s.commit()
        clip_ids, orientation = list(comp.clip_ids), comp.orientation
        clips = [s.get(Clip, cid) for cid in clip_ids]
        files = [c.file_path for c in clips if c and c.status==Status.done and c.file_path]

    if not files or len(files)!=len(clip_ids):
        _fail_compile(comp_id, "Every selected clip must be complete and available; compilation was not shortened")
        return

    try:
        cleanup.check_disk_space()
    except cleanup.DiskFullError as exc:
        _fail_compile(comp_id, str(exc))
        return

    stamp = _now().strftime("%Y-%m-%d_%H%M%S")
    suffix = "_landscape" if orientation == "landscape" else ""
    out = _attempt_root(settings.compilations_path) / f"compilation_{comp_id}_{stamp}{suffix}.mp4"
    log("info", "compile_start", f"{len(files)} clips -> {out.name}", orientation=orientation)

    def progress(p: float) -> None:
        with SessionLocal() as s:
            c = s.get(Compilation, comp_id)
            if c:
                c.progress = p
                s.commit()

    try:
        from .file_use import pin
        with pin(files), control.job("compile", f"Compilation #{comp_id} ({len(files)} clips)", scope="compile", ref=comp_id):
            compiler.compile_videos(
                files, out, orientation=orientation,
                encoder=settings.video_encoder, bitrate=settings.video_bitrate,
                fps=settings.encode_fps, on_progress=progress, on_start=control.track,
            )
            control.check()
            from .studio.media_quality import inspect_export
            quality=inspect_export(out,aspect=16/9 if orientation=='landscape' else 9/16)
            if quality['errors']: raise RuntimeError('Compilation quality blocked: '+'; '.join(quality['errors']))
    except leases.LeaseLost:
        raise
    except control.Cancelled:
        out.unlink(missing_ok=True)
        _fail_compile(comp_id, "stopped by user")
        return
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
    for model, fn in ((IngestJob,_process_ingest),(Compilation,_process_compile)):
        with SessionLocal() as s:
            ids=[r.id for r in s.query(model).filter(model.status==Status.queued).order_by(model.id).limit(20)]
        for ident in ids:
            if fn(ident): return True
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
    try:
        cleanup.cleanup_leftover_renders()
        cleanup.cleanup_temp_downloads()
        cleanup.prune_all_caches()
    except Exception as exc:
        log("warning", "retention_aux_cleanup_failed", str(exc))


def recover_interrupted_worker(s: Session) -> dict:
    """Recover scrape jobs, compilations, and clips interrupted by server restart or deploy."""
    import pathlib
    recovered_ingest = 0
    recovered_comp = 0
    recovered_clips = 0

    # Only a lease claimant can recover. Never infer completion from nonempty
    # partial files, delete a live worker's media, or promote an unverified output.
    for kind, model in (("ingest",IngestJob),("compile",Compilation)):
        ids=[r.id for r in s.query(model).filter(model.status==Status.running)]
        s.rollback()  # do not hold a scanned snapshot across resource acquisition
        for ident in ids:
            try:
                with leases.scope(f"{kind}:{ident}"):
                    row=s.get(model,ident,populate_existing=True)
                    if not row or row.status!=Status.running: continue
                    if kind=="ingest":
                        for clip in s.query(Clip).filter_by(job_id=ident,status=Status.running):
                            clip.status=Status.skipped;clip.error="Interrupted attempt; media was not promoted"
                            recovered_clips+=1
                        row.done_count=s.query(Clip).filter_by(job_id=ident,status=Status.done).count()
                        row.failed_count=s.query(Clip).filter_by(job_id=ident,status=Status.failed).count()
                        row.status=Status.queued;recovered_ingest+=1
                    else:
                        row.status=Status.queued;row.progress=0;recovered_comp+=1
                    s.commit()
            except leases.LeaseLost:
                s.rollback()
    s.commit()
    return {"ingest": recovered_ingest, "compilations": recovered_comp, "clips": recovered_clips}


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
