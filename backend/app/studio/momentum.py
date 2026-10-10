"""Release Momentum System Tracker.

Ensures movie and TV breakdown videos are scheduled and posted BEFORE release dates
to capture and ride pre-release search momentum.

Windows:
- 🚨 Critical / Urgent: 0–3 days before release (peak pre-release buzz)
- ⚡ Approaching / Prime: 4–14 days before release (ideal trailer breakdown window)
- 🗓️ Upcoming: >14 days before release
- ⚠️ Released / Missed: <0 days (title already opened; pre-release window closed)
"""

from __future__ import annotations

import datetime
from typing import Any

UTC = datetime.timezone.utc


def parse_date(val: Any) -> datetime.date | None:
    """Parse date from string (YYYY-MM-DD or ISO) or datetime object."""
    if not val:
        return None
    if isinstance(val, datetime.date) and not isinstance(val, datetime.datetime):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        # Try YYYY-MM-DD
        try:
            return datetime.date.fromisoformat(val[:10])
        except (ValueError, IndexError):
            pass
    return None


def parse_datetime(val: Any) -> datetime.datetime | None:
    """Parse datetime from ISO string or datetime object."""
    if not val:
        return None
    if isinstance(val, datetime.datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC)
        return val
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            cleaned = val.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            pass
    return None


def extract_release_date(item_or_project: Any, session: Any = None) -> datetime.date | None:
    """Extract release date from a StudioProject or QueueItem."""
    if item_or_project is None:
        return None

    # 1. StudioProject
    if hasattr(item_or_project, "facts") and isinstance(item_or_project.facts, dict):
        facts = item_or_project.facts
        d = parse_date(facts.get("primary_date"))
        if d:
            return d
        # Check releases array for US release
        for rel in facts.get("releases", []):
            if isinstance(rel, dict) and rel.get("date"):
                d = parse_date(rel.get("date"))
                if d:
                    return d

    # 2. QueueItem
    if hasattr(item_or_project, "research") and isinstance(item_or_project.research, dict):
        res = item_or_project.research
        d = parse_date(res.get("release_date") or res.get("primary_date"))
        if d:
            return d

    # 3. Lookup linked StudioProject if QueueItem has no research date but was created from Studio
    if session and hasattr(item_or_project, "id") and getattr(item_or_project, "source", None) == "LongForm Studio":
        try:
            from ..models import StudioProject
            p = session.query(StudioProject).filter(StudioProject.queue_item_id == item_or_project.id).first()
            if p and p.facts:
                return parse_date(p.facts.get("primary_date"))
        except Exception:
            pass

    return None


def compute_momentum(
    release_date: datetime.date | str | None,
    scheduled_at: datetime.datetime | str | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Calculate release momentum metrics for a movie or TV show.

    Returns:
        dict with release_date, days_until_release, status, urgency,
        is_pre_release, is_post_release, scheduled_after_release,
        badge_text, badge_variant, warning, priority_score.
    """
    rel = parse_date(release_date)
    today = today or datetime.date.today()
    sched = parse_datetime(scheduled_at)

    if not rel:
        return {
            "has_release_date": False,
            "release_date": None,
            "days_until_release": None,
            "status": "unknown",
            "urgency": "none",
            "is_pre_release": False,
            "is_post_release": False,
            "scheduled_after_release": False,
            "badge_text": "Date TBA",
            "badge_variant": "neutral",
            "warning": None,
            "priority_score": 0,
        }

    days = (rel - today).days
    rel_iso = rel.isoformat()

    sched_date = sched.date() if sched else None
    scheduled_after = (sched_date > rel) if sched_date else False

    # Status & Urgency categorization
    if days < 0:
        status = "released"
        urgency = "missed"
        badge_variant = "missed"
        badge_text = f"⚠️ Released {abs(days)}d ago (Post-Release)"
        priority_score = -100 - min(abs(days), 500)
    elif days == 0:
        status = "critical"
        urgency = "urgent"
        badge_variant = "critical"
        badge_text = "🚨 Releases TODAY (Peak Momentum)"
        priority_score = 1000
    elif 1 <= days <= 3:
        status = "critical"
        urgency = "urgent"
        badge_variant = "critical"
        badge_text = f"🚨 Releases in {days}d (URGENT)"
        priority_score = 1000 - days
    elif 4 <= days <= 14:
        status = "approaching"
        urgency = "optimal"
        badge_variant = "approaching"
        badge_text = f"⚡ Releases in {days}d (Prime Window)"
        priority_score = 800 - days
    else:  # days > 14
        status = "upcoming"
        urgency = "early"
        badge_variant = "upcoming"
        badge_text = f"🗓️ Releases in {days}d"
        priority_score = 500 - min(days, 300)

    # Generate helpful warnings for operator
    warning = None
    if scheduled_after:
        sched_str = sched_date.strftime("%b %d, %Y") if sched_date else ""
        rel_str = rel.strftime("%b %d, %Y")
        warning = (
            f"⚠️ Scheduled for {sched_str}, AFTER release date ({rel_str})! "
            f"Post before release to capture search momentum."
        )
    elif days < 0:
        warning = f"Title released on {rel.strftime('%b %d, %Y')} ({abs(days)} days ago). Pre-release window has closed."

    return {
        "has_release_date": True,
        "release_date": rel_iso,
        "days_until_release": days,
        "status": status,
        "urgency": urgency,
        "is_pre_release": days >= 0,
        "is_post_release": days < 0,
        "scheduled_after_release": scheduled_after,
        "badge_text": badge_text,
        "badge_variant": badge_variant,
        "warning": warning,
        "priority_score": priority_score,
    }
