"""Background jobs that survive restarts and deploys.

Jobs live in memory (control.py), so a restart used to kill them silently.
Now each long job also gets a ResumableJob row: written when it starts, closed
when it finishes / is stopped / fails, with a checkpoint of how far it got.
A row still "running" at startup was cut off, so it is launched again from its
checkpoint — at most MAX_RESUMES times, then it's marked failed with the reason.

Kinds and how they resume:
  studio          the interrupted step re-runs; an auto-chain carries on
  drive_sync      re-runs; files already in the queue are skipped
  channel_ingest  re-runs; videos already in the queue are skipped
  bulk_ai         continues with the videos it hadn't finished
  drive_save      the upload starts again
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from . import control, logbus
from .db import SessionLocal
from .models import ResumableJob

logger = logging.getLogger("scrapper.resume")
MAX_RESUMES = 2
_launchers: dict[str, Callable[[dict, dict, int], Any]] = {}
_lock = threading.Lock()


def launcher(kind: str):
    """Register how to start `kind` again: fn(params, checkpoint, rid)."""
    def deco(fn):
        _launchers[kind] = fn
        return fn
    return deco


def begin(kind: str, label: str, params: dict) -> int:
    with SessionLocal() as s:
        row = ResumableJob(kind=kind, label=label, params=params, checkpoint={}, status="running")
        s.add(row)
        s.commit()
        return row.id


def reopen(rid: int) -> None:
    _update(rid, status="running")


def checkpoint(rid: int | None, **data) -> None:
    if rid:
        with _lock, SessionLocal() as s:
            row = s.get(ResumableJob, rid)
            if row:
                row.checkpoint = {**(row.checkpoint or {}), **data}
                s.commit()


def add_done(rid: int | None, item: Any) -> None:
    """Append one finished item to checkpoint["done"] (bulk jobs)."""
    if rid:
        with _lock, SessionLocal() as s:
            row = s.get(ResumableJob, rid)
            if row:
                cp = dict(row.checkpoint or {})
                cp["done"] = [*cp.get("done", []), item]
                row.checkpoint = cp
                s.commit()


def finish(rid: int | None, status: str, error: str | None = None) -> None:
    if rid:
        _update(rid, status=status, last_error=(error or None) and str(error)[:500])


def _update(rid: int, **fields) -> None:
    with _lock, SessionLocal() as s:
        row = s.get(ResumableJob, rid)
        if row:
            for k, v in fields.items():
                setattr(row, k, v)
            s.commit()


def tracked_thread(kind: str, label: str, scope: str, params: dict, fn: Callable[[], Any],
                   rid: int | None = None, ref: Any = None) -> str:
    """control.start_thread + a ResumableJob row closed with the outcome."""
    if rid:
        reopen(rid)
    else:
        rid = begin(kind, label, params)

    def work():
        try:
            out = fn()
        except control.Cancelled:
            finish(rid, "stopped")
            raise
        except BaseException as exc:
            finish(rid, "failed", str(exc))
            raise
        finish(rid, "done")
        return out

    return control.start_thread(kind, label, scope, work, ref=ref)


def resume_interrupted() -> int:
    """Startup: relaunch jobs a restart cut off. Returns how many were resumed."""
    with SessionLocal() as s:
        rows = s.query(ResumableJob).filter(ResumableJob.status == "running").order_by(ResumableJob.id).all()
        todo = []
        for row in rows:
            if row.attempts >= MAX_RESUMES or row.kind not in _launchers:
                row.status = "failed"
                row.last_error = (f"interrupted by server restarts {row.attempts + 1} times — not resumed again"
                                  if row.kind in _launchers else f"no way to resume '{row.kind}'")
                logbus.log("error", "job_not_resumed", f"{row.label}: {row.last_error}")
                continue
            row.attempts += 1
            todo.append((row.id, row.kind, dict(row.params or {}), dict(row.checkpoint or {}), row.label, row.attempts))
        s.commit()
    resumed = 0
    for rid, kind, params, cp, label, n in todo:
        try:
            _launchers[kind](params, cp, rid)
            resumed += 1
            logbus.log("warning", "job_resumed", f"{label}: resumed after a server restart (attempt {n} of {MAX_RESUMES})",
                       job=rid, kind=kind)
        except Exception as exc:  # noqa: BLE001 — record why, keep going with the others
            finish(rid, "failed", f"could not resume: {exc}")
            logbus.log("error", "job_resume_failed", f"{label}: could not resume — {exc}", job=rid, kind=kind)
    return resumed
