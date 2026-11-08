"""LongForm freshness (owner, 2026-11-08): a voiceover that says a release date is still coming after it has passed
("Season 2 Episode 1 scheduled to release… October 20, 2026", posted in November) doesn't post. The row is held at
send time (status unchanged, a note says why) and GET /api/ops/longform-freshness lists upcoming ones for NEEDS OWNER.
A past date said in the past tense ("premiered on October 16, 2026") is fine."""
from __future__ import annotations

import datetime
import re
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..auth import require_token
from ..db import get_session

ET = ZoneInfo("America/New_York")
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
DATE = re.compile(r"\b(" + "|".join(m.capitalize() for m in MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?\b")
# Words that put a date ahead of the listener. Past-tense forms ("premiered", "arrived", "began") are not here.
UPCOMING = re.compile(r"\b(will|won't|arrives?|arriving|lands?|landing|premieres|premiering|debuts|debuting|begins?|"
                      r"beginning|starts|starting|airs|airing|drops|dropping|releases|releasing|for release|"
                      r"scheduled|slated|set to|coming|upcoming|returns on|mark (?:your|their) calendars?|"
                      r"look(?:ing)? forward|(?:have|has) to wait|wait for|until|get ready|countdown)\b", re.I)


def stale_sentences(sentences: list[dict], on: datetime.date) -> list[str]:
    """'S7: "…"' for each sentence that frames a date before `on` as still to come."""
    out = []
    for n, s in enumerate(sentences, 1):
        text = s.get("text") or ""
        if not UPCOMING.search(text):
            continue
        for m in DATE.finditer(text):
            year = int(m[3]) if m[3] else on.year
            try:
                when = datetime.date(year, MONTHS.index(m[1].lower()) + 1, int(m[2]))
            except ValueError:
                continue
            if when < on:
                out.append(f'S{s.get("i", n)} ({when:%b %-d, %Y}): "{text[:160]}"')
                break
    return out


def for_item(s: Session, item, at: datetime.datetime | None = None) -> list[str]:
    """Stale sentences in this LongForm row's voiceover script, judged on its post day (ET)."""
    from ..models import StudioProject
    proj = s.query(StudioProject).filter(StudioProject.queue_item_id == item.id).order_by(StudioProject.id.desc()).first()
    if not proj or not (proj.script or {}).get("sentences"):
        return []
    at = at or datetime.datetime.now(datetime.timezone.utc)
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    return stale_sentences(proj.script["sentences"], at.astimezone(ET).date())


router = APIRouter(prefix="/api/ops", dependencies=[Depends(require_token)])


@router.get("/longform-freshness")
def longform_freshness(days: int = 8, s: Session = Depends(get_session)) -> dict:
    """Ready/Review LongForm rows in the next `days` whose voiceover would be stale on their slot."""
    from ..models import QueueItem
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = (s.query(QueueItem).filter(QueueItem.pipeline == "LongForm", QueueItem.status.in_(["ready", "review"]),
                                      QueueItem.scheduled_at.isnot(None),
                                      QueueItem.scheduled_at <= now + datetime.timedelta(days=max(1, min(days, 31))))
            .order_by(QueueItem.scheduled_at).all())
    held = []
    for it in rows:
        at = it.scheduled_at if it.scheduled_at.tzinfo else it.scheduled_at.replace(tzinfo=datetime.timezone.utc)
        found = for_item(s, it, max(at, now))
        if found:
            held.append({"queue_id": it.id, "title": it.title, "status": it.status, "scheduled_at": at.isoformat(),
                         "stale": found})
    return {"checked": len(rows), "held": held}
