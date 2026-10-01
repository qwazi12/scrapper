"""LongForm Studio tests — external APIs (TMDB, Gemini, TTS) are always mocked."""

import pathlib

import pytest
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.models import StudioProject
from backend.app.studio import gemini, runner, stage_gather, tmdb

MOVIE = {
    "id": 1, "title": "Send Help", "tagline": "", "overview": "A boss and employee are stranded on an island.",
    "genres": [{"name": "Thriller"}], "runtime": 113, "status": "Released", "release_date": "2026-01-30",
    "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [
        {"release_date": "2026-01-30T00:00:00.000Z", "type": 3, "note": ""}]}]},
    "production_companies": [{"name": "20th Century Studios"}],
    "credits": {"cast": [{"name": "Rachel McAdams", "character": "Linda", "profile_path": "/rm.jpg", "order": 0},
                         {"name": "Dylan O'Brien", "character": "Bradley", "profile_path": "/do.jpg", "order": 1}],
                "crew": [{"name": "Sam Raimi", "job": "Director", "department": "Directing"}]},
    "videos": {"results": [
        {"name": "Teaser", "type": "Teaser", "site": "YouTube", "key": "t1", "official": True, "published_at": "2025-09-01"},
        {"name": "Official Trailer", "type": "Trailer", "site": "YouTube", "key": "k1", "official": True,
         "published_at": "2025-11-01"}]},
    "images": {"posters": [{"file_path": "/p.jpg"}], "backdrops": [{"file_path": "/b1.jpg"}], "logos": []},
    "external_ids": {"imdb_id": "tt0000001"}, "poster_path": "/p.jpg",
}


@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


@pytest.fixture()
def fake_tmdb(monkeypatch):
    monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "k")
    monkeypatch.setattr(tmdb, "get", lambda path, **kw: MOVIE)
    return MOVIE


def test_fact_sheet_shapes_tmdb_data(fake_tmdb):
    f = tmdb.fact_sheet("movie", 1)
    assert f["title"] == "Send Help" and f["directors"] == ["Sam Raimi"]
    assert f["releases"] == [{"country": "US", "date": "2026-01-30", "type": "Theatrical", "note": ""}]
    assert f["cast"][0]["character"] == "Linda"
    assert f["videos"][0]["type"] == "Trailer"  # trailers sort first
    assert f["attribution"] == tmdb.ATTRIBUTION and f["source"].endswith("/movie/1")


def test_tmdb_key_missing_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "")
    with pytest.raises(tmdb.TMDBError, match="TMDB_API_KEY"):
        tmdb.fact_sheet("movie", 1)


def test_v4_token_uses_bearer(monkeypatch):
    monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "eyJabc")
    headers, params = tmdb._auth()
    assert headers["Authorization"] == "Bearer eyJabc" and params == {}


def _new_project(**kw) -> int:
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=1, media_type="movie", title="Send Help", **kw)
        s.add(p)
        s.commit()
        return p.id


def test_gather_saves_facts_assets_and_sourced_research(fake_tmdb, monkeypatch, tmp_path):
    monkeypatch.setattr(stage_gather, "_download", lambda url, dest: str(dest) if url else None)
    monkeypatch.setattr(gemini, "research", lambda prompt: {
        "text": "Releases Jan 30, 2026 in theaters.", "queries": ["send help release date"],
        "sources": [{"title": "deadline.com", "url": "https://x"}],
        "claims": [{"text": "Releases Jan 30, 2026 in theaters.", "sources": [0]}]})
    pid = _new_project()
    summary = runner.run_one(pid, "gather")
    assert "2 cast" in summary and "1 web sources" in summary
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.facts["local"]["poster"].endswith("poster.jpg")
        assert p.research["claims"][0]["sources"] == [0]
        assert p.stage_status == "done"


def test_failed_stage_is_recorded_not_silent(monkeypatch):
    monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "")
    pid = _new_project()
    with pytest.raises(tmdb.TMDBError):
        runner.run_one(pid, "gather")
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.stage_status == "error" and "TMDB_API_KEY" in p.stage_message


def test_project_api_create_patch_delete(client):
    r = client.post("/api/studio/projects", json={"tmdb_id": 5, "media_type": "movie", "title": "X"})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.patch(f"/api/studio/projects/{pid}", json={"target_minutes": 2.5}).json()["target_minutes"] == 2.5
    assert client.patch(f"/api/studio/projects/{pid}", json={"target_minutes": 30}).status_code == 400
    assert any(p["id"] == pid for p in client.get("/api/studio/projects").json())
    assert client.delete(f"/api/studio/projects/{pid}").json() == {"deleted": pid}


def test_bad_media_type_and_unknown_stage(client):
    assert client.post("/api/studio/projects", json={"tmdb_id": 5, "media_type": "game"}).status_code == 400
    pid = client.post("/api/studio/projects", json={"tmdb_id": 5}).json()["id"]
    assert client.post(f"/api/studio/projects/{pid}/run", json={"stage": "nope"}).status_code == 400


def test_file_route_blocks_path_escape(client):
    pid = _new_project()
    (runner.project_dir(pid) / "ok.txt").write_text("hi")
    assert client.get(f"/api/studio/projects/{pid}/file", params={"path": "ok.txt"}).text == "hi"
    assert client.get(f"/api/studio/projects/{pid}/file", params={"path": "../../scrapper.db"}).status_code == 404


def test_status_reports_missing_keys(client):
    d = client.get("/api/studio/status").json()
    assert d["tmdb"] is False and d["tts"] is False and d["stages"][0] == "gather"


# --- trailer + shots ------------------------------------------------------------
import subprocess

from backend.app.models import Clip
from backend.app.studio import imdb, media, stage_shots, stage_trailer


@pytest.fixture(scope="session")
def three_scene_video(tmp_path_factory) -> pathlib.Path:
    """3 hard cuts: red 2s, blue 2s, green 2s (real ffmpeg)."""
    out = tmp_path_factory.mktemp("vid") / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=320x180:d=2:r=24",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2:r=24",
                    "-f", "lavfi", "-i", "color=c=green:s=320x180:d=2:r=24",
                    "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0", "-pix_fmt", "yuv420p", str(out)],
                   check=True)
    return out


def test_pick_videos_main_trailer_then_capped_extras():
    vids = [{"id": "vi1", "type": "Trailer", "seconds": 140}, {"id": "vi2", "type": "Clip", "seconds": 200},
            {"id": "vi3", "type": "Clip", "seconds": 90}, {"id": "vi4", "type": "Promo", "seconds": 60},
            {"id": "vi5", "type": "Interview", "seconds": 30}]
    got = [v["id"] for v in stage_trailer.pick_videos(vids)]
    assert got == ["vi1", "vi2", "vi3"]  # extras capped at 300 s; interviews skipped


def test_imdb_ids_are_validated():
    with pytest.raises(imdb.IMDbError):
        imdb.list_videos('tt1") { evil }')


def test_trailer_stage_downloads_from_imdb(monkeypatch, three_scene_video):
    pid = _new_project(facts={"imdb_id": "tt8036976", "videos": []})
    monkeypatch.setattr(imdb, "list_videos", lambda i: [{"id": "vi1", "type": "Trailer", "seconds": 6, "name": "T"}])
    monkeypatch.setattr(imdb, "download", lambda vid, dest: dest.write_bytes(three_scene_video.read_bytes()) or 1)
    assert "IMDb trailer" in runner.run_one(pid, "trailer")
    with SessionLocal() as s:
        t = s.get(StudioProject, pid).trailer
        assert t["origin"] == "imdb" and pathlib.Path(t["file"]).exists()


def test_trailer_stage_falls_back_to_home_worker(monkeypatch):
    pid = _new_project(facts={"imdb_id": None, "videos": [
        {"site": "YouTube", "type": "Trailer", "url": "https://www.youtube.com/watch?v=k1"}]})
    msg = runner.run_one(pid, "trailer")
    assert "home Mac worker" in msg
    with SessionLocal() as s:
        t = s.get(StudioProject, pid).trailer
        clip = s.get(Clip, t["pending_clip_id"])
        assert clip.file_path is None and clip.source_url.endswith("k1")  # what the worker polls for


def test_shots_stage_cuts_and_tags(monkeypatch, three_scene_video):
    pid = _new_project(facts={"cast": [{"actor": "Rachel McAdams", "character": "Linda"}], "local": {"cast": {}}},
                       trailer={"file": str(three_scene_video), "sources": [
                           {"id": "vi1", "type": "Trailer", "file": str(three_scene_video)}]})

    def fake_tag(prompt, images=None, **kw):
        n = len(images)
        return [{"i": k + 1, "description": f"shot {k + 1}", "people": ["Rachel McAdams", "Nobody"],
                 "setting": "beach", "mood": "tense", "size": "wide", "card": k == 0, "text": False,
                 "quality": "good"} for k in range(n)]

    monkeypatch.setattr(gemini, "ask_json", fake_tag)
    summary = runner.run_one(pid, "shots")
    with SessionLocal() as s:
        shots = s.get(StudioProject, pid).shots
    assert len(shots) == 3, summary
    assert shots[0]["card"] and not shots[0]["usable"]           # title/logo cards stay out
    assert shots[1]["usable"] and shots[1]["people"] == ["Rachel McAdams"]  # unknown names dropped
    assert (runner.project_dir(pid) / shots[1]["still"]).exists()
