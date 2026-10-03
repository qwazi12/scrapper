"""Studio automation: make trailer breakdowns from the ranked candidate list.

Switch + limits live in app_settings "studio_auto":
  {enabled: false, movies_per_day: 3, tv_per_day: 2}
The limits count EVERY breakdown started that day (automation and manual
"Make breakdown" clicks alike); a manual click past the limit asks to confirm.
"Day" = the posting schedule's timezone (same as the spend cap).

Each scheduler tick (5 min), when switched on, it starts at most one:
  1. jobs the daily spend cap paused go first — nothing new while any wait;
  2. Studio must be idle (one Studio job at a time);
  3. today's spend + an estimate of one breakdown must fit under the cap;
  4. take the best candidate (pins first) of a type with quota left that
     passes every check with an IMDb trailer;
  5. create the project and run steps 1–5 (gather → plan), stopping before
     render: the owner reviews, then renders (one by one or in a batch).
A breakdown that fails stays as a project (so it isn't picked again) with
its error; the automation moves on to the next title.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from .. import costs, logbus
from ..db import SessionLocal
from ..models import AppSetting, StudioProject

logger = logging.getLogger("scrapper.studio.auto")
UTC = datetime.timezone.utc
DEFAULTS = {"enabled": False, "movies_per_day": 3, "tv_per_day": 2}
DEFAULT_ESTIMATE = 0.60   # $ per breakdown until there's history
MAX_PER_TYPE = 20

state: dict[str, Any] = {"last_check_at": None, "last_result": None, "last_started": None}


def settings_value(s=None) -> dict[str, Any]:
    own = s is None
    s = s or SessionLocal()
    try:
        row = s.get(AppSetting, "studio_auto")
        return {**DEFAULTS, **((row.value or {}) if row else {})}
    finally:
        if own:
            s.close()


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if "enabled" in patch and patch["enabled"] is not None:
        clean["enabled"] = bool(patch["enabled"])
    for k in ("movies_per_day", "tv_per_day"):
        if patch.get(k) is not None:
            v = int(patch[k])
            if not 0 <= v <= MAX_PER_TYPE:
                raise ValueError(f"{k} must be 0–{MAX_PER_TYPE}")
            clean[k] = v
    with SessionLocal() as s:
        row = s.get(AppSetting, "studio_auto")
        before = settings_value(s)
        value = {**before, **clean}
        if row:
            row.value = value
        else:
            s.add(AppSetting(key="studio_auto", value=value))
        s.commit()
    logbus.log("info", "studio_auto_settings",
               f"Breakdown automation {'ON' if value['enabled'] else 'OFF'} — "
               f"{value['movies_per_day']} movies + {value['tv_per_day']} TV per day",
               before=before, after=value)
    return value


def limit_for(media_type: str, cfg: dict[str, Any]) -> int:
    return int(cfg["movies_per_day"] if media_type == "movie" else cfg["tv_per_day"])


def made_today(s, now: datetime.datetime | None = None) -> dict[str, int]:
    start, end = costs.day_bounds(now)
    out = {"movie": 0, "tv": 0}
    for (mt,) in s.query(StudioProject.media_type).filter(StudioProject.created_at >= start,
                                                          StudioProject.created_at < end).all():
        out[mt] = out.get(mt, 0) + 1
    return out


def over_limit(s, media_type: str) -> str | None:
    """Reason a new breakdown of this type would go past today's limit, or None."""
    cfg = settings_value(s)
    n, lim = made_today(s).get(media_type, 0), limit_for(media_type, cfg)
    if n >= lim:
        kind = "movie" if media_type == "movie" else "TV"
        return f"Today's limit is {lim} {kind} breakdown(s) and {n} were already started."
    return None


def estimate_cost(s) -> float:
    """Average tracked cost of the last 5 breakdowns that reached the script."""
    rows = s.query(StudioProject.id).filter(StudioProject.script.isnot(None)) \
        .order_by(StudioProject.id.desc()).limit(5).all()
    vals = [costs.ref_cost(f"studio:{pid}") for (pid,) in rows]
    vals = [v for v in vals if v > 0]
    return round(sum(vals) / len(vals), 4) if vals else DEFAULT_ESTIMATE


def _wait_reason(s, cfg: dict[str, Any]) -> tuple[str | None, list[str]]:
    """Why nothing can start now (or None), and which types still have quota."""
    from .. import resume
    from . import runner
    if resume.budget_paused(s):
        return "jobs paused by the daily cap resume first", []
    if runner.busy().get("project_id"):
        return f"Studio is busy with #{runner.busy()['project_id']}", []
    today = made_today(s)
    open_types = [mt for mt in ("movie", "tv") if today.get(mt, 0) < limit_for(mt, cfg)]
    if not open_types:
        return "today's limits reached", []
    day = costs.daily_status(s)
    if day["cap_usd"]:
        est = estimate_cost(s)
        if day["spent_usd"] + est > day["cap_usd"]:
            return (f"not enough budget left today for a full breakdown "
                    f"(${day['spent_usd']:.2f} spent, ~${est:.2f} each, cap ${day['cap_usd']:.2f})"), []
    return None, open_types


def tick() -> dict[str, Any] | None:
    """One scheduler pass. Starts at most one breakdown; returns what it did."""
    from . import candidates, runner
    now = datetime.datetime.now(UTC)
    state["last_check_at"] = now.isoformat()
    with SessionLocal() as s:
        cfg = settings_value(s)
    if not cfg["enabled"]:
        state["last_result"] = "off"
        return None
    if candidates.is_stale(now):
        candidates.refresh_async()
    with SessionLocal() as s:
        reason, open_types = _wait_reason(s, cfg)
    if reason:
        state["last_result"] = f"waiting: {reason}"
        return None
    pick = candidates.next_pick(open_types)
    if not pick:
        state["last_result"] = "waiting: no candidate passes every check (refresh the list or pin one)"
        return None
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=pick["tmdb_id"], media_type=pick["media_type"], title=pick["title"],
                          review={"auto": True, "reviewed_at": None, "score": pick["score"]["total"]})
        s.add(p)
        s.commit()
        pid = p.id
    try:
        runner.start(pid, "gather", auto=True, until="plan")
    except RuntimeError as exc:   # Studio got busy in between: try again next tick
        with SessionLocal() as s:
            s.delete(s.get(StudioProject, pid))
            s.commit()
        state["last_result"] = f"waiting: {exc}"
        return None
    started = {"project_id": pid, "title": pick["title"], "media_type": pick["media_type"],
               "score": pick["score"]["total"], "at": now.isoformat()}
    state["last_started"] = started
    state["last_result"] = f"started #{pid} {pick['title']}"
    logbus.log("info", "studio_auto_started",
               f"Automation started breakdown #{pid}: {pick['title']} ({pick['media_type']}, score {pick['score']['total']})",
               project=pid)
    return started


def status() -> dict[str, Any]:
    """Rule 40: current state, what happens next, when it last ran, how to undo."""
    from . import candidates, runner
    with SessionLocal() as s:
        cfg = settings_value(s)
        today = made_today(s)
        reason, open_types = _wait_reason(s, cfg)
        est = estimate_cost(s)
        last_auto = s.query(StudioProject).filter(StudioProject.review.isnot(None)) \
            .order_by(StudioProject.id.desc()).limit(20).all()
        last_auto = next((p for p in last_auto if (p.review or {}).get("auto")), None)
    nxt = candidates.next_pick(open_types) if cfg["enabled"] and not reason else None
    return {
        **cfg,
        "today": today,
        "estimate_usd": est,
        "waiting": reason,
        "next": {"title": nxt["title"], "media_type": nxt["media_type"], "score": nxt["score"]["total"]} if nxt else None,
        "last_check_at": state["last_check_at"],
        "last_result": state["last_result"],
        "last_started": state["last_started"] or (
            {"project_id": last_auto.id, "title": last_auto.title, "media_type": last_auto.media_type,
             "at": last_auto.created_at.isoformat() if last_auto.created_at else None} if last_auto else None),
        "busy": runner.busy(),
        "undo": "Switch off: nothing new starts. A breakdown already running finishes its step — press Stop on it to halt it.",
    }
