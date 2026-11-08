"""Posting gate (owner, 2026-11-07): with POSTING_GATE=sheet (the default) a due row posts only when the Posting Review
sheet lists it with Decision Approve, or with the Decision still blank 2 h before its slot. Skip / Edit / not in the
sheet / sheet unreadable: the row waits where it is (status unchanged). POSTING_GATE=off restores the old behaviour."""
from __future__ import annotations

import datetime
import os
import threading
import time

BLANK_AFTER = datetime.timedelta(hours=2)
CACHE_S = 240
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "rows": None, "error": None}


def on() -> bool:
    return os.environ.get("POSTING_GATE", "sheet").strip().lower() != "off"


def sheet_rows() -> dict[str, dict]:
    """{queue id: sheet row}, read at most every 4 minutes. Raises when the sheet can't be read."""
    with _lock:
        if _cache["rows"] is None or time.monotonic() - _cache["at"] > CACHE_S:
            from . import posting_review
            try:
                tabs = posting_review.read()
                _cache.update(rows={r.get("Queue ID", "").strip(): r for rows in tabs.values() for r in rows
                                    if r.get("Queue ID", "").strip()}, error=None)
            except Exception as exc:  # noqa: BLE001 — fail closed below
                _cache.update(rows=None, error=str(exc)[:200])
            _cache["at"] = time.monotonic()
        if _cache["rows"] is None:
            raise RuntimeError(f"Posting Review sheet unreadable: {_cache['error']}")
        return _cache["rows"]


def check(item_id: int, slot: datetime.datetime, now: datetime.datetime) -> str | None:
    """None = may post; otherwise why the row waits."""
    if not on():
        return None
    try:
        row = sheet_rows().get(str(item_id))
    except RuntimeError as exc:
        return str(exc)
    if row is None:
        return "not in the Posting Review sheet"
    decision = row.get("Decision", "").strip().lower()
    if decision == "approve":
        return None
    if not decision:
        return None if now >= slot - BLANK_AFTER else "Decision blank and the slot is more than 2 h away"
    return f"Posting Review Decision is {row.get('Decision', '').strip()!r}"
