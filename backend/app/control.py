"""Job registry + Stop for every long-running process.

Modelled on manhwa's job control (cooperative stop between steps, queued jobs
never start, stale "stopping" jobs retired by a sweep) with one improvement:
Stop also KILLS the running ffmpeg / yt-dlp child at once, because everything
here that runs a subprocess can be redone (a render, a download), so there is
no half-written work worth waiting for.

Use:
    with control.job("render", "Studio #3: render", scope="studio", ref=3):
        control.check()              # between steps: raises Cancelled on Stop
        control.run([...ffmpeg...])  # subprocess that Stop can kill

Only processes inside this server are covered. An upload already handed to
Upload-Post, or a download on the home Mac worker, cannot be recalled from here.
"""

from __future__ import annotations

import contextlib
import datetime
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

STOP_GRACE_SECONDS = 120   # a stop not acknowledged by then is applied by the sweep
KEEP_FINISHED = 30


class Cancelled(Exception):
    """Raised inside a job when someone pressed Stop."""


@dataclass
class Job:
    id: str
    kind: str
    label: str
    scope: str
    ref: Any = None
    status: str = "running"          # running | stopping | cancelled | done | error
    message: str = ""
    result: Any = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    heartbeat: float = field(default_factory=time.time)
    stop_requested_at: float | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    procs: set = field(default_factory=set)

    def public(self) -> dict[str, Any]:
        iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat() if t else None
        return {"id": self.id, "kind": self.kind, "label": self.label, "scope": self.scope, "ref": self.ref,
                "status": self.status, "message": self.message, "started_at": iso(self.started_at),
                "finished_at": iso(self.finished_at), "elapsed": round((self.finished_at or time.time()) - self.started_at),
                "result": self.result}


_jobs: dict[str, Job] = {}
_lock = threading.Lock()
_local = threading.local()


def current() -> Job | None:
    return getattr(_local, "job", None)


def adopt(j: Job | None) -> None:
    """Make j the current job of this thread (for helper threads / async tasks)."""
    _local.job = j


def check() -> None:
    """Between steps: stop here if Stop was pressed."""
    j = current()
    if j is not None:
        j.heartbeat = time.time()
        if j.stop.is_set():
            raise Cancelled(f"{j.label} stopped")


def stopped() -> bool:
    j = current()
    return bool(j and j.stop.is_set())


def progress(message: str) -> None:
    j = current()
    if j is not None:
        j.message = message[:300]
        j.heartbeat = time.time()


@contextlib.contextmanager
def job(kind: str, label: str, scope: str, ref: Any = None) -> Iterator[Job]:
    j = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label, scope=scope, ref=ref)
    with _lock:
        _jobs[j.id] = j
    prev = current()
    _local.job = j
    try:
        yield j
        j.status = "cancelled" if j.stop.is_set() else "done"
    except Cancelled:
        j.status, j.message = "cancelled", "stopped by user"
        raise
    except Exception as exc:
        # A process we killed on Stop surfaces as an error; report it as a stop.
        if j.stop.is_set():
            j.status, j.message = "cancelled", "stopped by user"
            raise Cancelled(f"{label} stopped") from exc
        j.status, j.message = "error", str(exc)[:300]
        raise
    finally:
        j.finished_at = time.time()
        j.procs.clear()
        _local.job = prev
        _prune()


def _prune() -> None:
    with _lock:
        done = sorted((x for x in _jobs.values() if x.finished_at), key=lambda x: x.finished_at)
        for x in done[:-KEEP_FINISHED]:
            _jobs.pop(x.id, None)


def run(cmd: list[str], timeout: int = 3600, text: bool = True) -> subprocess.CompletedProcess:
    """subprocess.run that Stop can kill. Output goes through temp files so a
    long ffmpeg log can never fill a pipe and hang the process."""
    j = current()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdout=out, stderr=err)
        if j is not None:
            j.procs.add(proc)
        deadline = time.time() + timeout
        try:
            while True:
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if j is not None and j.stop.is_set():
                        proc.kill()
                        proc.wait()
                        raise Cancelled(f"{j.label} stopped")
                    if time.time() > deadline:
                        proc.kill()
                        proc.wait()
                        raise subprocess.TimeoutExpired(cmd, timeout)
        finally:
            if j is not None:
                j.procs.discard(proc)
        if j is not None and j.stop.is_set():
            # Stop killed the process (or landed as it exited): report a stop,
            # never a half-finished result.
            raise Cancelled(f"{j.label} stopped")
        out.seek(0)
        err.seek(0)
        o, e = out.read(), err.read()
        if text:
            o, e = o.decode(errors="replace"), e.decode(errors="replace")
        return subprocess.CompletedProcess(cmd, proc.returncode, o, e)


def track(proc: subprocess.Popen) -> None:
    """Register a Popen started elsewhere so Stop can kill it."""
    j = current()
    if j is not None:
        j.procs.add(proc)


def request_stop(job_id: str) -> Job:
    with _lock:
        j = _jobs.get(job_id)
    if j is None:
        raise KeyError(job_id)
    if j.finished_at:
        return j
    j.stop.set()
    j.status = "stopping"
    j.stop_requested_at = time.time()
    for p in list(j.procs):
        with contextlib.suppress(Exception):
            p.kill()
    return j


def _sweep() -> None:
    """Retire jobs whose worker never acknowledged Stop (dead thread)."""
    now = time.time()
    for j in list(_jobs.values()):
        if j.status == "stopping" and not j.finished_at and j.stop_requested_at \
                and now - j.stop_requested_at > STOP_GRACE_SECONDS and now - j.heartbeat > STOP_GRACE_SECONDS:
            j.status, j.message, j.finished_at = "cancelled", "stopped (worker did not respond; retired)", now


def list_jobs() -> list[dict[str, Any]]:
    _sweep()
    with _lock:
        jobs = list(_jobs.values())
    jobs.sort(key=lambda x: (x.finished_at is not None, -(x.finished_at or x.started_at)))
    return [x.public() for x in jobs]


def running(scope: str | None = None, ref: Any = None) -> list[Job]:
    return [j for j in _jobs.values() if not j.finished_at and (scope is None or j.scope == scope)
            and (ref is None or j.ref == ref)]


def start_thread(kind: str, label: str, scope: str, fn, *args, ref: Any = None, **kwargs) -> str:
    """Run fn in a background thread as a stoppable job; returns the job id.
    fn's return value becomes job.result."""
    ready = threading.Event()
    holder: dict[str, str] = {}

    def work() -> None:
        try:
            with job(kind, label, scope, ref) as j:
                holder["id"] = j.id
                ready.set()
                j.result = fn(*args, **kwargs)
        except Cancelled:
            pass
        except Exception:
            pass  # recorded on the job (status=error, message)
        finally:
            ready.set()

    threading.Thread(target=work, daemon=True, name=f"job-{kind}").start()
    ready.wait(5)
    return holder.get("id", "")
