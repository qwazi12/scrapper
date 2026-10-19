"""2026-10-19: videos queued before the 0e5e500 release post as approved then; newer ones need a binding;
a refused scheduled post is shown on the row, not only in server logs."""
import datetime

import pytest
from fastapi import HTTPException

from backend.app import queue_sources
from backend.app.models import CountdownProject, QueueItem

UTC = datetime.timezone.utc


def _row(session, created):
    item = QueueItem(title="Countdown", source=next(iter(queue_sources.SOURCES)), accounts=["Cinama:*"],
                     status="ready", video_path="/tmp/x.mp4", created_at=created)
    session.add(item); session.commit()
    return item


def test_row_queued_before_the_release_is_allowed(session):
    item = _row(session, datetime.datetime(2026, 10, 18, 0, 55, tzinfo=UTC))
    assert queue_sources.required(session, item) is None


def test_row_queued_after_the_release_still_needs_a_reviewed_binding(session):
    item = _row(session, datetime.datetime(2026, 10, 19, 12, 0, tzinfo=UTC))
    with pytest.raises(HTTPException, match="unbound legacy row"):
        queue_sources.required(session, item)


def test_refused_scheduled_post_is_shown_on_the_row(session, monkeypatch):
    from backend.app.social import queue_manager as qm, upload_post
    from backend.app.models import LogEntry
    now = datetime.datetime(2026, 10, 20, 16, 5, tzinfo=UTC)
    item = QueueItem(title="Refused", accounts=["Cinama:*"], status="ready", video_path="/tmp/x.mp4",
                     scheduled_at=now - datetime.timedelta(minutes=5), position=1)
    session.add(item); session.commit()

    async def refuse(item_id, s, regular=False):
        raise upload_post.UploadPostError("Studio source: unbound legacy row", 409)
    monkeypatch.setattr(qm, "publish_queue_item", refuse)
    monkeypatch.setattr(qm, "plan_schedule", lambda s, n: 0)
    monkeypatch.setattr(qm, "reconcile_posting", lambda s, n: 0)
    monkeypatch.setattr(qm, "fill_destinations", lambda s: 0)
    monkeypatch.setattr(qm, "autopost_paused", lambda s: False)
    real = qm.datetime.datetime

    class FakeDT(real):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(qm.datetime, "datetime", FakeDT)
    qm.run_scheduler_tick()
    session.refresh(item)
    assert item.notes == "Not posted at its slot: Studio source: unbound legacy row"
    assert session.query(LogEntry).filter(LogEntry.event == "queue_post_refused").count() == 1
