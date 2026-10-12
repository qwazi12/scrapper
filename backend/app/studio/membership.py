"""Which Studio projects belong to Countdowns, and which LongForm Studio shows.

Audit 2026-10-11: the countdown tag lived only in `facts`, and the gather step
rewrites `facts` — so a countdown's segment projects (e.g. #45–59) lost it and
vanished from BOTH studios. Membership now also comes from the Countdown
queue rows themselves (research.project_ids), which nothing rewrites.
"""

from __future__ import annotations

import datetime
from typing import Any

COUNTDOWN_SOURCE = "Countdown Studio"
COUNTDOWN_KEYS = ("is_countdown", "origin", "countdown_topic", "countdown_id")
RECENT_DAYS = 15


def countdown_ids(s) -> set[int]:
    """Project ids linked to any Countdown Studio queue row."""
    from ..models import QueueItem
    ids: set[int] = set()
    for (research,) in s.query(QueueItem.research).filter(QueueItem.source == COUNTDOWN_SOURCE).all():
        for pid in (research or {}).get("project_ids") or []:
            try:
                ids.add(int(pid))
            except (TypeError, ValueError):
                pass
    return ids


def is_countdown(p, ids: set[int]) -> bool:
    """A countdown segment — unless it is a real LongForm breakdown (made by the
    automation or sent to the Posting Queue on its own), which always stays in LongForm."""
    if p.queue_item_id or (p.review or {}).get("auto"):
        return False
    f = p.facts or {}
    return p.id in ids or bool(f.get("is_countdown")) or f.get("origin") == "countdown"


def keep_tags(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """Carry the countdown tag over when a stage rewrites `facts`."""
    for k in COUNTDOWN_KEYS:
        if (old or {}).get(k) is not None and k not in new:
            new[k] = old[k]
    return new


def _in_progress(p, now: datetime.datetime) -> bool:
    from ..models import QueueItem
    if p.stage_status in ("running", "queued", "paused"):
        return True
    created = p.created_at
    if created is not None:
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.timezone.utc)
        if now - created < datetime.timedelta(days=RECENT_DAYS):
            return True
    if p.render and not p.queue_item_id:
        return True                          # rendered, not sent yet
    if p.queue_item_id:
        from sqlalchemy.orm import object_session
        s = object_session(p)
        q = s.get(QueueItem, p.queue_item_id) if s else None
        if q is not None and q.status not in ("posted", "archived"):
            return True                      # still waiting to post
    return False


def shown_in_longform(p, ids: set[int], today: datetime.date | None = None,
                      now: datetime.datetime | None = None) -> bool:
    """LongForm Studio list rule. Owner rule (2026-10-10): breakdowns are titles
    from the current month forward and at most 15 days after release — old titles
    are hidden. Audit fix: never hide work still in progress (started in the last
    15 days, running, rendered but not sent, or waiting in the queue), so nothing
    disappears on the 1st of the month."""
    if is_countdown(p, ids):
        return False
    today = today or datetime.date.today()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    raw = (p.facts or {}).get("primary_date")
    try:
        d = datetime.date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        d = None
    if d is None:
        return True
    old = d < datetime.date(today.year, today.month, 1) or (d - today).days < -15
    return not old or _in_progress(p, now)
