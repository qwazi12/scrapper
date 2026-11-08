"""LongForm freshness (owner, 2026-11-08): a voiceover that frames a past release date as upcoming is held at send
time and listed for NEEDS OWNER; past dates in the past tense, and dates still ahead, pass."""
import asyncio
import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models import QueueItem, StudioProject
from backend.app.social import queue_manager as qm, upload_post as up
from backend.app.studio import freshness

NOV8 = datetime.date(2026, 11, 8)


@pytest.mark.parametrize("words", [
    "Now, the wait for the next chapter is officially on, with Season 2 Episode 1 scheduled to release in the United "
    "States on October 20, 2026 on Netflix.",                                              # #4830 S7
    "New episodes begin streaming worldwide on Wednesday, September 16, 2026.",            # #4829 S7
    "The next exciting chapter, Season 3 Episode 4, officially lands on October 21, 2026.",  # #4824 S5
    "Fans can mark their calendars because Season 4 officially premieres on Apple TV+ on August 5, 2026.",  # #4840 S4
    "You will not have to wait long for more excitement, as episode two, titled Blind Rooster, arrives on October 23, 2026.",  # #4819 S7
    "The finale airs on October 21.",                                                      # no year: the post's year
])
def test_past_date_framed_as_upcoming_is_stale(words):
    assert freshness.stale_sentences([{"text": words, "i": 7}], NOV8)


@pytest.mark.parametrize("words", [
    "Fans can watch season four exclusively on Paramount+, which officially premiered on Friday, October 16, 2026.",
    "Originally premiering back in April 2022, the acclaimed drama has captured audiences across the globe.",
    "Season 3 arrives on December 4, 2026.",                                               # still ahead
    "Season 2 begins streaming on November 8, 2026.",                                      # today
])
def test_past_tense_or_future_dates_pass(words):
    assert freshness.stale_sentences([{"text": words}], NOV8) == []


def _longform(session, words, when):
    it = QueueItem(pipeline="LongForm", source="LongForm Studio", title="t", status="ready", accounts=["default:youtube"],
                   video_path="video.mp4", scheduled_at=when)
    session.add(it)
    session.commit()
    proj = StudioProject(tmdb_id=99001, title="t", queue_item_id=it.id, script={"sentences": [{"i": 1, "text": words}]})
    session.add(proj)
    session.commit()
    return it, proj


def test_stale_longform_row_is_held_at_send_and_listed(session):
    soon = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    stale, p1 = _longform(session, "Season 2 Episode 1 is scheduled to release on October 20, 2020.", soon)
    fresh, p2 = _longform(session, "Season 1 premiered on October 20, 2020.", soon)
    try:
        with pytest.raises(up.PayloadBlocked, match="Freshness hold.*S1 \\(Oct 20, 2020\\)"):
            asyncio.run(qm._publish_queue_item_owned(stale.id, session))
        session.refresh(stale)
        assert stale.status == "ready" and not stale.publish_requests      # held where it is; nothing sent
        held = TestClient(app, headers={"x-access-token": "test-token"}).get("/api/ops/longform-freshness").json()["held"]
        assert [h["queue_id"] for h in held] == [stale.id]
        assert freshness.for_item(session, fresh) == []
    finally:
        session.delete(p1)
        session.delete(p2)
        session.commit()
