"""Countdown production: turn a queued countdown into a finished video.

Audit 2026-10-11 — before this, "Auto-Queue Video" made a Ready queue row with
no video, ran each title only to step 5, and nothing ever stitched them.

Each scheduler tick (after the LongForm automation, so breakdowns go first):
  1. For every Countdown row being made (research.production.state == "making"):
     count rendered segments and update the row's note.
  2. If Studio is idle and nothing waits in its line, start the next segment
     that isn't rendered (one at a time, through render — the daily cap applies).
  3. When every segment is rendered, stitch the countdown in a background job,
     attach the video, thumbnail and real chapters, and leave the row in
     Needs Review. The owner approves it like any other video.
A segment that fails stops that countdown and says which one, instead of looping.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any

from ... import control, logbus
from ...db import SessionLocal
from ...models import QueueItem, StudioProject

logger = logging.getLogger("scrapper.countdown.production")
UTC = datetime.timezone.utc
_stitch_lock = threading.Lock()


def _making(s) -> list[QueueItem]:
    from ..membership import COUNTDOWN_SOURCE
    return [q for q in s.query(QueueItem).filter(QueueItem.source == COUNTDOWN_SOURCE,
                                                 QueueItem.status == "review").order_by(QueueItem.id).all()
            if ((q.research or {}).get("production") or {}).get("state") == "making"]


def _set_production(q: QueueItem, **kw) -> None:
    r = dict(q.research or {})
    r["production"] = {**(r.get("production") or {}), **kw}
    q.research = r


def _next_stage(p: StudioProject) -> str:
    from .. import runner
    if p.stage_status == "done" and p.stage in runner.ORDER:
        i = runner.ORDER.index(p.stage)
        return runner.ORDER[min(i + 1, len(runner.ORDER) - 1)]
    return p.stage if p.stage in runner.ORDER else "gather"


def progress(s, q: QueueItem) -> dict[str, Any]:
    from .stitch import resolve_project_render
    pids = [int(x) for x in (q.research or {}).get("project_ids") or []]
    done, failed, waiting, todo = [], [], [], []
    for pid in pids:
        p = s.get(StudioProject, pid)
        if p is None or not p.tmdb_id:
            failed.append((pid, "not a real title (no TMDB match) — delete this countdown and research again"))
        elif resolve_project_render(p.id, p.render):
            done.append(pid)
        elif p.stage_status == "error":
            failed.append((pid, f"{p.title}: step '{p.stage}' failed — {(p.stage_message or '')[:120]}"))
        elif p.stage_status == "stopped":
            failed.append((pid, f"{p.title}: stopped at step '{p.stage}'"))
        elif p.stage_status in ("running", "queued", "paused"):
            waiting.append(pid)
        else:
            todo.append(p)
    return {"pids": pids, "done": done, "failed": failed, "waiting": waiting, "todo": todo}


def tick() -> dict[str, Any] | None:
    """One pass. Starts at most one segment step or one stitch; returns what it did."""
    from .. import runner
    did = None
    with SessionLocal() as s:
        for q in _making(s):
            pr = progress(s, q)
            n, total = len(pr["done"]), len(pr["pids"])
            if pr["failed"]:
                pid, why = pr["failed"][0]
                note = f"Countdown stopped: segment #{pid} — {why}. Fix it (or press ▶ Make video again to retry)."
                _set_production(q, state="stopped", reason=why)
                q.notes = note
                s.commit()
                logbus.log("error", "countdown_stopped", f"Countdown #{q.id}: {note}")
                continue
            note = f"In production: {n} of {total} segments made."
            if q.notes != note and n < total:
                q.notes = note
                s.commit()
            if n == total and total >= 2:
                if did is None and not _stitch_lock.locked():
                    _set_production(q, state="stitching")
                    q.notes = f"Stitching the countdown video ({total} segments)…"
                    s.commit()
                    control.start_thread("countdown", f"Countdown #{q.id}: stitch video", "studio", stitch_row, q.id,
                                         ref=q.id)
                    did = {"stitch": q.id}
                continue
            if did is None and pr["todo"] and not pr["waiting"] and not runner.busy().get("project_id") \
                    and not runner.queued_jobs():
                p = pr["todo"][0]
                stage = _next_stage(p)
                try:
                    runner.start(p.id, stage, auto=True, until="render")
                    did = {"segment": p.id, "stage": stage, "countdown": q.id}
                    logbus.log("info", "countdown_segment", f"Countdown #{q.id}: making segment #{p.id} {p.title} "
                                                            f"(from step '{stage}')", project=p.id)
                except RuntimeError:
                    pass   # Studio got busy in between: next tick
    return did


def stitch_row(queue_id: int) -> dict[str, Any]:
    """Background job: stitch a countdown's rendered segments and attach the video."""
    from . import engine, stitch
    with _stitch_lock:
        with SessionLocal() as s:
            q = s.get(QueueItem, queue_id)
            if q is None:
                return {"error": "queue row gone"}
            pids = [int(x) for x in (q.research or {}).get("project_ids") or []]
            title = q.title or q.video_name
            countdown = (q.research or {}).get("countdown") or {}
        try:
            res = stitch.stitch_countdown_compilation(project_ids=pids, title=title, send_to_queue=False)
        except Exception as exc:  # noqa: BLE001 — say why on the row, don't loop
            with SessionLocal() as s:
                q = s.get(QueueItem, queue_id)
                if q is not None:
                    _set_production(q, state="stopped", reason=str(exc)[:300])
                    q.notes = f"Countdown stitch failed: {str(exc)[:200]}. Press ▶ Make video to retry."
                    s.commit()
            logbus.log("error", "countdown_stitch_failed", f"Countdown #{queue_id}: {exc}")
            raise
        with SessionLocal() as s:
            q = s.get(QueueItem, queue_id)
            if q is None:
                return res
            entries = sorted((countdown.get("entries") or []), key=lambda e: e.get("rank", 0), reverse=True)
            entries = [e for e in entries if (e.get("linked_project") or {}).get("id") in pids] or entries
            lines = [f"{c['time_formatted']} {c['title']}" for c in res["chapters"]]
            q.video_path = res["video_path"]
            q.thumb_path = res["primary_thumb"]
            q.description = engine.countdown_description(countdown, entries, lines)
            r = dict(q.research or {})
            r["chapters"] = res["chapters"]
            r["compilation_id"] = res["compilation_id"]
            q.research = r
            _set_production(q, state="done", finished_at=datetime.datetime.now(UTC).isoformat())
            q.notes = (f"✓ Countdown video made ({res['total_minutes']} min). Watch it, then approve it to post.")
            s.commit()
        logbus.log("info", "countdown_made", f"Countdown #{queue_id} video made: {res['total_minutes']} min")
        return res


def make(queue_id: int) -> dict[str, Any]:
    """▶ Make video: (re)start production for a countdown row (owner action)."""
    with SessionLocal() as s:
        q = s.get(QueueItem, queue_id)
        from ..membership import COUNTDOWN_SOURCE
        if q is None or q.source != COUNTDOWN_SOURCE:
            raise ValueError("Not a countdown row")
        if q.video_path:
            raise ValueError("This countdown already has its video")
        pr = progress(s, q)
        bad = [why for pid, why in pr["failed"] if "not a real title" in why]
        if bad:
            raise ValueError("This countdown has entries that aren't real titles — delete it and research a list "
                             "of movies or shows instead.")
        for pid, _ in pr["failed"]:           # retry failed segments from the step that failed
            p = s.get(StudioProject, pid)
            if p is not None and p.stage_status in ("error", "stopped"):
                p.stage_status = "idle"
        if q.status != "review":
            q.status = "review"
        _set_production(q, state="making", reason=None)
        q.notes = f"In production: {len(pr['done'])} of {len(pr['pids'])} segments made."
        s.commit()
        return {"ok": True, "queue_item_id": q.id, "done": len(pr["done"]), "total": len(pr["pids"])}
