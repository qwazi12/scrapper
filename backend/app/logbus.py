"""Observability: structured logging that fans out to the DB, a rotating file,
and an in-memory pub/sub queue the API exposes over SSE for the live Logs panel.

Also keeps a rolling per-source success/failure tally so we can fire an alert
webhook when a platform (e.g. X) starts failing — you find out before a video
goes missing.
"""

from __future__ import annotations

import collections
import datetime
import json
import logging
import queue
import threading

import httpx

from .config import settings
from .db import SessionLocal
from .models import LogEntry

# --- file logger -------------------------------------------------------------
_LOG_FILE = settings.data_path / "scrapper.log"
_file_logger = logging.getLogger("scrapper")
if not _file_logger.handlers:
    _file_logger.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(_LOG_FILE)
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _file_logger.addHandler(_fh)
    _sh = logging.StreamHandler()
    _sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    _file_logger.addHandler(_sh)

# --- live SSE subscribers ----------------------------------------------------
_subscribers: set[queue.Queue] = set()
_sub_lock = threading.Lock()

# --- rolling per-source outcomes (for failure-spike alerts) ------------------
_recent: dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=20))
_alerted: set[str] = set()


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=100)
    with _sub_lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _sub_lock:
        _subscribers.discard(q)


def _fanout(payload: dict) -> None:
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.discard(q)


def log(level: str, event: str, message: str, **context) -> None:
    """Emit a log line to file + DB + live subscribers."""
    getattr(_file_logger, level if level in ("debug", "info", "warning", "error") else "info")(
        "[%s] %s %s", event, message, context or ""
    )
    ts = datetime.datetime.now(datetime.timezone.utc)
    try:
        with SessionLocal() as s:
            s.add(LogEntry(level=level, event=event, message=message, context=context or None))
            s.commit()
    except Exception as exc:  # never let logging crash a job
        _file_logger.error("logbus DB write failed: %s", exc)
    _fanout({
        "level": level,
        "event": event,
        "message": message,
        "context": context,
        "created_at": ts.isoformat(),
    })


def record_outcome(source: str, ok: bool) -> None:
    """Track a per-source outcome and fire the alert webhook on a failure spike."""
    dq = _recent[source]
    dq.append(1 if ok else 0)
    if len(dq) >= 5:
        fail_rate = 1 - (sum(dq) / len(dq))
        if fail_rate >= settings.alert_failure_threshold and source not in _alerted:
            _alerted.add(source)
            _fire_alert(source, fail_rate, len(dq))
        elif fail_rate < settings.alert_failure_threshold:
            _alerted.discard(source)


def source_stats() -> list[dict]:
    """Per-source success-rate snapshot for the observability panel."""
    out = []
    for src, dq in _recent.items():
        n = len(dq)
        out.append({
            "source": src,
            "recent": n,
            "success_rate": round(sum(dq) / n, 3) if n else None,
            "alerting": src in _alerted,
        })
    return sorted(out, key=lambda r: r["source"])


def _fire_alert(source: str, fail_rate: float, sample: int) -> None:
    msg = f"{source}: {fail_rate:.0%} of last {sample} scrapes failing"
    log("warning", "alert", msg, source=source, fail_rate=fail_rate)
    if not settings.alert_webhook_url:
        return
    try:
        httpx.post(
            settings.alert_webhook_url,
            json={"text": f":rotating_light: scrapper alert — {msg}"},
            timeout=10,
        )
    except Exception as exc:
        _file_logger.error("alert webhook failed: %s", exc)
