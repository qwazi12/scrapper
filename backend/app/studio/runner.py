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


def run_one(project_id: int, name: str) -> str:
    """Run a single stage synchronously (used by the thread and by tests)."""
    if name not in STAGES:
        raise ValueError(f"Unknown stage '{name}'. Stages: {', '.join(STAGES)}")
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
            summary = STAGES[name](project_id) or "done"
    except control.Cancelled:
        _set(project_id, stage_status="stopped", stage_message=f"{name} stopped by user — re-run it when ready")
        logbus.log("warning", "studio_stage_stopped", f"Studio #{project_id}: {name} stopped by user",
                   project=project_id, stage=name)
        raise
    except Exception as exc:
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


def start(project_id: int, name: str, auto: bool = False) -> None:
    """Start a stage (and, with auto, the following ones) in the background."""
    if name not in STAGES:
        raise ValueError(f"Unknown stage '{name}'")
    if not _lock.acquire(blocking=False):
        raise RuntimeError(f"Studio is busy with #{_current['project_id']} ({_current['stage']}); try again shortly")
    _set(project_id, stage=name, stage_status="running", stage_message=f"{name} queued…")

    def work() -> None:
        try:
            names = [name]
            if auto:
                i = ORDER.index(name) if name in ORDER else len(ORDER)
                stop = ORDER.index("script")
                names = ORDER[i:stop + 1] if i <= stop else [name]
            for n in names:
                _current.update(project_id=project_id, stage=n)
                try:
                    run_one(project_id, n)
                except Exception:
                    return
        finally:
            _current.update(project_id=None, stage=None)
            _lock.release()

    threading.Thread(target=work, daemon=True, name=f"studio-{project_id}-{name}").start()
