"""Jobs cut off by a restart are resumed from their checkpoint (at most twice)."""
import time

from backend.app import control, resume
from backend.app.db import SessionLocal
from backend.app.models import ResumableJob


def _status(rid):
    with SessionLocal() as s:
        r = s.get(ResumableJob, rid)
        return r.status, r.attempts, r.checkpoint, r.last_error


def _clear():
    with SessionLocal() as s:
        s.query(ResumableJob).delete()
        s.commit()


def test_cut_off_job_is_resumed_from_its_checkpoint(monkeypatch):
    _clear()
    calls = []
    monkeypatch.setitem(resume._launchers, "studio", lambda p, cp, rid: calls.append((p, cp, rid)))
    rid = resume.begin("studio", "Studio #7: gather + following steps", {"project_id": 7, "stage": "gather", "auto": True})
    resume.checkpoint(rid, stage="shots")                       # the chain had reached "shots" when the server died
    assert resume.resume_interrupted() == 1
    assert calls == [({"project_id": 7, "stage": "gather", "auto": True}, {"stage": "shots"}, rid)]
    assert _status(rid)[1] == 1


def test_finished_stopped_and_failed_jobs_are_not_resumed(monkeypatch):
    _clear()
    calls = []
    monkeypatch.setitem(resume._launchers, "drive_sync", lambda p, cp, rid: calls.append(rid))
    for st in ("done", "stopped", "failed"):
        r = resume.begin("drive_sync", f"sync {st}", {})
        resume.finish(r, st)
    assert resume.resume_interrupted() == 0 and calls == []


def test_gives_up_after_two_resumes(monkeypatch):
    _clear()
    monkeypatch.setitem(resume._launchers, "drive_save", lambda p, cp, rid: None)
    rid = resume.begin("drive_save", "Save to Drive", {"project_id": 1})
    assert resume.resume_interrupted() == 1                     # 1st restart
    assert resume.resume_interrupted() == 1                     # 2nd restart (still "running")
    assert resume.resume_interrupted() == 0                     # 3rd: stop trying
    st, attempts, _, err = _status(rid)
    assert st == "failed" and attempts == 2 and "3 times" in err


def test_tracked_thread_records_the_outcome():
    _clear()
    jid = resume.tracked_thread("drive_sync", "ok job", "socialpilot", {"x": 1}, lambda: 42)
    for _ in range(50):
        if control.list_jobs() and next((j for j in control.list_jobs() if j["id"] == jid), {}).get("status") == "done":
            break
        time.sleep(0.05)
    time.sleep(0.1)
    with SessionLocal() as s:
        row = s.query(ResumableJob).filter(ResumableJob.label == "ok job").one()
        assert row.status == "done" and row.params == {"x": 1}

    def boom():
        raise RuntimeError("drive down")

    resume.tracked_thread("drive_sync", "bad job", "socialpilot", {}, boom)
    time.sleep(0.3)
    with SessionLocal() as s:
        row = s.query(ResumableJob).filter(ResumableJob.label == "bad job").one()
        assert row.status == "failed" and "drive down" in row.last_error


def test_bulk_ai_resume_skips_finished_videos(monkeypatch):
    _clear()
    from backend.app.social import ai_bulk
    seen = []

    async def fake_run(ids):
        seen.extend(ids)

    monkeypatch.setattr(ai_bulk, "_run", fake_run)
    rid = resume.begin("bulk_ai", "AI rewrite of 4 video(s)", {"ids": [1, 2, 3, 4]})
    resume.add_done(rid, 1)
    resume.add_done(rid, 3)
    assert resume.resume_interrupted() == 1
    for _ in range(50):
        if not ai_bulk.status["running"]:
            break
        time.sleep(0.05)
    assert seen == [2, 4] and _status(rid)[0] == "done"
