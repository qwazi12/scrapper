"""Posting gate (owner, 2026-11-07): with POSTING_GATE=sheet (the default) a due row posts only when the Posting Review
sheet lists it with Decision Approve, or with the Decision still blank 2 h before its slot. Skip / Edit / not in the
sheet / sheet unreadable: the row waits where it is (status unchanged). POSTING_GATE=off restores the old behaviour.
The sheet read is posting_review.cached(): one Google read per 5 min, and a rate limit keeps the last good read."""
from __future__ import annotations

import datetime
import os

BLANK_AFTER = datetime.timedelta(hours=2)


def on() -> bool:
    return os.environ.get("POSTING_GATE", "sheet").strip().lower() != "off"


def sheet_rows() -> dict[str, dict]:
    """{queue id: sheet row}. Raises RuntimeError when the sheet can't be read (a rate limit isn't that)."""
    from . import posting_review
    tabs, _ = posting_review.cached()
    return {r.get("Queue ID", "").strip(): r for rows in tabs.values() for r in rows if r.get("Queue ID", "").strip()}


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
