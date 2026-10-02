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
    monkeypatch.setattr(tmdb.settings, "tmdb_read_token", "")
    with pytest.raises(tmdb.TMDBError, match="API_Read_Access_Token or TMDB_API_KEY"):
        tmdb.fact_sheet("movie", 1)


def test_read_token_is_preferred_and_sent_as_bearer(monkeypatch):
    monkeypatch.setattr(tmdb.settings, "tmdb_read_token", "eyJtok")
    monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "k3")
    headers, params = tmdb._auth()
    assert headers["Authorization"] == "Bearer eyJtok" and params == {}


def test_falls_back_to_api_key_on_401(monkeypatch):
    monkeypatch.setattr(tmdb.settings, "tmdb_read_token", "bad")
    monkeypatch.setattr(tmdb.settings, "tmdb_api_key", "good")
    seen = []

    class R:
        def __init__(self, code):
            self.status_code, self.is_success, self.text = code, code == 200, "x"

        def json(self):
            return {"ok": True}

    def fake_get(url, headers=None, params=None, timeout=None):
        seen.append(("Authorization" in headers, params.get("api_key")))
        return R(401) if "Authorization" in headers else R(200)

    monkeypatch.setattr(tmdb.httpx, "get", fake_get)
    assert tmdb.get("/movie/1") == {"ok": True}
    assert seen == [(True, None), (False, "good")]


def test_env_name_from_railway_is_read(monkeypatch):
    from backend.app.config import Settings
    monkeypatch.setenv("API_Read_Access_Token", "eyJfromrailway")
    assert Settings().tmdb_read_token == "eyJfromrailway"


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


def test_status_reports_missing_keys(client, monkeypatch):
    monkeypatch.setattr(tmdb.settings, "tmdb_read_token", "")
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


# --- script / plan / render ---------------------------------------------------
from backend.app.studio import stage_plan, stage_render, stage_script, tts


def _facts():
    return {"title": "Send Help", "media_type": "movie", "overview": "Stranded on an island.",
            "primary_date": "2026-01-30", "releases": [{"country": "US", "date": "2026-01-30", "type": "Theatrical", "note": ""}],
            "directors": ["Sam Raimi"], "cast": [{"actor": "Rachel McAdams", "character": "Linda"}],
            "genres": ["Thriller"], "source": "https://www.themoviedb.org/movie/1", "local": {}}


def test_script_drops_unsupported_claims_and_credits_sources(monkeypatch):
    calls = []

    def fake(prompt, **kw):
        calls.append(prompt)
        if "fact-checker" in prompt:
            return [{"i": 1, "verdict": "ok"},
                    {"i": 2, "verdict": "unsupported", "reason": "storyboard says island, not city", "fix": ""},
                    {"i": 3, "verdict": "unsupported", "reason": "wrong date", "fix": "It opens January 30, 2026."}]
        return {"sentences": [{"paragraph": 1, "text": "Send Help is Sam Raimi's thriller.", "refs": ["F1"]},
                              {"paragraph": 1, "text": "Friends are stuck in a big city.", "refs": []},
                              {"paragraph": 2, "text": "It opens in March.", "refs": ["F5"]}],
                "youtube_title": "Send Help Trailer Breakdown", "tags": ["send help"]}

    monkeypatch.setattr(gemini, "ask_json", fake)
    pid = _new_project(facts=_facts(), research={"sources": [{"title": "deadline.com", "url": "https://d"}],
                                                 "claims": [{"text": "Opens Jan 30.", "sources": [0]}]})
    summary = runner.run_one(pid, "script")
    with SessionLocal() as s:
        sc = s.get(StudioProject, pid).script
    texts = [x["text"] for x in sc["sentences"]]
    assert texts == ["Send Help is Sam Raimi's thriller.", "It opens January 30, 2026."]
    assert sc["sentences"][1]["original"] == "It opens in March."
    assert "dropped 1" in summary and "fixed 1" in summary
    assert tmdb.ATTRIBUTION in sc["description"] and "https://d" in sc["description"]
    assert "Only the storyboard" not in calls[0] and "[R1] Opens Jan 30." in calls[0]  # research is in the board


def _tone(dest, seconds=1.2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", str(dest)], check=True)
    return dest


def _shots(video, n=6):
    return [{"id": f"s{i:03d}", "file": str(video), "start": i * 0.5, "end": i * 0.5 + 0.5, "usable": True,
             "description": f"shot {i}", "people": [], "setting": "x", "mood": "tense", "size": "wide",
             "source_type": "Trailer", "card": False} for i in range(1, n + 1)]


def test_validate_repairs_unknown_and_repeated_picks():
    slots = [{"slot": i} for i in (1, 2, 3)]
    cat = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    out, repaired = stage_plan._validate([{"slot": 1, "shot": "a"}, {"slot": 2, "shot": "a"},
                                          {"slot": 3, "shot": "zzz", "mode": "clip"}], slots, cat)
    assert [o["shot"] for o in out] == ["a", "b", "c"] and repaired == 2 and out[2]["mode"] == "clip"


def test_plan_voices_sentences_and_places_poster_on_release_line(monkeypatch, three_scene_video):
    monkeypatch.setattr(tts, "synth", lambda text, dest: _tone(dest))
    monkeypatch.setattr(gemini, "ask_json", lambda prompt, **kw: [{"slot": 1, "shot": "nope"}])
    pid = _new_project(facts=_facts(), shots=_shots(three_scene_video),
                       script={"sentences": [{"paragraph": 1, "text": "Send Help is here."},
                                             {"paragraph": 2, "text": "It hits theaters January 30."},
                                             {"paragraph": 3, "text": "Subscribe for more."}]})
    runner.run_one(pid, "plan")
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        plan, tl = p.plan, p.script["timeline"]
    assert plan[0]["kind"] == "poster"                       # no title card in the shots -> poster opens
    date_slot = next(i for i in plan if i["sentence"] == 2)
    assert date_slot["kind"] == "poster"                     # release-date line shows the poster card
    assert all(i.get("shot") for i in plan if i["kind"] in ("still", "clip"))
    assert tl[1]["start"] > tl[0]["start"] and abs(plan[-1]["end"] - tl[-1]["end"]) < 1e-6


def test_tts_requires_key_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(tts.settings, "tts_api_key", "")
    with pytest.raises(tts.TTSError, match="TTS_API_KEY"):
        tts.synth("A brand new sentence nobody voiced.", tmp_path / "a.mp3")
    assert tts.speakable('He said "run" — what the fuck') == "He said run — what the hell"


def test_render_produces_1080p_video_with_narration(monkeypatch, three_scene_video):
    pid = _new_project(facts=_facts(), shots=_shots(three_scene_video, 3))
    root = runner.project_dir(pid)
    (root / "tts").mkdir(exist_ok=True)
    for sh in _shots(three_scene_video, 3):
        media.frame(three_scene_video, sh["start"], root / f"{sh['id']}.jpg")
    shots = [{**sh, "still": f"{sh['id']}.jpg"} for sh in _shots(three_scene_video, 3)]
    _tone(root / "tts" / "a.mp3", 1.0)
    plan = [{"slot": 1, "sentence": 1, "kind": "poster", "start": 0, "end": 1.0, "duration": 1.0},
            {"slot": 2, "sentence": 1, "kind": "still", "shot": "s001", "start": 1.0, "end": 2.0, "duration": 1.0},
            {"slot": 3, "sentence": 1, "kind": "clip", "shot": "s002", "start": 2.0, "end": 3.0, "duration": 1.0,
             "clip_start": 2.1, "clip_len": 1.0}]
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        p.shots, p.plan = shots, plan
        p.script = {"timeline": [{"sentence": 1, "audio": "tts/a.mp3", "start": 0.0, "end": 3.0}]}
        s.commit()
    monkeypatch.setattr(stage_render, "END_CARD", 1.0)
    runner.run_one(pid, "render")
    with SessionLocal() as s:
        r = s.get(StudioProject, pid).render
    final = root / r["file"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration",
                            "-of", "compact", str(final)], capture_output=True, text=True).stdout
    assert "width=1920" in probe and "height=1080" in probe and "codec_type=audio" in probe
    assert 3.8 <= r["seconds"] <= 4.3 and (root / r["thumbnail"]).exists()


def test_publish_sends_render_to_queue_once(client):
    from backend.app.models import QueueItem
    pid = _new_project(script={"youtube_title": "Send Help Trailer Breakdown", "description": "d",
                               "tags": ["send help", "sam raimi"]})
    root = runner.project_dir(pid)
    (root / "render").mkdir(exist_ok=True)
    (root / "render" / "final.mp4").write_bytes(b"x")
    with SessionLocal() as s:
        s.get(StudioProject, pid).render = {"file": "render/final.mp4", "thumbnail": "render/thumbnail.jpg"}
        s.commit()
    a = client.post(f"/api/studio/projects/{pid}/publish").json()
    b = client.post(f"/api/studio/projects/{pid}/publish").json()
    assert a["queue_item_id"] == b["queue_item_id"] and a["status"] == "review"
    with SessionLocal() as s:
        it = s.get(QueueItem, a["queue_item_id"])
        assert it.pipeline == "LongForm" and it.tags == "#sendhelp #samraimi" and it.accounts == []


def test_publish_requires_a_render(client):
    pid = _new_project()
    assert client.post(f"/api/studio/projects/{pid}/publish").status_code == 400


def test_imdb_check_endpoint(client, monkeypatch):
    monkeypatch.setattr(imdb, "list_videos", lambda i: [{"id": "vi1", "type": "Trailer"}])
    monkeypatch.setattr(imdb, "mp4_url", lambda v: "https://cdn/x.mp4")
    d = client.get("/api/studio/imdb-videos", params={"imdb_id": "tt1"}).json()
    assert d["first_has_mp4"] is True and d["videos"][0]["id"] == "vi1"
