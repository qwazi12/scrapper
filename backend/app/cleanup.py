"""Deletion helpers and the retention sweep.

One place owns "what it means to delete a clip/compilation" so the API, the
retention sweep, and the rescan reconcile can never drift apart: remove the
media, its sidecars, and its entry in the yt-dlp dedup archive (so the same
video can be scraped again later).

Retention is a per-item rolling timer measured from each row's own created_at,
so something scraped on Thursday expires the following Tuesday — not on a
shared weekly purge date.
"""

from __future__ import annotations

import datetime
import pathlib

from sqlalchemy.orm import Session

from .config import settings
from .logbus import log
from .models import Clip, Compilation, QueueItem, Status


def _aware(dt: datetime.datetime) -> datetime.datetime:
    """SQLite drops tzinfo — treat naive timestamps as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


def forget_in_archive(video_id: str) -> None:
    """Drop a video id from archive.txt so yt-dlp will re-download it later."""
    arc = settings.archive_path
    if not arc.exists() or not video_id:
        return
    try:
        kept = [ln for ln in arc.read_text().splitlines() if video_id not in ln]
        arc.write_text("\n".join(kept) + ("\n" if kept else ""))
    except Exception as exc:  # non-fatal
        log("warning", "archive_cleanup_failed", str(exc), video_id=video_id)


def remove_clip_files(clip: Clip) -> None:
    for attr in ("file_path", "thumb_path"):
        p = getattr(clip, attr)
        if p:
            pathlib.Path(p).unlink(missing_ok=True)
    if clip.file_path:
        pathlib.Path(clip.file_path).with_suffix(".info.json").unlink(missing_ok=True)
    if clip.video_id:
        forget_in_archive(clip.video_id)


def remove_compilation_files(comp: Compilation) -> None:
    if comp.output_path:
        pathlib.Path(comp.output_path).unlink(missing_ok=True)


def expires_at(created_at: datetime.datetime) -> datetime.datetime | None:
    if settings.retention_days <= 0:
        return None
    return _aware(created_at) + datetime.timedelta(days=settings.retention_days)


def purge_expired(s: Session) -> dict:
    """Delete clips/compilations past their own retention window.
    Items in SocialPilot (QueueItem) are strictly EXEMPT from retention deletion.
    """
    days = settings.retention_days
    if days <= 0:
        return {"clips": 0, "compilations": 0}

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    n_clips = n_comps = 0

    # SocialPilot protected sets: clips/compilations scheduled or in queue are exempt
    queued_clips = {
        row[0]
        for row in s.query(QueueItem.clip_id)
        .filter(QueueItem.clip_id.isnot(None), QueueItem.status.in_(("review", "ready", "posting", "posted", "retry")))
        .all()
    }
    queued_comps = {
        row[0]
        for row in s.query(QueueItem.compilation_id)
        .filter(QueueItem.compilation_id.isnot(None), QueueItem.status.in_(("review", "ready", "posting", "posted", "retry")))
        .all()
    }

    for clip in s.query(Clip).all():
        if clip.status in (Status.queued, Status.running):
            continue  # never yank something mid-flight
        if clip.id in queued_clips:
            continue  # SocialPilot scheduler/queue is exempt from retention
        if _aware(clip.created_at) < cutoff:
            remove_clip_files(clip)
            s.delete(clip)
            n_clips += 1
            log("info", "retention_delete",
                f"clip expired after {days}d: {clip.title or clip.source_url}")

    for comp in s.query(Compilation).all():
        if comp.status in (Status.queued, Status.running):
            continue
        if comp.id in queued_comps:
            continue  # SocialPilot scheduler/queue is exempt from retention
        if _aware(comp.created_at) < cutoff:
            remove_compilation_files(comp)
            s.delete(comp)
            n_comps += 1
            log("info", "retention_delete", f"compilation #{comp.id} expired after {days}d")

    if n_clips or n_comps:
        s.commit()
        log("info", "retention_sweep", f"auto-deleted {n_clips} clip(s), {n_comps} compilation(s)")
    return {"clips": n_clips, "compilations": n_comps}


# --- Disk Space & Cache Management -------------------------------------------
class DiskFullError(Exception):
    """Raised when disk volume usage exceeds settings.max_disk_usage_percent."""
    pass


def get_disk_usage(target_path: pathlib.Path | None = None) -> dict:
    """Return volume storage statistics and health status."""
    import shutil
    target = target_path or settings.data_path
    try:
        total, used, free = shutil.disk_usage(target)
    except Exception:
        total, used, free = (0, 0, 0)
    pct = round((used / total) * 100, 1) if total > 0 else 0.0
    status = "healthy"
    if pct >= settings.max_disk_usage_percent:
        status = "critical"
    elif pct >= settings.warn_disk_usage_percent:
        status = "warning"
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "total_gb": round(total / (1024**3), 2),
        "used_gb": round(used / (1024**3), 2),
        "free_gb": round(free / (1024**3), 2),
        "percent_used": pct,
        "status": status,
        "max_threshold_percent": settings.max_disk_usage_percent,
        "warn_threshold_percent": settings.warn_disk_usage_percent,
        "path": str(target),
    }


def check_disk_space() -> None:
    """Refuse heavy operations if disk space is critical."""
    usage = get_disk_usage()
    if usage["percent_used"] >= settings.max_disk_usage_percent:
        msg = (
            f"Disk space critical: {usage['percent_used']}% used ({usage['used_gb']} GB / {usage['total_gb']} GB). "
            f"Operation blocked until volume usage drops below {settings.max_disk_usage_percent}%."
        )
        log("error", "disk_space_critical", msg)
        raise DiskFullError(msg)


def cleanup_render_intermediates(out_dir: pathlib.Path) -> dict:
    """Remove heavy intermediate files (ProRes mov overlays, raw audio, temp frame lists)
    after video compositing completes to avoid bloating disk space.
    """
    if not out_dir.exists():
        return {"files_removed": 0, "bytes_freed": 0}
    patterns = [
        "subscribe_*.mov",
        "banner_*.mov",
        "subscribe*.png",
        "banner*.png",
        "segments*.txt",
        "narration.m4a",
        "poster_clean.jpg",
        "end_card.jpg",
        "video_motion.mp4",
    ]
    removed = 0
    freed_bytes = 0
    for pat in patterns:
        for f in out_dir.glob(pat):
            try:
                sz = f.stat().st_size
                f.unlink(missing_ok=True)
                removed += 1
                freed_bytes += sz
            except Exception as exc:
                log("warning", "render_intermediate_cleanup_failed", str(exc), file=str(f))
    # Segments subfolder if still around
    segs = out_dir / "segments"
    if segs.exists():
        import shutil
        try:
            for child in segs.glob("*"):
                freed_bytes += child.stat().st_size if child.is_file() else 0
            shutil.rmtree(segs, ignore_errors=True)
        except Exception:
            pass
    if removed:
        log("info", "render_intermediates_cleaned", f"freed {round(freed_bytes / 1e6, 1)} MB across {removed} files", path=str(out_dir))
    return {"files_removed": removed, "bytes_freed": freed_bytes}


def prune_lru_cache(cache_dir: pathlib.Path, max_bytes: int, target_ratio: float = 0.8) -> dict:
    """Evict oldest entries in cache_dir until size drops below target_ratio * max_bytes."""
    if not cache_dir.exists() or not cache_dir.is_dir():
        return {"files_removed": 0, "bytes_freed": 0, "remaining_bytes": 0}
    entries: list[tuple[pathlib.Path, int, float]] = []
    total_bytes = 0
    for p in cache_dir.iterdir():
        if p.is_file():
            try:
                st = p.stat()
                entries.append((p, st.st_size, st.st_mtime))
                total_bytes += st.st_size
            except OSError:
                continue

    if total_bytes <= max_bytes:
        return {"files_removed": 0, "bytes_freed": 0, "remaining_bytes": total_bytes}

    # Sort oldest first
    entries.sort(key=lambda x: x[2])
    target_bytes = int(max_bytes * target_ratio)
    freed = 0
    removed = 0
    for path, sz, _ in entries:
        try:
            path.unlink(missing_ok=True)
            freed += sz
            total_bytes -= sz
            removed += 1
        except OSError as exc:
            log("warning", "cache_eviction_failed", str(exc), path=str(path))
        if total_bytes <= target_bytes:
            break

    log("info", "cache_pruned", f"evicted {removed} files, freed {round(freed / 1e6, 1)} MB from {cache_dir.name}")
    return {"files_removed": removed, "bytes_freed": freed, "remaining_bytes": total_bytes}


def prune_all_caches() -> dict:
    """Enforce LRU cache caps across studio motion graphics, TTS, and temporary audio."""
    res = {}
    motion_dir = settings.data_path / "studio" / "_motioncache"
    res["motion"] = prune_lru_cache(motion_dir, settings.max_motion_cache_mb * 1024 * 1024)

    tts_dir = settings.data_path / "studio" / "_ttscache"
    res["tts"] = prune_lru_cache(tts_dir, settings.max_tts_cache_mb * 1024 * 1024)

    audio_dir = settings.data_path / "audio"
    if audio_dir.exists():
        res["audio"] = prune_lru_cache(audio_dir, settings.max_tts_cache_mb * 1024 * 1024)
    return res


def cleanup_leftover_renders(max_age_seconds: int = 3600) -> dict:
    """Delete abandoned render_new/ scratch directories older than max_age_seconds."""
    import shutil
    import time
    projects_dir = settings.data_path / "studio"   # projects live in studio/<id>/ (runner.project_dir)
    if not projects_dir.exists():
        return {"folders_removed": 0}
    now = time.time()
    removed = 0
    for p in projects_dir.iterdir():
        if p.is_dir() and p.name.isdigit():
            rn = p / "render_new"
            if rn.exists():
                try:
                    if now - rn.stat().st_mtime > max_age_seconds:
                        shutil.rmtree(rn, ignore_errors=True)
                        removed += 1
                        log("info", "abandoned_render_cleaned", f"removed leftover {rn}")
                except Exception as exc:
                    log("warning", "abandoned_render_cleanup_failed", str(exc), path=str(rn))
    return {"folders_removed": removed}


def cleanup_temp_downloads(max_age_seconds: int = 7200) -> dict:
    """Clean up orphaned download chunks (.part, .ytdl, .tmp) older than 2 hours."""
    import time
    dl_dir = settings.data_path / "downloads"
    if not dl_dir.exists():
        return {"files_removed": 0, "bytes_freed": 0}
    now = time.time()
    removed = 0
    freed = 0
    for pat in ("*.part", "*.ytdl", "*.tmp"):
        for f in dl_dir.glob(pat):
            try:
                if now - f.stat().st_mtime > max_age_seconds:
                    sz = f.stat().st_size
                    f.unlink(missing_ok=True)
                    removed += 1
                    freed += sz
            except OSError:
                continue
    if removed:
        log("info", "temp_downloads_cleaned", f"removed {removed} orphaned partial downloads ({round(freed / 1e6, 1)} MB)")
    return {"files_removed": removed, "bytes_freed": freed}


def startup_cleanup() -> dict:
    """Safe cleanup executed on server startup."""
    leftovers = cleanup_leftover_renders()
    temp_dl = cleanup_temp_downloads()
    caches = prune_all_caches()
    return {"leftover_renders": leftovers, "temp_downloads": temp_dl, "caches": caches}


def run_full_sweep(s: Session | None = None) -> dict:
    """Execute complete cleanup sweep: retention, leftover renders, partial downloads, and cache pruning."""
    res = {}
    if s:
        res["retention"] = purge_expired(s)
    res["leftover_renders"] = cleanup_leftover_renders()
    res["temp_downloads"] = cleanup_temp_downloads()
    res["caches"] = prune_all_caches()
    res["disk_usage"] = get_disk_usage()
    return res
