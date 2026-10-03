"""Render many reviewed breakdowns one after another (step 6 in bulk).

POST /api/studio/render-batch {ids} starts one batch: each project runs the
steps it still needs (plan if missing, then render) through the normal runner,
one at a time, waiting for Studio to be free between them. It is a
ResumableJob ("studio_batch", checkpoint = finished ids), so a deploy or
restart carries on with the projects it hadn't finished; Stop stops the batch
and the render in progress. Projects that aren't ready (no script, archived,
already running) are skipped with the reason.
"""

from __future__ import annotations

import datetime
import threading
import time
from typing import Any

from .. import control, logbus, resume
from ..db import SessionLocal
from ..models import ResumableJob, StudioProject
from . import runner

POLL = 2.0
MAX_ITEMS = 50
status: dict[str, Any] = {"running": False, "rid": None, "total": 0, "done": 0, "current": None,
                          "results": [], "started_at": None, "finished_at": None}
_lock = threading.Lock()


def _needs(p: StudioProject) -> str | None:
    """First step this project needs to end up rendered, or None if it can't."""
    if (p.archive or {}).get("archived_at"):
        return None
    if not p.script:
        return None
    return "render" if p.plan else "plan"


def _wait_idle(job_check) -> None:
    while runner.busy().get("project_id"):
        job_check()
        time.sleep(POLL)


def _wait_done(pid: int, job_check) -> StudioProject:
    time.sleep(POLL)
    while True:
        job_check()
        with SessionLocal() as s:
            p = s.get(StudioProject, pid)
            if p.stage_status not in ("running", "queued") and runner.busy().get("project_id") != pid:
                s.expunge(p)
                return p
        time.sleep(POLL)


def _run(ids: list[int], rid: int) -> None:
    for pid in ids:
        control.check()
        status["current"] = pid
        with SessionLocal() as s:
            p = s.get(StudioProject, pid)
            first = _needs(p) if p else None
            title = p.title if p else f"#{pid}"
            before = (p.render or {}).get("rendered_at") if p else None
        if not first:
            why = "not found" if not p else "archived — restore footage first" if (p.archive or {}).get("archived_at") \
                else "no script yet"
            status["results"].append({"id": pid, "title": title, "ok": False, "why": why})
            resume.add_done(rid, pid)
            continue
        control.progress(f"Rendering {title} ({status['done'] + 1} of {status['total']})")
        # Join Studio's waiting line (like any other start) so the card shows
        # "waiting in line" right away and nothing jumps ahead of it. It used to
        # poll for a free Studio, so a queued job always got in first and the
        # selected video showed no sign of the render (owner, 2026-10-03).
        if runner.start_or_queue(pid, first, auto=True, until="render") == "queued":
            control.progress(f"{title} is waiting in Studio's line to render ({status['done'] + 1} of {status['total']})")
        try:
            p = _wait_done(pid, control.check)
        except control.Cancelled:
            runner.stop(pid)
            raise
        ok = p.stage == "render" and p.stage_status == "done" and (p.render or {}).get("rendered_at") != before
        status["results"].append({"id": pid, "title": title, "ok": ok,
                                  "why": None if ok else (p.stage_message or p.stage_status)})
        status["done"] += 1
        resume.add_done(rid, pid)


def start(ids: list[int], rid: int | None = None, already_done: list[int] | None = None) -> dict[str, Any]:
    ids = list(dict.fromkeys(int(i) for i in ids))[:MAX_ITEMS]
    todo = [i for i in ids if i not in set(already_done or [])]
    if not todo:
        raise ValueError("Nothing to render")
    with _lock:
        if status["running"]:
            raise RuntimeError("A render batch is already running — stop it or wait for it to finish")
        status.update(running=True, total=len(todo), done=0, current=None, results=[],
                      started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), finished_at=None)
    label = f"Render {len(todo)} breakdown(s) one by one" + (" (resumed)" if already_done is not None else "")

    def work() -> None:
        try:
            _run(todo, status["rid"])
        finally:
            status["running"] = False
            status["current"] = None
            status["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            ok = sum(1 for r in status["results"] if r["ok"])
            logbus.log("info" if ok == len(status["results"]) else "warning", "studio_render_batch",
                       f"Render batch: {ok} of {len(status['results'])} rendered")

    if rid:
        resume.reopen(rid)
    else:
        rid = resume.begin("studio_batch", label, {"ids": ids})
    status["rid"] = rid
    resume.tracked_thread("studio_batch", label, "studio", {"ids": ids}, work, rid=rid)
    return dict(status)


def _resume(p: dict, cp: dict, rid: int) -> dict[str, Any]:
    return start(p.get("ids", []), rid=rid, already_done=cp.get("done", []))


resume.launcher("studio_batch")(_resume)


def public() -> dict[str, Any]:
    out = dict(status)
    with SessionLocal() as s:
        row = s.get(ResumableJob, status["rid"]) if status["rid"] else None
        out["job_status"] = row.status if row else None
    return out
