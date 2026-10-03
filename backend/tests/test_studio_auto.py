"""Breakdown automation: checked & ranked candidates, daily limits per type,
the tick, render batches and queue batches. Network is mocked throughout."""

import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import costs, resume
from backend.app.db import SessionLocal
from backend.app.models import AppSetting, QueueItem, ResumableJob, StudioProject, UsageEvent
from backend.app.studio import auto, candidates, imdb, runner, tmdb, youtube

TODAY = dt.date.today()


def _iso(days):
    return (TODAY + dt.timedelta(days=days)).isoformat()


@pytest.fixture(autouse=True)
def clean():
    with SessionLocal() as s:
        for m in (StudioProject, UsageEvent, ResumableJob):
            s.query(m).delete()
        for k in ("studio_candidates", "studio_candidate_marks", "studio_auto", "costs"):
            row = s.get(AppSetting, k)
            if row:
                s.delete(row)
        s.commit()
    auto.state.update(last_check_at=None, last_result=None, last_started=None)
    yield


@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


# A small fake world: 3 movies, 2 TV shows.
TITLES = {
    ("movie", 1): dict(title="Hit Movie", pop=300, release=_iso(10), trailer=True, yt="ytA", cast=10),
    ("movie", 2): dict(title="Old Movie", pop=200, release=_iso(-400), trailer=True, yt="ytB", cast=10),
    ("movie", 3): dict(title="No Trailer", pop=150, release=_iso(5), trailer=False, yt=None, cast=10),
    ("tv", 10): dict(title="Airing Show", pop=250, next=_iso(3), trailer=True, yt="ytC", cast=8),
    ("tv", 11): dict(title="YouTube Only Show", pop=120, next=_iso(6), trailer=False, yt="ytD", cast=8),
}


@pytest.fixture()
def world(monkeypatch):
    def fake_get(path, **params):
        if path.startswith("/trending/"):
            mt = path.split("/")[2]
            if params.get("page", 1) > 1:
                return {"results": []}
            return {"results": [{"id": tid, "popularity": TITLES[(m, tid)]["pop"], "vote_average": 7.5}
                                for (m, tid) in TITLES if m == mt]}
        mt, tid = path.split("/")[1], int(path.split("/")[2])
        t = TITLES[(mt, tid)]
        d = {"id": tid, "overview": "x", "poster_path": "/p.jpg", "vote_average": 7.5,
             "credits": {"cast": [{}] * t["cast"]}, "external_ids": {"imdb_id": f"tt{tid:07d}"},
             "videos": {"results": [{"site": "YouTube", "type": "Trailer", "official": True, "key": t["yt"]}] if t["yt"] else []}}
        if mt == "movie":
            d["title"] = t["title"]
            d["release_dates"] = {"results": [{"iso_3166_1": "US", "release_dates": [{"type": 3, "release_date": t["release"] + "T00:00:00Z"}]}]}
        else:
            d["name"] = t["title"]
            d["next_episode_to_air"] = {"air_date": t["next"]}
        return d

    def fake_audience(imdb_id, reviews=10):
        tid = int(imdb_id[2:])
        t = next(v for (m, i), v in TITLES.items() if i == tid)
        return {"rating": 7.8, "votes": 5000, "metascore": 70, "meter_rank": 50, "meter_change": "UP",
                "review_avg": 8.0, "review_lines": [], "has_trailer": t["trailer"], "reviews_total": 10}

    monkeypatch.setattr(tmdb, "get", fake_get)
    monkeypatch.setattr(imdb, "audience", fake_audience)
    monkeypatch.setattr(youtube, "configured", lambda: True)
    monkeypatch.setattr(youtube, "video_stats", lambda ids: {i: {"views": 2_000_000, "likes": 60_000, "comments": 900,
                                                                  "published_at": "2026-09-20T00:00:00Z"} for i in ids})
    candidates.build()


def test_list_has_checks_reasons_and_sentiment(world):
    lst = candidates.listing()
    assert [r["title"] for r in lst["movie"]] == ["Hit Movie"]
    assert [r["title"] for r in lst["tv"]] == ["Airing Show", "YouTube Only Show"]
    why = {r["title"]: r["checks"] for r in lst["not_passing"]}
    assert why["Old Movie"]["release"]["status"] == "fail" and "days ago" in why["Old Movie"]["release"]["why"]
    assert why["No Trailer"]["trailer"]["status"] == "fail"
    yt_only = next(r for r in lst["tv"] if r["title"] == "YouTube Only Show")
    assert yt_only["checks"]["trailer"]["status"] == "warn" and not yt_only["auto_ok"]
    hit = lst["movie"][0]
    assert hit["score"]["sentiment"]["parts"]["imdb_rating"] == 0.78
    assert hit["score"]["parts"]["rising"] == 3.0 and 0 < hit["score"]["total"] <= 100


def test_marks_pin_and_skip_survive_refresh(world, client):
    client.put("/api/studio/candidates/tv:10/mark", json={"mark": "skip"})
    lst = client.get("/api/studio/candidates").json()
    assert all(r["key"] != "tv:10" for r in lst["tv"])
    assert client.put("/api/studio/candidates/bogus/mark", json={"mark": "pin"}).status_code == 400
    candidates.build()
    assert any(r["key"] == "tv:10" and r["mark"] == "skip" for r in candidates.listing()["not_passing"])


def test_made_titles_drop_out_live(world):
    with SessionLocal() as s:
        s.add(StudioProject(tmdb_id=1, media_type="movie", title="Hit Movie"))
        s.commit()
    assert candidates.listing()["movie"] == []


def _started(monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "start", lambda pid, name, auto=False, rid=None, until="script": calls.append((pid, name, until)))
    return calls


def test_tick_starts_best_auto_ok_title_and_respects_type_limits(world, monkeypatch):
    calls = _started(monkeypatch)
    auto.save_settings({"enabled": True, "movies_per_day": 1, "tv_per_day": 1})
    first = auto.tick()
    second = auto.tick()
    assert {first["title"], second["title"]} == {"Hit Movie", "Airing Show"}
    assert all(c[1:] == ("gather", "plan") for c in calls)           # steps 1–5, stops before render
    assert auto.tick() is None and "limits reached" in auto.state["last_result"]
    with SessionLocal() as s:
        assert all((p.review or {}).get("auto") for p in s.query(StudioProject).all())


def test_tick_is_off_by_default_and_waits_for_budget_paused_jobs(world, monkeypatch):
    calls = _started(monkeypatch)
    assert auto.tick() is None and calls == []
    auto.save_settings({"enabled": True})
    r = resume.begin("studio", "paused one", {"project_id": 99, "stage": "shots"})
    resume.finish(r, "budget_paused")
    assert auto.tick() is None and "paused by the daily cap" in auto.state["last_result"]


def test_tick_waits_when_a_full_breakdown_would_not_fit_the_cap(world, monkeypatch):
    calls = _started(monkeypatch)
    auto.save_settings({"enabled": True})
    with SessionLocal() as s:
        s.add(UsageEvent(service="gemini", cost_usd=5.7, month=costs.month_key()))
        s.commit()
    assert auto.tick() is None and "not enough budget" in auto.state["last_result"] and calls == []


def test_manual_create_counts_toward_the_limit_and_can_be_forced(client):
    auto.save_settings({"movies_per_day": 1})
    assert client.post("/api/studio/projects", json={"tmdb_id": 5, "media_type": "movie", "title": "A"}).status_code == 200
    r = client.post("/api/studio/projects", json={"tmdb_id": 6, "media_type": "movie", "title": "B"})
    assert r.status_code == 409 and "daily_limit" in r.json()["detail"]
    assert client.post("/api/studio/projects", json={"tmdb_id": 6, "media_type": "movie", "title": "B",
                                                      "force": True}).status_code == 200
    assert client.post("/api/studio/projects", json={"tmdb_id": 7, "media_type": "tv", "title": "C"}).status_code == 200


def test_auto_settings_validate(client):
    assert client.put("/api/studio/auto", json={"movies_per_day": 99}).status_code == 400
    d = client.put("/api/studio/auto", json={"movies_per_day": 4, "tv_per_day": 1}).json()
    assert d["movies_per_day"] == 4 and d["tv_per_day"] == 1 and d["enabled"] is False and d["undo"]


def test_review_mark(client):
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=8, title="R", review={"auto": True})
        s.add(p)
        s.commit()
        pid = p.id
    d = client.put(f"/api/studio/projects/{pid}/review", json={"reviewed": True}).json()
    assert d["review"]["reviewed_at"] and d["review"]["auto"] is True


def test_render_batch_runs_one_by_one_and_skips_unready(monkeypatch):
    from backend.app.studio import batch
    monkeypatch.setattr(batch, "POLL", 0.01)
    with SessionLocal() as s:
        ready = StudioProject(tmdb_id=20, title="Ready", script={"s": 1}, plan=[1])
        needs_plan = StudioProject(tmdb_id=21, title="Needs plan", script={"s": 1})
        no_script = StudioProject(tmdb_id=22, title="No script")
        s.add_all([ready, needs_plan, no_script])
        s.commit()
        ids = [ready.id, needs_plan.id, no_script.id]
    order = []

    def fake_start(pid, name, auto=False, rid=None, until="script"):
        order.append((pid, name, until))
        with SessionLocal() as s:
            p = s.get(StudioProject, pid)
            p.stage, p.stage_status = "render", "done"
            p.render = {"rendered_at": f"t{pid}"}
            s.commit()

    monkeypatch.setattr(runner, "start", fake_start)
    batch.start(ids)
    for _ in range(300):
        if not batch.status["running"]:
            break
        time.sleep(0.02)
    assert order == [(ids[0], "render", "render"), (ids[1], "plan", "render")]
    res = {r["title"]: r for r in batch.status["results"]}
    assert res["Ready"]["ok"] and res["Needs plan"]["ok"] and res["No script"]["why"] == "no script yet"
    with SessionLocal() as s:
        row = s.query(ResumableJob).filter(ResumableJob.kind == "studio_batch").one()
        assert row.status == "done" and sorted(row.checkpoint["done"]) == sorted(ids)


def test_publish_batch_reports_each(client, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "project_dir", lambda pid: tmp_path)
    (tmp_path / "final.mp4").write_bytes(b"x")
    from backend.app.studio import routes
    monkeypatch.setattr(routes, "_start_drive", lambda p: None)
    with SessionLocal() as s:
        a = StudioProject(tmdb_id=30, title="Rendered", render={"file": "final.mp4", "rendered_at": "t"}, script={})
        b = StudioProject(tmdb_id=31, title="Not rendered")
        s.add_all([a, b])
        s.commit()
        ids = [a.id, b.id]
    d = client.post("/api/studio/publish-batch", json={"ids": ids}).json()
    assert d["sent"] == 1
    assert [r["ok"] for r in d["results"]] == [True, False]
    with SessionLocal() as s:
        s.query(QueueItem).filter(QueueItem.id == d["results"][0]["queue_item_id"]).delete()
        s.commit()
