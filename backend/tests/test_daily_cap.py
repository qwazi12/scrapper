"""Daily spend cap: refuses paid calls once today's spend hits it; jobs pause
(budget_paused) instead of failing and resume first when there's budget again."""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.app import costs, resume
from backend.app.db import SessionLocal
from backend.app.models import AppSetting, ResumableJob, StudioProject, UsageEvent


@pytest.fixture(autouse=True)
def clean():
    with SessionLocal() as s:
        s.query(UsageEvent).delete()
        s.query(ResumableJob).delete()
        row = s.get(AppSetting, "costs")
        if row:
            s.delete(row)
        s.commit()
    yield


@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


def _spend(usd: float, when: dt.datetime | None = None):
    with SessionLocal() as s:
        s.add(UsageEvent(service="gemini", operation="test", cost_usd=usd, month=costs.month_key(),
                         created_at=when or dt.datetime.now(dt.timezone.utc)))
        s.commit()


def test_default_cap_is_6_and_blocks_once_reached():
    assert costs.settings_dict()["daily_usd"] == 6.0
    _spend(5.99)
    costs.check_budget()
    _spend(0.01)
    with pytest.raises(costs.DailyCapReached, match=r"\$6.00 of \$6.00"):
        costs.check_budget()


def test_yesterdays_spend_does_not_count():
    start, _ = costs.day_bounds()
    _spend(50, start - dt.timedelta(minutes=1))
    costs.check_budget()
    assert costs.daily_status()["spent_usd"] == 0


def test_cap_is_editable_and_zero_turns_it_off(client):
    _spend(7)
    with pytest.raises(costs.DailyCapReached):
        costs.check_budget()
    d = client.put("/api/costs/settings", json={"daily_usd": 10}).json()
    assert d["today"]["cap_usd"] == 10 and not d["today"]["reached"]
    costs.check_budget()
    client.put("/api/costs/settings", json={"daily_usd": 0})
    _spend(100)
    costs.check_budget()
    assert client.put("/api/costs/settings", json={"daily_usd": -1}).status_code == 400


def test_gemini_does_not_wrap_the_cap_so_steps_cannot_swallow_it(monkeypatch):
    from backend.app.studio import gemini
    monkeypatch.setattr(gemini.settings, "gemini_api_key", "mock-key")
    _spend(6)
    with pytest.raises(costs.DailyCapReached):
        gemini._post({"contents": []})


def test_studio_step_pauses_on_cap_then_resumes_from_that_step(monkeypatch):
    from backend.app.studio import runner
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=99, title="Cap Test")
        s.add(p)
        s.commit()
        pid = p.id

    def capped(project_id):
        raise costs.DailyCapReached("Daily spend cap reached")

    monkeypatch.setitem(runner.STAGES, "shots", capped)
    monkeypatch.setitem(runner.STAGES, "gather", lambda project_id: "ok")
    monkeypatch.setitem(runner.STAGES, "trailer", lambda project_id: "ok")
    runner.start(pid, "gather", auto=True)
    for _ in range(100):
        with SessionLocal() as s:
            row = s.query(ResumableJob).filter(ResumableJob.kind == "studio").first()
            if row and row.status != "running":
                break
        import time
        time.sleep(0.05)
    with SessionLocal() as s:
        row = s.query(ResumableJob).filter(ResumableJob.kind == "studio").one()
        assert row.status == "budget_paused" and row.checkpoint["stage"] == "shots"
        p = s.get(StudioProject, pid)
        assert p.stage_status == "paused" and "daily spend cap" in p.stage_message

    # Still capped: nothing resumes. Budget back: it resumes from "shots".
    calls = []
    monkeypatch.setitem(resume._launchers, "studio", lambda params, cp, rid: calls.append(cp["stage"]))
    _spend(6)
    assert resume.resume_budget_paused() == 0
    with SessionLocal() as s:
        s.query(UsageEvent).delete()
        s.commit()
    assert resume.resume_budget_paused() == 1
    assert calls == ["shots"]


def test_busy_launcher_keeps_the_order(monkeypatch):
    a = resume.begin("studio", "first", {"project_id": 1, "stage": "script"})
    b = resume.begin("studio", "second", {"project_id": 2, "stage": "gather"})
    resume.finish(a, "budget_paused")
    resume.finish(b, "budget_paused")

    def busy(params, cp, rid):
        raise RuntimeError("Studio is busy")

    monkeypatch.setitem(resume._launchers, "studio", busy)
    assert resume.resume_budget_paused() == 0
    assert [r.label for r in resume.budget_paused()] == ["first", "second"]


def test_summary_lists_paused_jobs():
    r = resume.begin("bulk_ai", "AI rewrite of 3 video(s)", {"ids": [1, 2, 3]})
    resume.finish(r, "budget_paused")
    t = costs.summary()["today"]
    assert t["paused_jobs"][0]["label"] == "AI rewrite of 3 video(s)"
