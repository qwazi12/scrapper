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
from .models import Clip, Compilation, Status


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
    """Delete clips/compilations past their own retention window."""
    days = settings.retention_days
    if days <= 0:
        return {"clips": 0, "compilations": 0}

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=days)
    n_clips = n_comps = 0

    for clip in s.query(Clip).all():
        if clip.status in (Status.queued, Status.running):
            continue  # never yank something mid-flight
        if _aware(clip.created_at) < cutoff:
            remove_clip_files(clip)
            s.delete(clip)
            n_clips += 1
            log("info", "retention_delete",
                f"clip expired after {days}d: {clip.title or clip.source_url}")

    for comp in s.query(Compilation).all():
        if comp.status in (Status.queued, Status.running):
            continue
        if _aware(comp.created_at) < cutoff:
            remove_compilation_files(comp)
            s.delete(comp)
            n_comps += 1
            log("info", "retention_delete", f"compilation #{comp.id} expired after {days}d")

    if n_clips or n_comps:
        s.commit()
        log("info", "retention_sweep", f"auto-deleted {n_clips} clip(s), {n_comps} compilation(s)")
    return {"clips": n_clips, "compilations": n_comps}
