"""Countdowns Studio, built like LongForm (2026-10-12)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.models import AppSetting, CountdownProject, QueueItem, StudioProject
from backend.app.studio import countdown, membership

RANKING = {
    "topic": "Top 3 horror", "format": "top5", "title_options": ["Top 3 Horror Movies"], "hook_script": "Hook!",
    "closing_question": "Which scared you most?",
    "entries": [
        {"rank": 1, "title": "The Shining", "year": 1980, "why_it_ranks": "Iconic."},
        {"rank": 2, "title": "Alien", "year": 1979, "why_it_ranks": "Space terror."},
        {"rank": 3, "title": "Not A Movie Theory", "year": 2026},
    ],
}
IDS = {"The Shining": 694, "Alien": 348}


@pytest.fixture(autouse=True)
def _fresh():
    """Each test starts with no countdowns (the scheduler tick walks all of them)."""
    def clear():
        with SessionLocal() as s:
            s.query(CountdownProject).delete()
            s.commit()
    clear()
    yield
    clear()          # the app's background scheduler must not pick up a test's countdowns


@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


def _researched(client) -> int:
    resolve = lambda e: {"tmdb_id": IDS[e["title"]], "media_type": "movie"} if e.get("title") in IDS else None
    with patch("backend.app.studio.compilations.engine.generate_countdown", return_value=dict(RANKING, entries=[dict(e) for e in RANKING["entries"]])), \
         patch("backend.app.studio.compilations.engine._resolve_title", side_effect=resolve), \
         patch.object(countdown, "start_research", side_effect=lambda cid: countdown.research_job(cid)):
        r = client.post("/api/countdowns", json={"topic": "Top 3 horror", "fmt": "top5"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_research_flags_non_titles_and_confirm_refuses_them(client):
    cid = _researched(client)
    c = client.get(f"/api/countdowns/{cid}").json()
    assert c["stage"] == "research" and c["stage_status"] == "done" and c["unmatched"] == 1
    r = client.post(f"/api/countdowns/{cid}/confirm")
    assert r.status_code == 400 and "Not A Movie Theory" in r.json()["detail"]
    # Owner removes the non-title (list order = rank, #1 first)
    entries = [e for e in c["ranking"]["entries"] if e.get("tmdb_id")]
    c = client.put(f"/api/countdowns/{cid}/entries", json={"entries": entries}).json()
    assert c["unmatched"] == 0 and c["entry_count"] == 2
    c = client.post(f"/api/countdowns/{cid}/confirm").json()
    assert c["stage"] == "segments" and c["segments_total"] == 2 and c["script"]["intro"] == "Hook!"
    with SessionLocal() as s:
        cp = s.get(CountdownProject, cid)
        segs = [s.get(StudioProject, pid) for pid in cp.segment_ids]
        assert [p.title for p in segs] == ["Alien", "The Shining"]               # lowest rank plays first
        assert segs[1].facts["countdown"]["rank"] == 1 and segs[0].target_minutes == 1.6
        ids = membership.countdown_ids(s)
        assert all(membership.is_countdown(p, ids) for p in segs)                # never in LongForm Studio


def test_real_longform_breakdown_is_reused_untouched(client):
    with SessionLocal() as s:
        lf = StudioProject(tmdb_id=IDS["Alien"], media_type="movie", title="Alien", queue_item_id=999,
                           facts={"primary_date": "1979-05-25"})
        s.add(lf)
        s.commit()
        lf_id = lf.id
    cid = _researched(client)
    c = client.get(f"/api/countdowns/{cid}").json()
    client.put(f"/api/countdowns/{cid}/entries", json={"entries": [e for e in c["ranking"]["entries"] if e.get("tmdb_id")]})
    c = client.post(f"/api/countdowns/{cid}/confirm").json()
    assert lf_id in [x["id"] for x in c["segments"]]
    with SessionLocal() as s:
        assert "countdown" not in (s.get(StudioProject, lf_id).facts or {})      # LongForm project not changed


def test_tick_makes_one_segment_when_idle_then_render_and_publish(client):
    cid = _researched(client)
    c = client.get(f"/api/countdowns/{cid}").json()
    client.put(f"/api/countdowns/{cid}/entries", json={"entries": [e for e in c["ranking"]["entries"] if e.get("tmdb_id")]})
    c = client.post(f"/api/countdowns/{cid}/confirm").json()
    seg_ids = [x["id"] for x in c["segments"]]
    rendered: set[int] = set()
    resolve = lambda pid, r: "x.mp4" if pid in rendered else None
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", side_effect=resolve), \
         patch("backend.app.studio.runner.busy", return_value={"project_id": None}), \
         patch("backend.app.studio.runner.queued_jobs", return_value=[]), \
         patch("backend.app.studio.runner.start") as start:
        did = countdown.tick()
        assert did["segment"] == seg_ids[0] and did["stage"] == "gather"
        start.assert_called_once_with(seg_ids[0], "gather", auto=True, until="render")
        rendered.update(seg_ids)
        countdown.tick()
        c = client.get(f"/api/countdowns/{cid}").json()
        assert c["stage_status"] == "done" and c["has"]["segments"]
        fake = {"video_path": "/v/final.mp4", "primary_thumb": "/v/thumbnails/thumbnail.jpg", "compilation_id": 42,
                "chapters": [{"time_formatted": "0:00", "title": "Intro & Preview"}], "total_seconds": 540.0,
                "total_minutes": 9.0}
        with patch("backend.app.studio.compilations.stitch.stitch_countdown_compilation", return_value=fake):
            countdown.render_job(cid)
    c = client.get(f"/api/countdowns/{cid}").json()
    assert c["has"]["render"] and c["render"]["minutes"] == 9.0
    r = client.post(f"/api/countdowns/{cid}/publish").json()
    with SessionLocal() as s:
        q = s.get(QueueItem, r["queue_item_id"])
        assert q.pipeline == "Countdowns" and q.status == "ready" and q.video_path == "/v/final.mp4"
        assert q.accounts == ["default:*"] and "0:00 Intro" in q.description


def test_segment_failure_stops_the_countdown_with_the_reason(client):
    cid = _researched(client)
    c = client.get(f"/api/countdowns/{cid}").json()
    client.put(f"/api/countdowns/{cid}/entries", json={"entries": [e for e in c["ranking"]["entries"] if e.get("tmdb_id")]})
    c = client.post(f"/api/countdowns/{cid}/confirm").json()
    with SessionLocal() as s:
        p = s.get(StudioProject, c["segments"][0]["id"])
        p.stage, p.stage_status, p.stage_message = "trailer", "error", "no trailer found"
        s.commit()
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", return_value=None):
        countdown.tick()
    c = client.get(f"/api/countdowns/{cid}").json()
    assert c["stage_status"] == "error" and "no trailer found" in c["stage_message"]
    with patch("backend.app.studio.countdown.tick"):
        c = client.post(f"/api/countdowns/{cid}/make").json()
    assert c["stage_status"] == "waiting"


def test_migration_moves_old_rows_and_saved_lists():
    with SessionLocal() as s:
        flag = s.get(AppSetting, "countdowns_migrated")
        if flag:
            s.delete(flag)
        s.add(QueueItem(pipeline="LongForm", source="Countdown Studio", title="Old", status="review",
                        research={"countdown": {"id": "cd-1", "topic": "Old list", "format": "top5",
                                                "entries": [{"rank": 1, "title": "X"}]}}))
        saved = s.get(AppSetting, "saved_countdowns")
        if saved:
            s.delete(saved)
        s.add(AppSetting(key="saved_countdowns", value={"items": [
            {"id": "cd-1", "topic": "Old list", "entries": [{"rank": 1, "title": "X"}]},
            {"id": "cd-2", "topic": "Other list", "format": "top10", "entries": [{"rank": 1, "title": "Y"}]},
            {"id": "cd-9", "topic": "Other list", "format": "top10", "entries": [{"rank": 1, "title": "Y"}]}]}))  # same list saved twice
        s.commit()
    with SessionLocal() as s:
        segs = [StudioProject(tmdb_id=900 + i, media_type="movie", title=f"S{i}") for i in range(2)]
        s.add_all(segs)
        s.commit()
        s.add(QueueItem(pipeline="LongForm", source="Countdown Studio", title="With segments", status="review",
                        research={"project_ids": [segs[0].id, segs[1].id],
                                  "countdown": {"id": "cd-3", "topic": "Seg list", "format": "top5",
                                                "entries": [{"rank": 1, "title": "S1", "tmdb_id": 901},
                                                            {"rank": 2, "title": "S0", "tmdb_id": 900}]}}))
        s.commit()
        seg_ids = [segs[0].id, segs[1].id]
    assert countdown.migrate_legacy() == 3
    with SessionLocal() as s:
        c = s.query(CountdownProject).filter(CountdownProject.topic == "Seg list").one()
        assert c.segment_ids == seg_ids and c.stage == "segments" and c.stage_status == "stopped"   # nothing auto-spends
    assert countdown.migrate_legacy() == 0                                         # only once
    with SessionLocal() as s:
        assert s.query(QueueItem).filter(QueueItem.source == "Countdown Studio").count() == 0
        topics = {c.topic for c in s.query(CountdownProject).all()}
        assert {"Old list", "Other list"} <= topics


def test_file_route_serves_only_the_countdowns_files(client, tmp_path, monkeypatch):
    from backend.app.studio.compilations import stitch
    monkeypatch.setattr(stitch, "compilations_dir", lambda: tmp_path)
    (tmp_path / "7" / "thumbnails").mkdir(parents=True)
    (tmp_path / "7" / "final_compilation.mp4").write_bytes(b"v")
    (tmp_path / "secret.txt").write_text("x")
    with SessionLocal() as s:
        c = CountdownProject(topic="t", render={"compilation_id": 7, "thumbnails": {}})
        s.add(c)
        s.commit()
        cid = c.id
    assert client.get(f"/api/countdowns/{cid}/file").status_code == 200
    assert client.get(f"/api/countdowns/{cid}/file", params={"path": "../secret.txt"}).status_code == 404


def test_countdown_segment_script_prompt():
    from backend.app.studio import stage_script
    ctx = {"rank": 4, "total": 10, "topic": "Top 10 horror", "why_it_ranks": "Iconic", "fun_fact": "Shot in 3 weeks"}
    p = stage_script.countdown_prompt("board", "Alien", 0.9, ctx)
    assert 'Number 4: Alien.' in p and "subscribe" in p.lower()
    assert "[C1] Ranked #4 of 10" in stage_script.countdown_notes(ctx)


def _confirmed(client) -> tuple[int, list[int]]:
    cid = _researched(client)
    c = client.get(f"/api/countdowns/{cid}").json()
    client.put(f"/api/countdowns/{cid}/entries", json={"entries": [e for e in c["ranking"]["entries"] if e.get("tmdb_id")]})
    c = client.post(f"/api/countdowns/{cid}/confirm").json()
    return cid, [x["id"] for x in c["segments"]]


def test_longform_goes_first_outside_midday_then_midday_turn(client):
    cid, segs = _confirmed(client)
    import datetime as dt
    morning = dt.datetime(2026, 10, 13, 13, 0, tzinfo=dt.timezone.utc)     # 9:00 ET
    noon = dt.datetime(2026, 10, 13, 16, 30, tzinfo=dt.timezone.utc)       # 12:30 ET
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", return_value=None), \
         patch.object(countdown, "longform_first", return_value=True), \
         patch("backend.app.studio.runner.start_or_queue", return_value="queued") as soq, \
         patch.object(countdown, "_now", return_value=morning):
        assert countdown.tick() is None and not soq.called
        assert "LongForm breakdowns go first" in client.get(f"/api/countdowns/{cid}").json()["stage_message"]
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", return_value=None), \
         patch.object(countdown, "longform_first", return_value=True), \
         patch("backend.app.studio.runner.start_or_queue", return_value="queued") as soq, \
         patch.object(countdown, "_now", return_value=noon):
        did = countdown.tick()
    assert did["segment"] == segs[0] and did["state"] == "queued"
    assert soq.call_count == 1 and soq.call_args.args[0] == segs[0] and soq.call_args.kwargs == {"auto": True, "until": "render"}
    assert "next in Studio's line" in client.get(f"/api/countdowns/{cid}").json()["stage_message"]


def test_segment_length_fixed_for_unscripted_segments_only(client):
    cid, segs = _confirmed(client)
    with SessionLocal() as s:
        a, b = s.get(StudioProject, segs[0]), s.get(StudioProject, segs[1])
        a.target_minutes, a.script = 1.5, {"sentences": []}            # already written: keep
        b.target_minutes = 1.5                                         # old auto-queue length: fix
        s.commit()
        assert countdown.fix_lengths(s, s.get(CountdownProject, cid)) == 1
        s.commit()
        assert s.get(StudioProject, segs[0]).target_minutes == 1.5 and s.get(StudioProject, segs[1]).target_minutes == 1.6


def test_daily_cap_pause_is_shown_on_the_countdown(client):
    cid, segs = _confirmed(client)
    with SessionLocal() as s:
        p = s.get(StudioProject, segs[0])
        p.stage, p.stage_status = "shots", "paused"
        s.commit()
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", return_value=None):
        countdown.tick()
    c = client.get(f"/api/countdowns/{cid}").json()
    assert c["stage_status"] == "paused" and "AI budget" in c["stage_message"]


def test_countdowns_queue_posts_at_midday_once():
    from backend.app.social import queue_manager as qm
    with SessionLocal() as s:
        for k in ("countdowns_schedule_set",):
            row = s.get(AppSetting, k)
            if row:
                s.delete(row)
        s.commit()
    assert countdown.ensure_midday_schedule() is True
    with SessionLocal() as s:
        assert qm.load_schedule(s)["pipelines"]["Countdowns"] == {"times": ["12:00"]}
    assert countdown.ensure_midday_schedule() is False                      # never put back over the owner's choice
    with SessionLocal() as s:
        qm.save_schedule(s, None)


def test_seo_tags_use_vidiq_keywords_then_titles():
    c = CountdownProject(topic="Top 3 horror", fmt="top5",
                         review={"keyword": "best horror movies", "keywords": ["scariest horror movies", "horror movies 2026"]})
    tags = countdown.seo_tags(c, [{"title": "Alien", "rank": 2}, {"title": "The Shining", "rank": 1}])
    assert tags.startswith("#besthorrormovies #scariesthorrormovies #horrormovies2026 #theshining #alien")
    assert len(tags.split()) <= 12


def test_batch_and_segment_length(client):
    cid, segs = _confirmed(client)
    c = client.put(f"/api/countdowns/{cid}/settings", json={"segment_minutes": 1.2}).json()
    assert c["segment_minutes"] == 1.2
    with SessionLocal() as s:
        assert all(s.get(StudioProject, p).target_minutes == 1.2 for p in segs if not s.get(StudioProject, p).script)
    r = client.post("/api/countdowns/batch", json={"action": "render", "ids": [cid]}).json()
    assert r["done"] == 0 and "segments" in r["results"][0]["why"]
    r = client.post("/api/countdowns/batch", json={"action": "archive", "ids": [cid]}).json()
    assert r["done"] == 1 and client.get(f"/api/countdowns/{cid}").json()["review"]["delete_after"]


def test_restart_interruption_is_retried_not_a_failure(client):
    cid, segs = _confirmed(client)
    with SessionLocal() as s:
        p = s.get(StudioProject, segs[0])
        p.stage, p.stage_status, p.stage_message = "shots", "stopped", "shots was interrupted by a server restart — re-run it"
        s.commit()
    with patch("backend.app.studio.compilations.stitch.resolve_project_render", return_value=None), \
         patch.object(countdown, "longform_first", return_value=False), \
         patch("backend.app.studio.runner.start_or_queue", return_value="started") as soq:
        countdown.tick()
    assert client.get(f"/api/countdowns/{cid}").json()["stage_status"] == "running"
    assert soq.call_args.args[:2] == (segs[0], "shots")
