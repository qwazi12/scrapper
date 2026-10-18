"""Pending dependencies and GET endpoints cannot silently start paid work."""
import time

from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import AppSetting, Clip, QueueItem, ResumableJob, StudioProject
from backend.app.studio import runner, stage_trailer


def test_auto_chain_stops_at_worker_handoff_then_collects_same_clip(monkeypatch, tmp_path):
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=88001, title="Worker handoff", facts={"videos": [
            {"site": "YouTube", "type": "Trailer", "url": "https://youtube.com/watch?v=synthetic"}]})
        s.add(p); s.commit(); pid = p.id
    downstream = []
    monkeypatch.setitem(runner.STAGES, "shots", lambda pid: downstream.append(pid))
    monkeypatch.setattr(runner, "start_next_waiting", lambda: True)
    runner.start(pid, "trailer", auto=True, until="shots")
    deadline = time.monotonic() + 5
    while runner._lock.locked() and time.monotonic() < deadline:
        time.sleep(.02)
    assert not runner._lock.locked()
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        cid = p.trailer["pending_clip_id"]
        assert p.stage == "trailer" and p.stage_status == "awaiting_worker"
        job = s.query(ResumableJob).filter(ResumableJob.label.like(f"Studio #{pid}:%")).order_by(ResumableJob.id.desc()).first()
        assert job.status == "awaiting_worker" and downstream == []
        count = s.query(Clip).count()
    # An offline worker never creates another queue clip or runs vision.
    import pytest
    with pytest.raises(runner.AwaitingWorker):
        runner.run_one(pid, "trailer")
    with SessionLocal() as s:
        assert s.query(Clip).count() == count
        path = tmp_path / "worker-upload.mp4"; path.write_bytes(b"transport fixture")
        s.get(Clip, cid).file_path = str(path); s.commit()
    assert "received" in runner.run_one(pid, "trailer")
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.stage_status == "done" and p.trailer["file"] == str(path)


def test_get_topics_and_schedule_never_discover_or_plan(session, monkeypatch):
    from backend.app.studio.compilations import engine
    from backend.app.social import queue_manager
    monkeypatch.setattr(engine, "discover_topics", lambda **kw: (_ for _ in ()).throw(AssertionError("paid discovery")))
    monkeypatch.setattr(queue_manager, "plan_schedule", lambda *a: (_ for _ in ()).throw(AssertionError("schedule mutation")))
    row = session.get(AppSetting, "countdown_candidates")
    if row: session.delete(row)
    item = QueueItem(title="Unscheduled", status="ready", accounts=["default:youtube"])
    session.add(item); session.commit()
    client = TestClient(app, headers={"x-access-token": "test-token"})
    for _ in range(2):
        assert client.get("/api/studio/compilations/topics").json() == []
        assert client.get("/api/studio/compilations/topics?refresh=true").status_code == 405
        assert client.get("/api/schedule").status_code == 200
    session.refresh(item)
    assert item.scheduled_at is None and session.get(AppSetting, "countdown_candidates") is None


def test_explicit_topic_refresh_populates_cache(session, monkeypatch):
    from backend.app.studio.compilations import engine
    calls = []
    def discover(**kw):
        calls.append(kw)
        return [{"id": "synthetic", "trigger": "evergreen"}]
    monkeypatch.setattr(engine, "discover_topics", discover)
    client = TestClient(app, headers={"x-access-token": "test-token"})
    assert client.post("/api/studio/compilations/topics/refresh").status_code == 200
    assert client.get("/api/studio/compilations/topics?trigger=evergreen").json()[0]["id"] == "synthetic"
    assert client.get("/api/studio/compilations/topics?trigger=trending").json() == []
    assert len(calls) == 1
