"""Stage runner for Studio projects.

A stage is a function (project_id) -> short summary string. It reads what it
needs from the project row, saves its own output, and raises on failure. The
runner runs one stage at a time in a background thread (renders and shot
detection are CPU-heavy), records running/done/error on the row, and logs
every start, finish and failure — no silent work.
"""

from __future__ import annotations

import logging
import pathlib
import threading
import traceback
from typing import Callable

from .. import control, logbus
from ..config import settings
from ..db import SessionLocal
from ..models import StudioProject

logger = logging.getLogger("scrapper.studio")

# Pipeline order. "auto" runs every stage after the one given, stopping at the
# first error or at "script" (owner reviews the script before voice/render).
ORDER = ["gather", "trailer", "shots", "script", "plan", "render"]
STAGES: dict[str, Callable[[int], str]] = {}

_lock = threading.Lock()
_current: dict[str, object] = {"project_id": None, "stage": None}
_stop_events: dict[int, threading.Event] = {}


def stage(name: str):
    def deco(fn: Callable[[int], str]) -> Callable[[int], str]:
        STAGES[name] = fn
        return fn
    return deco


def project_dir(project_id: int) -> pathlib.Path:
    p = settings.data_path / "studio" / str(project_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _set(project_id: int, **fields) -> None:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        if p is None:
            return
        for k, v in fields.items():
            setattr(p, k, v)
        s.commit()


def busy() -> dict[str, object]:
    return dict(_current)


def is_stopped(project_id: int) -> bool:
    ev = _stop_events.get(project_id)
    return bool(ev and ev.is_set())


def stop(project_id: int) -> bool:
    """Stop all activity on a studio project immediately, killing child processes."""
    ev = _stop_events.get(project_id)
    if ev:
        ev.set()
    from .. import control
    control.stop_jobs_for("studio", project_id)
    with SessionLocal() as s:
        from ..models import ResumableJob
        for r in s.query(ResumableJob).filter(ResumableJob.kind == "studio", ResumableJob.status == "running").all():
            if int((r.params or {}).get("project_id", -1)) == project_id:
                r.status = "stopped"
                r.last_error = "Stopped by user"
        s.commit()
    _set(project_id, stage_status="stopped", stage_message="Stopped by user — re-run it when ready")
    logbus.log("warning", "studio_stage_stopped", f"Studio #{project_id}: stopped by user", project=project_id)
    return True


def run_one(project_id: int, name: str, stop_event: threading.Event | None = None) -> str:
    """Run a single stage synchronously (used by the thread and by tests)."""
    if name not in STAGES:
        raise ValueError(f"Unknown stage '{name}'. Stages: {', '.join(STAGES)}")
    ev = stop_event or _stop_events.get(project_id)
    if ev and ev.is_set():
        _set(project_id, stage_status="stopped", stage_message=f"{name} stopped by user — re-run it when ready")
        raise control.Cancelled(f"Studio #{project_id}: {name} stopped by user")
    if name in ("script", "plan"):
        # Re-running these replaces text/plan the owner may have edited: keep an undo point.
        from .. import undo
        with SessionLocal() as s:
            p = s.get(StudioProject, project_id)
            if p is not None and (p.script or p.plan):
                undo.record(s, f"studio:{project_id}", f"Re-run {name}", rows=[p], model="studio_projects",
                            fields=["script", "plan", "render", "target_minutes"])
                s.commit()
    _set(project_id, stage=name, stage_status="running", stage_message=f"{name} running…")
    logbus.log("info", "studio_stage_start", f"Studio #{project_id}: {name} started", project=project_id, stage=name)
    try:
        from .. import costs
        with control.job("studio", f"Studio #{project_id}: {name}", scope="studio", ref=project_id), \
                costs.operation(f"studio:{name}", ref=f"studio:{project_id}"):
            if ev and ev.is_set():
                raise control.Cancelled(f"Studio #{project_id}: {name} stopped by user")
            summary = STAGES[name](project_id) or "done"
            if ev and ev.is_set():
                raise control.Cancelled(f"Studio #{project_id}: {name} stopped by user")
    except control.Cancelled:
        _set(project_id, stage_status="stopped", stage_message=f"{name} stopped by user — re-run it when ready")
        logbus.log("warning", "studio_stage_stopped", f"Studio #{project_id}: {name} stopped by user",
                   project=project_id, stage=name)
        raise
    except Exception as exc:
        if ev and ev.is_set():
            _set(project_id, stage_status="stopped", stage_message=f"{name} stopped by user — re-run it when ready")
            logbus.log("warning", "studio_stage_stopped", f"Studio #{project_id}: {name} stopped by user",
                       project=project_id, stage=name)
            raise control.Cancelled(f"Studio #{project_id}: {name} stopped by user") from exc
        msg = f"{type(exc).__name__}: {exc}"[:1000]
        logger.error("Studio #%s %s failed:\n%s", project_id, name, traceback.format_exc())
        _set(project_id, stage_status="error", stage_message=msg)
        logbus.log("error", "studio_stage_error", f"Studio #{project_id}: {name} failed — {msg}",
                   project=project_id, stage=name)
        raise
    _set(project_id, stage_status="done", stage_message=summary)
    logbus.log("info", "studio_stage_done", f"Studio #{project_id}: {name} — {summary}",
               project=project_id, stage=name)
    return summary


def recover_interrupted() -> int:
    """Startup: a step can only be running inside this process, so any project
    still marked running/queued was cut off by a restart or deploy. Mark it
    stopped so it can be re-run — otherwise it looked busy forever, Stop found no
    job to stop, and edits were refused (seen 2026-10-02)."""
    from ..models import ResumableJob
    with SessionLocal() as s:
        # Steps with a job record are handled by resume.py (resumed once their
        # old server is gone — deploys overlap, so it may still be running them).
        tracked = {int((r.params or {}).get("project_id", -1)) for r in
                   s.query(ResumableJob).filter(ResumableJob.kind == "studio", ResumableJob.status == "running").all()}
        stuck = [p for p in s.query(StudioProject).filter(StudioProject.stage_status == "running").all()
                 if p.id not in tracked]
        for p in stuck:
            p.stage_status = "stopped"
            p.stage_message = f"{p.stage} was interrupted by a server restart — re-run it"
            logbus.log("warning", "studio_stage_interrupted",
                       f"Studio #{p.id}: {p.stage} was interrupted by a server restart", project=p.id, stage=p.stage)
        s.commit()
        return len(stuck)


def start(project_id: int, name: str, auto: bool = False, rid: int | None = None) -> None:
    """Start a stage (and, with auto, the following ones) in the background.
    Recorded as a ResumableJob: a restart re-runs the step it was on and the
    auto-chain carries on (checkpoint = the current step)."""
    from .. import resume
    if name not in STAGES:
        raise ValueError(f"Unknown stage '{name}'")
    if not _lock.acquire(blocking=False):
        raise RuntimeError(f"Studio is busy with #{_current['project_id']} ({_current['stage']}); try again shortly")
    stop_event = threading.Event()
    _stop_events[project_id] = stop_event
    _set(project_id, stage=name, stage_status="running",
         stage_message=f"{name} resumed after a server restart…" if rid else f"{name} queued…")
    if rid:
        resume.reopen(rid)
    else:
        rid = resume.begin("studio", f"Studio #{project_id}: {name}" + (" + following steps" if auto else ""),
                           {"project_id": project_id, "stage": name, "auto": auto})

    def work() -> None:
        outcome, err = "done", None
        try:
            names = [name]
            if auto:
                i = ORDER.index(name) if name in ORDER else len(ORDER)
                stop_idx = ORDER.index("script")
                names = ORDER[i:stop_idx + 1] if i <= stop_idx else [name]
            for n in names:
                if stop_event.is_set():
                    outcome = "stopped"
                    _set(project_id, stage_status="stopped",
                         stage_message=f"{n} stopped by user — re-run it when ready")
                    return
                _current.update(project_id=project_id, stage=n)
                resume.checkpoint(rid, stage=n)
                try:
                    run_one(project_id, n, stop_event=stop_event)
                except control.Cancelled:
                    outcome = "stopped"
                    return
                except Exception as exc:
                    if stop_event.is_set():
                        outcome = "stopped"
                        _set(project_id, stage_status="stopped",
                             stage_message=f"{n} stopped by user — re-run it when ready")
                        return
                    outcome, err = "failed", str(exc)
                    return
                if stop_event.is_set():
                    outcome = "stopped"
                    _set(project_id, stage_status="stopped",
                         stage_message=f"{n} stopped by user — re-run it when ready")
                    return
        finally:
            resume.finish(rid, outcome, err)
            _current.update(project_id=None, stage=None)
            _stop_events.pop(project_id, None)
            _lock.release()

    threading.Thread(target=work, daemon=True, name=f"studio-{project_id}-{name}").start()


def _resume(p: dict, cp: dict, rid: int) -> None:
    start(int(p["project_id"]), cp.get("stage") or p["stage"], bool(p.get("auto")), rid)


from .. import resume as _resume_mod  # noqa: E402  (registered at import)
_resume_mod.launcher("studio")(_resume)
