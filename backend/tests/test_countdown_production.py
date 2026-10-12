"""Countdown production loop (audit 2026-10-11)."""

from unittest.mock import patch

from backend.app.db import SessionLocal
from backend.app.models import QueueItem, StudioProject
from backend.app.studio.compilations import production


def _countdown(s, renders):
    pids = []
    for i, r in enumerate(renders):
        p = StudioProject(tmdb_id=500 + i, media_type="movie", title=f"T{i}", stage="plan" if not r else "render",
                          stage_status="done", render={"file": "render/final.mp4"} if r else None)
        s.add(p)
        s.commit()
        pids.append(p.id)
    q = QueueItem(pipeline="LongForm", source="Countdown Studio", title="Top 2", status="review",
                  accounts=["default:*"], research={"project_ids": pids, "production": {"state": "making"},
                                                    "countdown": {"topic": "x", "entries": []}})
    s.add(q)
    s.commit()
    return q.id, pids


def test_tick_makes_next_segment_only_when_studio_is_free():
    with SessionLocal() as s:
        qid, pids = _countdown(s, [True, False])
    rendered = lambda pid, r: "x.mp4" if r else None
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", side_effect=rendered), \
         patch("backend.app.studio.runner.busy", return_value={"project_id": 99}), \
         patch("backend.app.studio.runner.start") as start:
        assert production.tick() is None and not start.called          # Studio busy: wait
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", side_effect=rendered), \
         patch("backend.app.studio.runner.busy", return_value={"project_id": None}), \
         patch("backend.app.studio.runner.queued_jobs", return_value=[]), \
         patch("backend.app.studio.runner.start") as start:
        did = production.tick()
    assert did == {"segment": pids[1], "stage": "render", "countdown": qid}
    start.assert_called_once_with(pids[1], "render", auto=True, until="render")
    with SessionLocal() as s:
        assert s.get(QueueItem, qid).notes.startswith("In production: 1 of 2")
        s.delete(s.get(QueueItem, qid)); s.commit()


def test_tick_stitches_when_all_segments_are_made_and_attaches_video():
    with SessionLocal() as s:
        qid, pids = _countdown(s, [True, True])
    fake = {"video_path": "/v/final.mp4", "primary_thumb": "/v/t.jpg", "compilation_id": 7, "total_minutes": 9.1,
            "chapters": [{"time_formatted": "0:00", "title": "Intro & Preview"}]}
    run_now = lambda kind, label, scope, fn, *a, **kw: fn(*a)
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", return_value="x.mp4"), \
         patch("backend.app.control.start_thread", side_effect=run_now), \
         patch("backend.app.studio.compilations.stitch.stitch_countdown_compilation", return_value=fake):
        assert production.tick() == {"stitch": qid}
    with SessionLocal() as s:
        q = s.get(QueueItem, qid)
        assert q.video_path == "/v/final.mp4" and q.status == "review"           # owner approves it
        assert q.research["production"]["state"] == "done" and "0:00 Intro" in q.description
        s.delete(q); s.commit()


def test_next_stage_continues_where_it_stopped():
    p = StudioProject(tmdb_id=1, media_type="movie", title="x", stage="plan", stage_status="done")
    assert production._next_stage(p) == "render"
    p.stage, p.stage_status = "shots", "error"
    assert production._next_stage(p) == "shots"
    p.stage, p.stage_status = "new", "idle"
    assert production._next_stage(p) == "gather"
