"""Countdown membership + LongForm list rules (audit 2026-10-11)."""

import datetime

from backend.app.models import QueueItem, StudioProject
from backend.app.studio import auto, membership

UTC = datetime.timezone.utc
TODAY = datetime.date(2026, 10, 11)
NOW = datetime.datetime(2026, 10, 11, 12, tzinfo=UTC)


def _p(s, title, date=None, **kw):
    p = StudioProject(tmdb_id=1, media_type="movie", title=title,
                      facts={"primary_date": date} if date else {}, **kw)
    s.add(p)
    s.commit()
    return p


def test_countdown_membership_survives_lost_tag(session):
    seg = _p(session, "The Ring", "2002-10-18")           # tag lost when gather rewrote facts
    real = _p(session, "Wildwood", "2026-10-22")
    session.add(QueueItem(pipeline="LongForm", source="Countdown Studio", title="Top 15", status="review",
                          research={"project_ids": [seg.id]}))
    session.commit()
    ids = membership.countdown_ids(session)
    assert membership.is_countdown(seg, ids) and not membership.is_countdown(real, ids)
    assert not membership.shown_in_longform(seg, ids, TODAY, NOW)
    assert membership.shown_in_longform(real, ids, TODAY, NOW)


def test_old_titles_hidden_unless_in_progress(session):
    old_idle = _p(session, "Old", "2026-02-23", created_at=NOW - datetime.timedelta(days=40))
    old_running = _p(session, "Running", "2026-02-23", stage_status="running",
                     created_at=NOW - datetime.timedelta(days=40))
    old_recent = _p(session, "Started this week", "2026-09-20", created_at=NOW - datetime.timedelta(days=3))
    old_rendered = _p(session, "Rendered, not sent", "2026-09-20", render={"file": "x.mp4"},
                      created_at=NOW - datetime.timedelta(days=40))
    show = {p.title: membership.shown_in_longform(p, set(), TODAY, NOW)
            for p in (old_idle, old_running, old_recent, old_rendered)}
    assert show == {"Old": False, "Running": True, "Started this week": True, "Rendered, not sent": True}


def test_countdown_segments_do_not_use_the_daily_limit(session):
    before = auto.made_today(session)["movie"]
    seg = _p(session, "Theory")
    seg.facts = {"is_countdown": True, "origin": "countdown"}
    _p(session, "Real breakdown")
    session.commit()
    assert auto.made_today(session)["movie"] == before + 1


def test_keep_tags_carries_countdown_tag():
    out = membership.keep_tags({"is_countdown": True, "origin": "countdown", "title": "a"}, {"title": "b"})
    assert out["is_countdown"] is True and out["origin"] == "countdown" and out["title"] == "b"
