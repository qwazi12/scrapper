"""Background jobs that survive restarts and deploys.

Jobs live in memory (control.py), so a restart used to kill them silently.
Now each long job also gets a ResumableJob row: written when it starts, closed
when it finishes / is stopped / fails, with a checkpoint of how far it got.
A row still "running" whose server has gone quiet was cut off, so it is
launched again from its checkpoint — at most MAX_RESUMES times, then it's
marked failed with the reason.

Deploys overlap: Railway starts the new server while the old one is still
running (and finishing) its jobs. So each server stamps the rows it runs
(`owner` = this process) and refreshes `updated_at` every HEARTBEAT seconds;
a new server only resumes rows whose owner has been silent for STALE seconds,
and keeps checking for WATCH seconds after it starts (an old server can take
a while to be shut down). Never two copies of one job.

Kinds and how they resume:
  studio          the interrupted step re-runs; an auto-chain carries on
  drive_sync      re-runs; files already in the queue are skipped
  channel_ingest  re-runs; videos already in the queue are skipped
  bulk_ai         continues with the videos it hadn't finished
  drive_save      the upload starts again

Budget pauses: a job that hit the daily spend cap is closed as "budget_paused"
(not failed) with its checkpoint. When the day resets, resume_budget_paused()
relaunches those first, oldest first, before anything new is started
(the Studio automation waits while any remain). They don't count as resumes.
"""

from __future__ import annotations

import datetime
import logging
import threading
import uuid
from typing import Any, Callable

from . import control, logbus, worker_leases as leases
from .db import SessionLocal
from .models import ResumableJob

logger = logging.getLogger("scrapper.resume")
MAX_RESUMES = 2
BOOT = uuid.uuid4().hex[:16]          # this server process
HEARTBEAT, STALE, WATCH = 20, 75, 600
_launchers: dict[str, Callable[[dict, dict, int], Any]] = {}
_lock = threading.RLock()
_claims: dict[int, leases.Handle] = {}
_active_rids: set[int] = set()


def launcher(kind: str):
    """Register how to start `kind` again: fn(params, checkpoint, rid)."""
    def deco(fn):
        _launchers[kind] = fn
        return fn
    return deco


def begin(kind: str, label: str, params: dict) -> int:
    with SessionLocal() as s:
        h = leases.acquire(s, leases.resource(kind, params), BOOT)
        row = ResumableJob(kind=kind, label=label, params=params, checkpoint={}, status="running", owner=BOOT)
        s.add(row); s.commit(); _claims[row.id] = h
        return row.id


def reopen(rid: int) -> None:
    with _lock, SessionLocal() as s:
        row=s.get(ResumableJob,rid)
        if not row: raise RuntimeError("Resumable job not found")
        if rid in _active_rids: raise leases.LeaseLost("Job already has an active invocation in this process")
        if rid in _claims and row.owner == BOOT and row.status == "running":
            leases.guard(s, _claims[rid]); s.commit(); return
        h=leases.acquire(s, leases.resource(row.kind,row.params), BOOT)
        row.status, row.owner = "running", BOOT
        s.commit(); _claims[rid]=h


def _mutate(rid, fn):
    if not rid: return False
    h=leases.current() or _claims.get(rid)
    with _lock, SessionLocal() as s:
        row=s.get(ResumableJob,rid)
        if not row: return False
        if row.status == "running":
            if not h or row.owner != h.owner: raise leases.LeaseLost("Job is no longer owned by this worker")
            leases.guard(s,h)
        elif leases.current():
            raise leases.LeaseLost("Job is no longer running")
        fn(row); s.commit(); return True


def checkpoint(rid: int | None, **data) -> None:
    _mutate(rid, lambda row: setattr(row,"checkpoint",{**(row.checkpoint or {}),**data}))


def add_done(rid: int | None, item: Any) -> None:
    _mutate(rid, lambda row: setattr(row,"checkpoint",{**(row.checkpoint or {}),
        "done":[*(row.checkpoint or {}).get("done",[]),item]}))


def finish(rid: int | None, status: str, error: str | None = None) -> None:
    if not rid: return
    h=leases.current() or _claims.get(rid)
    try:
        def apply(row):
            row.status, row.last_error=status,(str(error)[:500] if error else None)
        if h:
            with SessionLocal() as s:
                leases.guard(s,h)
                row=s.get(ResumableJob,rid)
                if row and row.owner == h.owner:
                    apply(row); leases.release(s,h); s.commit()
        else:
            _mutate(rid,apply)
    except leases.LeaseLost:
        logger.warning("Ignoring stale completion for job %s", rid)
    finally:
        if _claims.get(rid) == h: _claims.pop(rid,None)
        if leases.current() == h: leases._current.set(None)


def bind_work(rid, fn):
    with _lock:
        h=_claims.get(rid)
        if not h or rid in _active_rids: raise leases.LeaseLost("Cannot start duplicate work or work without a lease")
        _active_rids.add(rid)
    def wrapped():
        try:
            with leases.bind(h), leases.keepalive(h):
                leases.guard()
                return fn()
        finally:
            with _lock: _active_rids.discard(rid)
    return wrapped


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

    return control.start_thread(kind, label, scope, bind_work(rid, work), ref=ref)


def _heartbeat_once() -> None:
    now = datetime.datetime.now(datetime.timezone.utc)
    with _lock, SessionLocal() as s:
        for rid,h in list(_claims.items()):
            row=s.get(ResumableJob,rid)
            if row and row.status == "running" and row.owner == BOOT and leases.renew(s,h):
                row.updated_at=now
        s.commit()


def start_background(watch: int | None = None) -> None:
    """Startup: keep this server's jobs alive, and resume dead servers' jobs as
    soon as they've been silent STALE seconds (checked for `watch` seconds)."""
    import time

    def beat():
        while True:
            try:
                _heartbeat_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("job heartbeat failed: %s", exc)
            time.sleep(HEARTBEAT)

    def watcher():
        end = time.time() + watch if watch is not None else None
        while end is None or time.time() < end:
            try:
                resume_interrupted()
            except Exception as exc:  # noqa: BLE001
                logger.warning("resume check failed: %s", exc)
            time.sleep(30)

    threading.Thread(target=beat, daemon=True, name="job-heartbeat").start()
    threading.Thread(target=watcher, daemon=True, name="job-resume-watch").start()


def mark_shutdown() -> int:
    """Server shutting down normally (a deploy/restart sends SIGTERM): flag this
    server's running jobs so their resume doesn't count toward MAX_RESUMES —
    that limit is for crash loops, not deploys (2026-10-03: a day of deploys
    made Verity and a render batch give up after 3 cut-offs)."""
    n = 0
    with _lock, SessionLocal() as s:
        for row in s.query(ResumableJob).filter(ResumableJob.status == "running", ResumableJob.owner == BOOT).all():
            row.checkpoint = {**(row.checkpoint or {}), "cut_by_shutdown": True}
            n += 1
        s.commit()
    return n


def resume_interrupted(stale: int = STALE) -> int:
    """Relaunch jobs whose server died. Returns how many were resumed."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=stale)
    with SessionLocal() as s:
        rows = [r for r in s.query(ResumableJob).filter(ResumableJob.status == "running").order_by(ResumableJob.id).all()
                if _aware(r.updated_at) < cutoff]
        todo = []
        for row in rows:
            cp = dict(row.checkpoint or {})
            shutdown = cp.pop("cut_by_shutdown", False)
            exhausted = row.attempts >= MAX_RESUMES and not shutdown
            # Claim expired resource and compare the scanned job version in one transaction.
            try:
                with s.begin_nested():
                    h=leases.acquire(s,leases.resource(row.kind,row.params),BOOT)
                    from sqlalchemy import update
                    won=s.execute(update(ResumableJob).where(ResumableJob.id==row.id,
                        ResumableJob.status=="running", ResumableJob.owner==row.owner,
                        ResumableJob.updated_at==row.updated_at).values(owner=BOOT,
                        attempts=row.attempts+(0 if shutdown or exhausted else 1),checkpoint=cp,
                        updated_at=datetime.datetime.now(datetime.timezone.utc))).rowcount
                    if won != 1: raise leases.LeaseLost("Job was claimed concurrently")
                if exhausted or row.kind not in _launchers:
                    row.status="failed"
                    row.last_error=(f"interrupted by server restarts {row.attempts + 1} times — not resumed again"
                        if row.kind in _launchers else f"no way to resume '{row.kind}'")
                    leases.release(s,h)
                    continue
                _claims[row.id]=h
                todo.append((row.id,row.kind,dict(row.params or {}),cp,row.label,row.attempts))
            except leases.LeaseLost:
                continue
        s.commit()
    resumed = 0
    for rid, kind, params, cp, label, n in todo:
        try:
            _launchers[kind](params, cp, rid)
            resumed += 1
            logbus.log("warning", "job_resumed", f"{label}: resumed after a server restart (attempt {n} of {MAX_RESUMES})",
                       job=rid, kind=kind)
        except RuntimeError as exc:
            if kind == "studio":
                # Studio is busy (one job at a time): wait in line instead of failing —
                # it starts next, from the step it was cut off at (2026-10-03: a
                # restart's resume lost the race to a new automation pick and failed).
                finish(rid, "queued")
                try:
                    from .models import StudioProject
                    with SessionLocal() as s:
                        proj = s.get(StudioProject, int(params.get("project_id", -1)))
                        if proj:
                            proj.stage_status = "queued"
                            proj.stage_message = (f"{cp.get('stage') or params.get('stage')} cut off by a restart — "
                                                  "waiting in line, resumes from this step")
                            s.commit()
                except Exception as e2:  # noqa: BLE001 — the label is cosmetic; the line still works
                    logger.warning("could not relabel queued project: %s", e2)
                logbus.log("warning", "job_resumed", f"{label}: cut off by a restart — waiting in line, "
                           f"starts next from its step ({exc})", job=rid, kind=kind)
            else:
                finish(rid, "failed", f"could not resume: {exc}")
                logbus.log("error", "job_resume_failed", f"{label}: could not resume — {exc}", job=rid, kind=kind)
        except Exception as exc:  # noqa: BLE001 — record why, keep going with the others
            finish(rid, "failed", f"could not resume: {exc}")
            logbus.log("error", "job_resume_failed", f"{label}: could not resume — {exc}", job=rid, kind=kind)
    return resumed


def _aware(t: datetime.datetime | None) -> datetime.datetime:
    if t is None:
        return datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def budget_paused(s=None) -> list[ResumableJob]:
    own = s is None
    s = s or SessionLocal()
    try:
        return s.query(ResumableJob).filter(ResumableJob.status == "budget_paused").order_by(ResumableJob.id).all()
    finally:
        if own:
            s.close()


def resume_budget_paused() -> int:
    """Once there's budget again, relaunch jobs the daily cap paused, oldest
    first. A launcher that's busy (one Studio job at a time) raises
    RuntimeError before reopening the row, so that job waits for the next tick."""
    from . import costs
    if costs.daily_status()["reached"]:
        return 0
    with SessionLocal() as s:
        todo = [(r.id, r.kind, dict(r.params or {}), dict(r.checkpoint or {}), r.label) for r in budget_paused(s)]
    resumed = 0
    for rid, kind, params, cp, label in todo:
        if kind not in _launchers:
            finish(rid, "failed", f"no way to resume '{kind}'")
            continue
        claimed=False
        try:
            reopen(rid)
            claimed=True
            _launchers[kind](params, cp, rid)
        except RuntimeError as exc:  # busy: try again next tick, keep the order
            if claimed: finish(rid, "budget_paused")
            logger.info("budget-paused job %s waits: %s", rid, exc)
            break
        except Exception as exc:  # noqa: BLE001
            finish(rid, "failed", f"could not resume: {exc}")
            logbus.log("error", "job_resume_failed", f"{label}: could not resume after the daily cap — {exc}", job=rid)
            continue
        resumed += 1
        logbus.log("info", "job_budget_resumed", f"{label}: resumed — new day's budget", job=rid, kind=kind)
    return resumed


def assert_current_lease() -> None:
    leases.guard()
