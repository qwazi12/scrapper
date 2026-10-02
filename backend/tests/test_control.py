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
        with control.job("test", "lookup test", "studio", 99):
            started.set()
            while not control.stopped():
                time.sleep(0.05)

    threading.Thread(target=work, daemon=True).start()
    started.wait(2)
    jid = next(j["id"] for j in control.list_jobs() if j["label"] == "lookup test")
    r = c.get(f"/api/jobs/{jid}", headers=hdr)
    assert r.status_code == 200 and r.json()["status"] == "running"     # was a 422 from the int-only route
    assert c.post(f"/api/jobs/{jid}/stop", headers=hdr).json()["status"] == "stopping"
    assert c.get("/api/jobs/999999", headers=hdr).status_code == 404     # numeric ids still mean ingest jobs
