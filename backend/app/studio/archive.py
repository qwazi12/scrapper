"""14-day archive for finished breakdowns (owner rule, 2026-10-03).

A breakdown is archived once ALL of these hold:
  - it posted at least `days` (default 14) days ago,
  - its Drive copy is saved AND is of the current render,
  - no step is running on it.
Archiving deletes what can be rebuilt (downloaded footage, stills, the render
folder, the segment cache) and keeps the script, plan, shots metadata,
thumbnails, voice-over and the Drive link. Footage the owner uploaded is never
deleted (it can't be re-downloaded). "Restore footage" re-downloads the same
IMDb videos and rebuilds the stills — no AI calls — so it can be edited and
re-rendered. Setting: app_settings "studio_archive" {"enabled", "days"}.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
import shutil
from typing import Any

from .. import control, logbus
from ..db import SessionLocal
from ..models import AppSetting, QueueItem, StudioProject
from . import media
from .runner import project_dir

logger = logging.getLogger("scrapper.studio.archive")
UTC = datetime.timezone.utc
DEFAULTS = {"enabled": True, "days": 14}
_last_sweep_day: str | None = None


def settings_dict(s=None) -> dict[str, Any]:
    own = s is None
    s = s or SessionLocal()
    try:
        row = s.get(AppSetting, "studio_archive")
        return {**DEFAULTS, **((row.value or {}) if row else {})}
    finally:
        if own:
            s.close()


def _dir_bytes(path: pathlib.Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def note_posted(s, item: QueueItem, p: StudioProject | None = None) -> None:
    """Remember on the project when its queue item posted (the 4-day sweep deletes
    the row). Only the project that currently points at this item, and only if
    the item really is its LongForm post (queue ids can be reused after deletes)."""
    if item.pipeline != "LongForm" or not item.published_at:
        return
    targets = [p] if p is not None else \
        s.query(StudioProject).filter(StudioProject.queue_item_id == item.id).all()
    for proj in targets:
        if proj.queue_item_id == item.id and not (proj.archive or {}).get("posted_at"):
            proj.archive = {**(proj.archive or {}), "posted_at": item.published_at.isoformat()}


def posted_at(s, p: StudioProject) -> datetime.datetime | None:
    a = p.archive or {}
    if not a.get("posted_at") and p.queue_item_id:
        item = s.get(QueueItem, p.queue_item_id)
        if item and item.status == "posted" and item.published_at:
            note_posted(s, item, p)
            a = p.archive or {}
    if not a.get("posted_at"):
        return None
    t = datetime.datetime.fromisoformat(a["posted_at"])
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def _deletable(p: StudioProject) -> list[pathlib.Path]:
    root = project_dir(p.id)
    uploaded = any(src.get("origin") == "upload" for src in (p.trailer or {}).get("sources", [])) or \
        (p.trailer or {}).get("origin") == "upload"
    parts = [root / "stills", root / "render", root / "render_new", root / "segcache"]
    if not uploaded:
        parts.append(root / "footage")
    return [x for x in parts if x.exists()]


def check(s, p: StudioProject, now: datetime.datetime, days: int) -> tuple[bool, str]:
    """(eligible, reason) — the reason is shown in the preview."""
    if (p.archive or {}).get("archived_at"):
        return False, "already archived"
    if p.stage_status == "running":
        return False, "a step is running"
    when = posted_at(s, p)
    if not when:
        return False, "not posted yet"
    age = (now - when).days
    if age < days:
        return False, f"posted {age} day(s) ago (archives at {days})"
    d = p.drive or {}
    if d.get("status") != "saved":
        return False, "no confirmed Drive copy"
    if not p.render or d.get("rendered_at") != p.render.get("rendered_at"):
        return False, "Drive copy is of an older render"
    return True, f"posted {age} days ago, Drive copy confirmed"


def preview(now: datetime.datetime | None = None) -> dict[str, Any]:
    now = now or datetime.datetime.now(UTC)
    with SessionLocal() as s:
        cfg = settings_dict(s)
        rows = []
        for p in s.query(StudioProject).order_by(StudioProject.id).all():
            ok, why = check(s, p, now, int(cfg["days"]))
            rows.append({"id": p.id, "title": p.title, "eligible": ok, "reason": why,
                         "frees_mb": round(sum(_dir_bytes(x) for x in _deletable(p)) / 1e6, 1) if ok else 0,
                         "archived_at": (p.archive or {}).get("archived_at")})
        s.commit()   # posted_at notes picked up on the way
    return {"settings": cfg, "projects": rows,
            "would_free_mb": round(sum(r["frees_mb"] for r in rows), 1)}


def archive_project(project_id: int, now: datetime.datetime | None = None) -> dict[str, Any]:
    now = now or datetime.datetime.now(UTC)
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        ok, why = check(s, p, now, int(settings_dict(s)["days"]))
        if not ok:
            return {"id": project_id, "archived": False, "reason": why}
        root = project_dir(p.id)
        thumb = root / (p.render or {}).get("thumbnail", "render/thumbnail.jpg")
        if thumb.exists():                                   # keep the thumbnail outside render/
            shutil.copy(thumb, root / "thumbnail.jpg")
        freed = 0
        for part in _deletable(p):
            freed += _dir_bytes(part)
            shutil.rmtree(part, ignore_errors=True)
        p.archive = {**(p.archive or {}), "archived_at": now.isoformat(), "freed_mb": round(freed / 1e6, 1),
                     "restored_at": None}
        s.commit()
    logbus.log("info", "studio_archived", f"Studio #{project_id} archived ({why}); freed {round(freed / 1e6)} MB — "
               "the video stays in Drive", project=project_id)
    return {"id": project_id, "archived": True, "freed_mb": round(freed / 1e6, 1)}


def sweep(now: datetime.datetime | None = None) -> list[dict]:
    with SessionLocal() as s:
        cfg = settings_dict(s)
        ids = [p.id for p in s.query(StudioProject).all()]
    if not cfg.get("enabled"):
        return []
    return [r for r in (archive_project(i, now) for i in ids) if r.get("archived")]


def free_footage(project_id: int) -> dict[str, Any] | None:
    """Disk saver (owner, 2026-10-04 — volume hit 97.6%): once a project's
    CURRENT render is safely in Drive, delete its downloaded trailer footage
    and segment cache (~300 MB). The render (the queue posts it), stills,
    script, plan and thumbnails stay. Rendering again re-downloads the footage
    from IMDb first (render → ensure_footage). Uploaded footage is never deleted.
    Returns what was freed, or None when the project doesn't qualify."""
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        if not p or p.stage_status in ("running", "queued"):
            return None
        r, d = p.render or {}, p.drive or {}
        if not r or d.get("status") != "saved" or d.get("rendered_at") != r.get("rendered_at"):
            return None
        sources = (p.trailer or {}).get("sources", [])
        if any(src.get("origin") != "imdb" for src in sources) or (p.trailer or {}).get("origin") == "upload":
            return None          # only footage IMDb can give back is deleted
        root = project_dir(project_id)
        parts = [x for x in (root / "footage", root / "segcache") if x.exists()]
        freed = sum(_dir_bytes(x) for x in parts)
        if not freed:
            return None
        for x in parts:
            shutil.rmtree(x, ignore_errors=True)
        info = {"footage_freed_at": datetime.datetime.now(UTC).isoformat(), "footage_freed_mb": round(freed / 1e6)}
        p.archive = {**(p.archive or {}), **info}
        s.commit()
        title = p.title
    logbus.log("info", "studio_footage_freed",
               f"Studio #{project_id} {title}: freed {info['footage_freed_mb']} MB of trailer footage "
               "(video is in Drive; footage re-downloads from IMDb if you render again)", project=project_id)
    return {"project_id": project_id, **info}


def free_footage_sweep() -> list[dict[str, Any]]:
    with SessionLocal() as s:
        ids = [pid for (pid,) in s.query(StudioProject.id).all()]
    return [r for r in (free_footage(pid) for pid in ids) if r]


def ensure_footage(project_id: int) -> bool:
    """Before a render: re-download freed footage from IMDb (no AI cost).
    Returns True if anything was downloaded."""
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        sources = (p.trailer or {}).get("sources", []) if p else []
    missing = [src for src in sources if src.get("file") and not pathlib.Path(src["file"]).exists()]
    if not missing:
        return False
    control.progress(f"re-downloading {len(missing)} trailer file(s) freed to save disk space")
    restore(project_id)
    return True


def maybe_sweep() -> None:
    """Called by the scheduler; runs once a day."""
    global _last_sweep_day
    today = datetime.datetime.now(UTC).strftime("%Y-%m-%d")
    if _last_sweep_day == today:
        return
    _last_sweep_day = today
    try:
        sweep()
    except Exception as exc:  # noqa: BLE001 — never break the scheduler
        logbus.log("error", "studio_archive_failed", str(exc))
    try:
        free_footage_sweep()        # catches any project whose footage wasn't freed after its Drive save
    except Exception as exc:  # noqa: BLE001
        logbus.log("error", "studio_footage_free_failed", str(exc))


def restore(project_id: int) -> str:
    """Re-download the IMDb footage and rebuild every shot's still (no AI calls)."""
    from . import imdb
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        sources = (p.trailer or {}).get("sources", [])
        shots = p.shots or []
    root = project_dir(project_id)
    (root / "footage").mkdir(exist_ok=True)
    (root / "stills").mkdir(exist_ok=True)
    for n, src in enumerate(sources, 1):
        dest = pathlib.Path(src["file"])
        if dest.exists():
            continue
        if src.get("origin") != "imdb":
            raise RuntimeError(f"{src.get('name') or dest.name} can't be re-downloaded (origin {src.get('origin')})")
        control.check()
        control.progress(f"re-downloading footage {n} of {len(sources)}")
        imdb.download(src["id"], dest)
    for n, sh in enumerate(shots, 1):
        still = root / sh["still"]
        if not still.exists() and pathlib.Path(sh.get("file", "")).exists():
            control.check()
            control.progress(f"rebuilding still {n} of {len(shots)}")
            media.frame(sh["file"], sh["start"] + (sh["end"] - sh["start"]) / 2, still, crop=sh.get("crop"))
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.archive = {**(p.archive or {}), "archived_at": None,
                     "restored_at": datetime.datetime.now(UTC).isoformat()}
        s.commit()
    logbus.log("info", "studio_restored", f"Studio #{project_id}: footage restored — re-render when ready",
               project=project_id)
    return "footage restored"


def start_restore(project_id: int, title: str, rid: int | None = None) -> str:
    from .. import resume
    return resume.tracked_thread("studio_restore", f"Restore footage: {title}", "studio",
                                 {"project_id": project_id, "title": title}, lambda: restore(project_id),
                                 rid=rid, ref=project_id)


from .. import resume as _resume_mod  # noqa: E402  (registered at import)
_resume_mod.launcher("studio_restore")(lambda p, cp, rid: start_restore(int(p["project_id"]), p.get("title", ""), rid))
