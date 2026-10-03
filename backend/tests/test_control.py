"""Stop button: job registry, killable subprocesses, queued jobs, Studio stages."""

import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import control
from backend.app.db import SessionLocal
from backend.app.models import Compilation, IngestJob, Status, StudioProject


@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


def test_stop_kills_a_running_subprocess_fast():
    result = {}

    def work():
        try:
            with control.job("test", "sleepy ffmpeg", scope="test"):
                control.run(["sleep", "30"])
        except control.Cancelled:
            result["cancelled"] = True

    t = threading.Thread(target=work)
    t.start()
    for _ in range(50):
        jobs = control.running(scope="test")
        if jobs and jobs[0].procs:
            break
        time.sleep(0.05)
    started = time.time()
    control.request_stop(jobs[0].id)
    t.join(5)
    assert result.get("cancelled") and time.time() - started < 3
    assert next(j for j in control.list_jobs() if j["id"] == jobs[0].id)["status"] == "cancelled"


def test_check_raises_between_steps():
    with pytest.raises(control.Cancelled):
        with control.job("test", "loop", scope="test") as j:
            j.stop.set()
            control.check()


def test_start_thread_returns_result():
    jid = control.start_thread("test", "adds", "test", lambda: 2 + 2)
    for _ in range(50):
        j = next(x for x in control.list_jobs() if x["id"] == jid)
        if j["status"] == "done":
            break
        time.sleep(0.02)
    assert j["result"] == 4


def test_stop_queued_scrape_and_compile_before_they_start(client):
    with SessionLocal() as s:
        ij = IngestJob(urls=["https://x.com/a"], status=Status.queued)
        cp = Compilation(clip_ids=[1], status=Status.queued)
        s.add_all([ij, cp])
        s.commit()
        iid, cid = ij.id, cp.id
    ids = [j["id"] for j in client.get("/api/jobs").json()]
    assert f"ingest:{iid}" in ids and f"compile:{cid}" in ids
    assert client.post(f"/api/jobs/ingest:{iid}/stop").json()["status"] == "cancelled"
    assert client.post(f"/api/jobs/compile:{cid}/stop").json()["status"] == "cancelled"
    with SessionLocal() as s:
        assert s.get(IngestJob, iid).status == Status.failed
        assert s.get(Compilation, cid).error == "stopped before it started"
    assert client.post(f"/api/jobs/ingest:{iid}/stop").status_code == 409


def test_studio_stage_stop_marks_stopped_not_error(monkeypatch):
    from backend.app.studio import runner

    def slow_stage(pid):
        for _ in range(100):
            control.check()
            time.sleep(0.05)
        return "finished"

    monkeypatch.setitem(runner.STAGES, "slow", slow_stage)
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=1, title="x")
        s.add(p)
        s.commit()
        pid = p.id
    t = threading.Thread(target=lambda: pytest.raises(control.Cancelled, runner.run_one, pid, "slow"))
    t.start()
    for _ in range(50):
        jobs = control.running(scope="studio", ref=pid)
        if jobs:
            break
        time.sleep(0.02)
    control.request_stop(jobs[0].id)
    t.join(5)
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.stage_status == "stopped" and "stopped by user" in p.stage_message


def test_autopost_pause_blocks_new_submissions(client, monkeypatch):
    import datetime
    from backend.app.models import QueueItem
    from backend.app.social import queue_manager as qm
    with SessionLocal() as s:
        it = QueueItem(title="t", status="ready", accounts=["default:*"],
                       scheduled_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1))
        s.add(it)
        s.commit()
    called = []

    async def fake_publish(item_id, s):
        called.append(item_id)

    monkeypatch.setattr(qm, "publish_queue_item", fake_publish)
    monkeypatch.setattr(qm, "plan_schedule", lambda s, now: 0)
    monkeypatch.setattr(qm, "sweep_archive", lambda s, now: 0)
    assert client.put("/api/autopost", json={"paused": True}).json() == {"paused": True}
    qm.run_scheduler_tick()
    assert called == []
    client.put("/api/autopost", json={"paused": False})
    qm.run_scheduler_tick()
    assert called
    with SessionLocal() as s:
        s.query(QueueItem).delete()
        s.commit()


def test_job_lookup_by_registry_id_and_by_ingest_number():
    import threading, time
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import control
    c = TestClient(app)
    hdr = {"Authorization": f"Bearer {__import__('backend.app.config', fromlist=['settings']).settings.access_token}"}
    started = threading.Event()

    def work():
        try:
            with control.job("test", "lookup test", "studio", 99):
                started.set()
                while not control.stopped():
                    time.sleep(0.05)
        except control.Cancelled:
            pass

    threading.Thread(target=work, daemon=True).start()
    started.wait(2)
    jid = next(j["id"] for j in control.list_jobs() if j["label"] == "lookup test")
    r = c.get(f"/api/jobs/{jid}", headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "running"     # was a 422 from the int-only route
    assert c.post(f"/api/jobs/{jid}/stop", headers=hdr).json()["status"] == "stopping"
    assert c.get("/api/jobs/999999", headers=hdr).status_code == 404     # numeric ids still mean ingest jobs


def test_job_context_raises_cancelled_if_stopped_during_execution():
    with pytest.raises(control.Cancelled):
        with control.job("test", "test stop on exit", scope="test") as j:
            j.stop.set()
            # Even if the inner block doesn't call control.check(), exiting raises Cancelled
    assert j.status == "cancelled"


def test_runner_stop_cancels_auto_chain(monkeypatch):
    from backend.app.studio import runner
    from backend.app.models import ResumableJob

    stages_executed = []

    def stage_a(pid):
        stages_executed.append("gather")
        for _ in range(50):
            control.check()
            time.sleep(0.05)
        return "gather done"

    def stage_b(pid):
        stages_executed.append("trailer")
        return "trailer done"

    monkeypatch.setitem(runner.STAGES, "gather", stage_a)
    monkeypatch.setitem(runner.STAGES, "trailer", stage_b)

    with SessionLocal() as s:
        p = StudioProject(tmdb_id=10, title="Auto Chain Test")
        s.add(p)
        s.commit()
        pid = p.id

    # Start auto chain: gather -> trailer
    runner.start(pid, "gather", auto=True)

    # Wait until gather starts
    for _ in range(50):
        with SessionLocal() as s:
            p = s.get(StudioProject, pid)
            if p.stage_status == "running":
                break
        time.sleep(0.05)

    # Now call runner.stop(pid)
    assert runner.stop(pid) is True

    # Wait for the background thread to finish
    time.sleep(0.5)

    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.stage_status == "stopped"
        assert "stopped by user" in p.stage_message.lower()
        # Verify ResumableJob is marked stopped
        r = s.query(ResumableJob).filter(ResumableJob.kind == "studio").order_by(ResumableJob.id.desc()).first()
        if r and (r.params or {}).get("project_id") == pid:
            assert r.status == "stopped"

    # trailer must NEVER have run!
    assert "trailer" not in stages_executed


def test_studio_project_stop_api_endpoint(client, monkeypatch):
    from backend.app.studio import runner

    def slow_stage(pid):
        while not control.stopped():
            time.sleep(0.05)
            control.check()
        return "done"

    monkeypatch.setitem(runner.STAGES, "gather", slow_stage)

    with SessionLocal() as s:
        p = StudioProject(tmdb_id=11, title="API Stop Test")
        s.add(p)
        s.commit()
        pid = p.id

    # Start stage via API
    r_run = client.post(f"/api/studio/projects/{pid}/run", json={"stage": "gather", "auto": True})
    assert r_run.status_code == 200

    # Wait for stage to be running
    for _ in range(50):
        if client.get(f"/api/studio/projects/{pid}").json()["stage_status"] == "running":
            break
        time.sleep(0.02)

    # Call stop API endpoint
    r_stop = client.post(f"/api/studio/projects/{pid}/stop")
    assert r_stop.status_code == 200
    assert r_stop.json()["stage_status"] == "stopped"

    # Verify project in DB is stopped
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.stage_status == "stopped"
