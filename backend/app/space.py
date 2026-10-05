"""Disk guard: when the Railway volume reaches disk_free_at_percent (80) % full, back up first,
then free space that can be rebuilt, oldest first, until it's under FREE_TO %.

Owner rule (2026-10-05, after Railway's "volume is 80% full" email). Order:
  1. Back up the database (local + Drive copy, as the nightly backup does).
  2. Every rendered breakdown whose CURRENT render isn't in Drive yet is
     saved to Drive — nothing is freed before its video is backed up.
  3. Free, stopping as soon as usage is under FREE_TO %:
     a. trailer footage + segment cache of Drive-saved breakdowns (re-downloads
        from IMDb if re-rendered; the render the queue posts is never touched)
     b. the motion-graphics cache (rebuilt on the next render)
     c. old local database backups beyond the newest KEEP_BACKUPS
     d. Scraper clips/compilations past their retention window
Runs as a stoppable job, at most once per COOLDOWN, one log line with the result.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any

from . import control, logbus
from .config import settings
from .db import SessionLocal

logger = logging.getLogger("scrapper.space")
FREE_TO = 70.0          # trigger: settings.disk_free_at_percent (80)
COOLDOWN = datetime.timedelta(minutes=60)
KEEP_BACKUPS = 3

_lock = threading.Lock()
state: dict[str, Any] = {"last_run": None, "last_result": None}


def usage_pct() -> float:
    from .cleanup import get_disk_usage
    return float(get_disk_usage()["percent_used"])


def _ok() -> bool:
    return usage_pct() < FREE_TO


def run() -> dict[str, Any]:
    """One full pass (backup first, then free). Returns what it did."""
    from . import backup, cleanup
    from .models import StudioProject
    from .studio import archive, drive_store

    before = usage_pct()
    did: dict[str, Any] = {"before_pct": before}

    control.progress("backing up the database first")
    try:
        b = backup.create_backup(tag="pre_cleanup")
        did["backup"] = b.get("filename") or b.get("error")
    except Exception as exc:  # noqa: BLE001 — no backup, no deleting
        did["backup_error"] = str(exc)[:200]
        logbus.log("error", "space_backup_failed", f"Disk at {before:.0f}%: backup failed, nothing freed — {exc}")
        return did

    # Make sure every current render is in Drive before anything is freed.
    with SessionLocal() as s:
        todo = [(p.id, p.title) for p in s.query(StudioProject).all()
                if p.render and p.stage_status not in ("running", "queued")
                and ((p.drive or {}).get("status") != "saved"
                     or (p.drive or {}).get("rendered_at") != p.render.get("rendered_at"))]
    saved = []
    for pid, title in todo:
        control.check()
        control.progress(f"saving {title} to Drive before freeing space")
        try:
            drive_store.save(pid)          # frees that project's footage itself on success
            saved.append(pid)
        except Exception as exc:  # noqa: BLE001 — not backed up → its files stay
            logger.warning("space: Drive save of #%s failed: %s", pid, exc)
    did["drive_saved"] = saved

    steps = [
        ("footage", lambda: archive.free_footage_sweep()),
        ("motion_cache", lambda: cleanup.prune_lru_cache(settings.data_path / "studio" / "_motioncache", 0)),
        ("old_backups", lambda: backup.prune_local_backups(keep=KEEP_BACKUPS)),
        ("expired_clips", lambda: _purge_expired()),
    ]
    for name, fn in steps:
        if _ok():
            break
        control.check()
        control.progress(f"freeing space: {name.replace('_', ' ')}")
        try:
            did[name] = fn()
        except Exception as exc:  # noqa: BLE001 — try the next kind
            did[name] = f"failed: {exc}"[:200]
    did["after_pct"] = usage_pct()
    return did


def _purge_expired() -> dict:
    from .cleanup import purge_expired
    with SessionLocal() as s:
        return purge_expired(s)


def maybe_free(now: datetime.datetime | None = None) -> bool:
    """Scheduler tick: start a pass when the volume is ≥ disk_free_at_percent (cooldown)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    last = state["last_run"]
    if last and now - last < COOLDOWN:
        return False
    pct = usage_pct()
    if pct < settings.disk_free_at_percent or not _lock.acquire(blocking=False):
        return False
    state["last_run"] = now

    def work():
        try:
            did = run()
            state["last_result"] = did
            freed = round(did.get("before_pct", 0) - did.get("after_pct", did.get("before_pct", 0)), 1)
            logbus.log("warning", "space_freed",
                       f"Disk was {did.get('before_pct', 0):.0f}% full: backed up "
                       f"({did.get('backup') or did.get('backup_error')}), saved {len(did.get('drive_saved', []))} "
                       f"breakdown(s) to Drive, freed {freed} points → {did.get('after_pct', 0):.0f}%", **{
                           k: str(v)[:200] for k, v in did.items()})
        finally:
            _lock.release()

    control.start_thread("space", f"Disk {pct:.0f}% full — back up, then free space", "system", work)
    return True
