"""Tests for Release Momentum System Tracker and Default Poster Thumbnail."""

import datetime
import pytest

from backend.app.studio import momentum, stage_render
from backend.app.models import QueueItem, StudioProject
from backend.app.social import queue_manager

UTC = datetime.timezone.utc


def test_compute_momentum_pre_release_windows():
    today = datetime.date(2026, 10, 10)

    # 1. Critical window (today or <= 3 days)
    m_today = momentum.compute_momentum("2026-10-10", today=today)
    assert m_today["status"] == "critical"
    assert m_today["days_until_release"] == 0
    assert m_today["urgency"] == "urgent"
    assert m_today["is_pre_release"] is True
    assert "TODAY" in m_today["badge_text"]

    m_crit = momentum.compute_momentum("2026-10-12", today=today)
    assert m_crit["status"] == "critical"
    assert m_crit["days_until_release"] == 2
    assert "2d" in m_crit["badge_text"]

    # 2. Approaching prime window (4–14 days)
    m_prime = momentum.compute_momentum("2026-10-18", today=today)
    assert m_prime["status"] == "approaching"
    assert m_prime["days_until_release"] == 8
    assert m_prime["urgency"] == "optimal"
    assert "Prime Window" in m_prime["badge_text"]

    # 3. Upcoming (> 14 days)
    m_up = momentum.compute_momentum("2026-11-20", today=today)
    assert m_up["status"] == "upcoming"
    assert m_up["days_until_release"] == 41

    # 4. Missed / Released (< 0 days)
    m_past = momentum.compute_momentum("2026-10-05", today=today)
    assert m_past["status"] == "released"
    assert m_past["days_until_release"] == -5
    assert m_past["is_post_release"] is True
    assert m_past["urgency"] == "missed"
    assert "Post-Release" in m_past["badge_text"]


def test_compute_momentum_scheduled_after_release_warning():
    today = datetime.date(2026, 10, 10)
    rel_date = "2026-10-15"

    # Scheduled before release -> no warning
    m_good = momentum.compute_momentum(
        rel_date,
        scheduled_at="2026-10-13T14:00:00Z",
        today=today,
    )
    assert m_good["scheduled_after_release"] is False

    # Scheduled after release -> triggers warning
    m_late = momentum.compute_momentum(
        rel_date,
        scheduled_at="2026-10-18T14:00:00Z",
        today=today,
    )
    assert m_late["scheduled_after_release"] is True
    assert m_late["warning"] is not None
    assert "AFTER release date" in m_late["warning"]


def test_extract_release_date():
    # From StudioProject
    p = StudioProject(
        tmdb_id=100,
        media_type="movie",
        title="Avatar 3",
        facts={"primary_date": "2026-12-18", "releases": [{"country": "US", "date": "2026-12-18"}]},
    )
    assert momentum.extract_release_date(p) == datetime.date(2026, 12, 18)

    # From QueueItem
    q = QueueItem(
        pipeline="LongForm",
        title="Avatar 3 Breakdown",
        research={"release_date": "2026-12-18", "primary_date": "2026-12-18"},
    )
    assert momentum.extract_release_date(q) == datetime.date(2026, 12, 18)


def test_default_thumbnail_selection_defaults_to_poster(session, tmp_path, monkeypatch):
    p = StudioProject(tmdb_id=555, title="Thumbnail Default Test", render={"file": "render/final.mp4"})
    session.add(p)
    session.commit()

    monkeypatch.setattr(stage_render, "project_dir", lambda pid: tmp_path)
    rdir = tmp_path / "render"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "thumbnail_poster.jpg").write_bytes(b"poster_data")
    (rdir / "thumbnail_shot1.jpg").write_bytes(b"shot1_data")
    (rdir / "thumbnail_shot2.jpg").write_bytes(b"shot2_data")

    # When no explicit selection is saved, it defaults to poster
    res = stage_render.select_project_thumbnail(p.id, "poster")
    assert res["selected_thumbnail"] == "poster"
    assert (rdir / "thumbnail.jpg").read_bytes() == b"poster_data"


def test_queue_scheduler_prioritizes_upcoming_releases_before_release(session):
    now = datetime.datetime(2026, 10, 10, 8, 0, tzinfo=UTC)

    # Item A: releases in 20 days
    item_a = QueueItem(
        pipeline="LongForm",
        title="Movie Far Ahead",
        accounts=["default:*"],
        status="ready",
        position=1,
        research={"release_date": "2026-10-30"},
    )
    # Item B: releases in 2 days (urgent pre-release!)
    item_b = QueueItem(
        pipeline="LongForm",
        title="Movie Releasing Soon",
        accounts=["default:*"],
        status="ready",
        position=5,
        research={"release_date": "2026-10-12"},
    )
    # Item C: already released 10 days ago (past)
    item_c = QueueItem(
        pipeline="LongForm",
        title="Movie Already Out",
        accounts=["default:*"],
        status="ready",
        position=2,
        research={"release_date": "2026-09-30"},
    )

    session.add_all([item_a, item_b, item_c])
    session.commit()

    # Plan schedule
    queue_manager.plan_schedule(session, now)
    session.refresh(item_a)
    session.refresh(item_b)
    session.refresh(item_c)

    # Item B (releasing in 2 days) MUST have the earliest scheduled_at slot!
    assert item_b.scheduled_at is not None
    assert item_a.scheduled_at is not None
    assert item_b.scheduled_at < item_a.scheduled_at
    # Item B is scheduled BEFORE its release date
    assert item_b.scheduled_at.date() <= datetime.date(2026, 10, 12)
