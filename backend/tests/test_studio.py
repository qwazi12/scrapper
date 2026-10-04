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
    from backend.app.studio import auto
    auto.save_settings({"movies_per_day": 20, "tv_per_day": 20})  # these tests make many projects a day
    from backend.app.db import SessionLocal as _S
    from backend.app.models import AppSetting as _A
    with _S() as s:                       # and reuse tmdb ids: clear the never-repeat ledger
        row = s.get(_A, "studio_made")
        if row:
            s.delete(row)
            s.commit()
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
    monkeypatch.setattr(stage_render, "motion_mode", lambda: "off")
    runner.run_one(pid, "render")
    with SessionLocal() as s:
        r = s.get(StudioProject, pid).render
    final = root / r["file"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration",
                            "-of", "compact", str(final)], capture_output=True, text=True).stdout
    assert "width=1920" in probe and "height=1080" in probe and "codec_type=audio" in probe
    assert 3.8 <= r["seconds"] <= 4.3 and (root / r["thumbnail"]).exists()
    frames = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                             "frame=pix_fmt,color_range", "-of", "csv=p=0", str(final)], capture_output=True, text=True).stdout
    assert {f.strip(",") for f in frames.split()} == {"yuv420p,tv"}   # poster stills must not switch format mid-stream


def test_publish_sends_render_to_queue_once(client, monkeypatch):
    from backend.app.models import QueueItem
    from backend.app.studio import drive_store
    started = []
    monkeypatch.setattr(drive_store, "start", lambda pid, title: started.append(pid) or "job1")  # never real Drive
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
    assert a["drive_job_id"] == "job1" and started == [pid, pid]  # not saved yet, so each publish retries Drive


def test_publish_requires_a_render(client):
    pid = _new_project()
    assert client.post(f"/api/studio/projects/{pid}/publish").status_code == 400


def test_imdb_check_endpoint(client, monkeypatch):
    monkeypatch.setattr(imdb, "list_videos", lambda i: [{"id": "vi1", "type": "Trailer"}])
    monkeypatch.setattr(imdb, "mp4_url", lambda v: "https://cdn/x.mp4")
    d = client.get("/api/studio/imdb-videos", params={"imdb_id": "tt1"}).json()
    assert d["first_has_mp4"] is True and d["videos"][0]["id"] == "vi1"


# --- source quality --------------------------------------------------------------
from backend.app.studio import sources as srcq


@pytest.mark.parametrize("url,tier", [
    ("https://www.deadline.com/2026/send-help", "trusted"), ("https://en.wikipedia.org/wiki/Send_Help", "trusted"),
    ("https://www.20thcenturystudios.com/movies/send-help", "official"), ("https://www.reddit.com/r/movies", "low"),
    ("https://unobtainium13.com/x", "other"),
])
def test_source_tiers(url, tier):
    assert srcq.tier(url) == tier


def test_storyboard_keeps_only_findings_backed_by_trusted_sources():
    research = {"sources": [{"title": "reddit.com", "url": "https://reddit.com/x", "tier": "low"},
                            {"title": "variety.com", "url": "https://variety.com/y", "tier": "trusted"}],
                "claims": [{"text": "Rumour: a sequel is planned.", "sources": [0]},
                           {"text": "It opens January 30.", "sources": [0, 1]}]}
    b = stage_script.storyboard(_facts(), research, [])
    assert [r["text"] for r in b["R"]] == ["It opens January 30."] and b["skipped_findings"] == 1
    assert [s["title"] for s in b["sources"]] == ["variety.com"]  # only good sources go in the description


def test_redirect_links_are_resolved(monkeypatch):
    class R:
        headers = {"location": "https://variety.com/real"}
    monkeypatch.setattr(srcq.httpx, "head", lambda url, **kw: R())
    out = srcq.annotate({"sources": [{"title": "variety.com",
                                      "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"}]})
    s = out["sources"][0]
    assert s["url"] == "https://variety.com/real" and s["tier"] == "trusted" and s["domain"] == "variety.com"


# --- LongForm → Drive -----------------------------------------------------------
class _SharedDriveSvc:
    """A Drive client stub whose folder lives in a Shared Drive."""
    def files(self):
        class F:
            def get(self, **kw):
                class R:
                    def execute(self):
                        return {"name": "LongForm Studio", "driveId": "D"}
                return R()
        return F()


def test_drive_quota_error_is_explained():
    from backend.app.studio import drive_store
    msg = drive_store._explain(Exception("<HttpError 403 ... storageQuotaExceeded: Service Accounts do not have storage quota>"))
    assert "Shared Drive" in msg and "LONGFORM_DRIVE_FOLDER_ID" in msg


def test_drive_save_uploads_and_links_queue_item(session, monkeypatch, tmp_path):
    from backend.app import drive_sync
    from backend.app.models import QueueItem, StudioProject
    from backend.app.studio import drive_store, runner as srunner

    monkeypatch.setattr(srunner, "project_dir", lambda pid: tmp_path)
    (tmp_path / "final.mp4").write_bytes(b"x" * 10)
    (tmp_path / "thumb.jpg").write_bytes(b"y")
    item = QueueItem(title="t", status="review", pipeline="LongForm", accounts=[])
    session.add(item)
    session.flush()
    p = StudioProject(tmdb_id=1, title="Send Help", render={"file": "final.mp4", "thumbnail": "thumb.jpg",
                                                             "rendered_at": "r1"}, queue_item_id=item.id)
    session.add(p)
    session.commit()
    uploaded = []
    monkeypatch.setattr(drive_sync, "get_drive_service", lambda: _SharedDriveSvc())
    monkeypatch.setattr(drive_store, "folder_id", lambda svc: "FOLDER")
    monkeypatch.setattr(drive_store, "_upload", lambda svc, path, parent, name: (
        uploaded.append((parent, name)) or {"id": f"id{len(uploaded)}", "webViewLink": f"https://drive/{len(uploaded)}"}))
    info = drive_store.save(p.id)
    session.expire_all()
    assert info["status"] == "saved" and info["rendered_at"] == "r1" and len(uploaded) == 2
    assert all(parent == "FOLDER" for parent, _ in uploaded) and uploaded[0][1].startswith("Send Help — Trailer Breakdown")
    assert session.get(QueueItem, item.id).drive_link == "https://drive/1"


def test_drive_save_failure_keeps_reason(session, monkeypatch, tmp_path):
    from backend.app import drive_sync
    from backend.app.models import StudioProject
    from backend.app.studio import drive_store, runner as srunner

    monkeypatch.setattr(srunner, "project_dir", lambda pid: tmp_path)
    (tmp_path / "final.mp4").write_bytes(b"x")
    p = StudioProject(tmdb_id=1, title="X", render={"file": "final.mp4"})
    session.add(p)
    session.commit()
    monkeypatch.setattr(drive_sync, "get_drive_service", lambda: _SharedDriveSvc())
    monkeypatch.setattr(drive_store, "folder_id", lambda svc: "F")

    def boom(*a):
        raise Exception("storageQuotaExceeded")

    monkeypatch.setattr(drive_store, "_upload", boom)
    with pytest.raises(drive_store.DriveStoreError):
        drive_store.save(p.id)
    session.expire_all()
    d = session.get(StudioProject, p.id).drive
    assert d["status"] == "error" and "Shared Drive" in d["error"]


# --- upcoming calendar ------------------------------------------------------------
def test_upcoming_is_new_films_in_the_next_three_months(monkeypatch):
    import datetime as dt
    calls = []
    us = {  # TMDB per-country release dates (type 2 limited, 3 theatrical, 4 digital)
        1: [(1, "2026-09-01"), (3, "2026-10-07")],
        2: [(1, "2026-09-05"), (2, "2026-11-14"), (3, "2026-11-21")],          # festival, then limited
        3: [(3, "2026-11-01")],
        4: [(3, "2026-12-20")],
        5: [(3, "2026-09-12"), (4, "2026-10-20")],                              # already out; digital in window
        6: [(1, "2026-10-10")],                                                  # premiere only: not listed
    }

    def fake_get(path, **params):
        calls.append((path, params))
        if path == "/discover/movie":
            if params["release_date.gte"] != "2026-10-02":
                return {"total_pages": 1, "results": []}
            return {"total_pages": 1, "results": [
                {"id": 1, "title": "Vampire Carnival", "release_date": "2026-09-01", "poster_path": "/a.jpg", "popularity": 50},
                {"id": 2, "title": "Festival Darling", "release_date": "2026-09-05", "poster_path": "/b.jpg", "popularity": 80},
                {"id": 3, "title": "No Poster", "release_date": "2026-11-01", "poster_path": None, "popularity": 10},
                {"id": 4, "title": "Holiday Film", "release_date": "2026-12-20", "poster_path": "/d.jpg", "popularity": 30},
                {"id": 5, "title": "Streaming Now", "release_date": "2026-09-12", "poster_path": "/e.jpg", "popularity": 20},
                {"id": 6, "title": "Premiere Only", "release_date": "2026-10-10", "poster_path": "/f.jpg", "popularity": 5},
            ]}
        mid = int(path.split("/")[2])
        return {"results": [{"iso_3166_1": "GB", "release_dates": [{"type": 3, "release_date": "2026-10-03T00:00:00.000Z"}]},
                            {"iso_3166_1": "US", "release_dates": [
                                {"type": t, "release_date": f"{d}T00:00:00.000Z"} for t, d in us[mid]]}]}

    monkeypatch.setattr(tmdb, "get", fake_get)
    out = tmdb.upcoming_movies("US", today=dt.date(2026, 10, 2))
    assert [(m["title"], m["date"], m["release"], m["in_theaters_since"]) for m in out] == [
        ("Vampire Carnival", "2026-10-07", "theaters", None),
        ("Streaming Now", "2026-10-20", "digital", "2026-09-12"),
        ("Festival Darling", "2026-11-14", "limited", None),
        ("Holiday Film", "2026-12-20", "theaters", None)]
    windows = [(c[1]["release_date.gte"], c[1]["release_date.lte"]) for c in calls if c[0] == "/discover/movie"]
    assert windows == [("2026-10-02", "2026-10-31"), ("2026-11-01", "2026-11-30"),
                       ("2026-12-01", "2026-12-31"), ("2027-01-01", "2027-01-02")]
    p = calls[0][1]
    assert p["primary_release_date.gte"] == "2026-04-02" and p["region"] == "US"


# --- motion layer (HyperFrames) -------------------------------------------------------
def _render_project(three_scene_video):
    pid = _new_project(facts=_facts(), shots=_shots(three_scene_video, 3))
    root = runner.project_dir(pid)
    (root / "tts").mkdir(exist_ok=True)
    shots = []
    for sh in _shots(three_scene_video, 3):
        media.frame(three_scene_video, sh["start"], root / f"{sh['id']}.jpg")
        shots.append({**sh, "still": f"{sh['id']}.jpg"})
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
    return pid, root


def test_compare_mode_makes_both_versions_and_survives_motion_failure(monkeypatch, three_scene_video):
    from backend.app.studio import motion
    pid, root = _render_project(three_scene_video)
    monkeypatch.setattr(stage_render, "END_CARD", 1.0)
    monkeypatch.setattr(stage_render, "motion_mode", lambda: "compare")

    def boom(kind, values, assets=None):
        raise motion.MotionError(f"{kind}: HyperFrames render failed — no chrome")

    monkeypatch.setattr(motion, "render_piece", boom)
    runner.run_one(pid, "render")
    with SessionLocal() as s:
        r = s.get(StudioProject, pid).render
    assert r["file"].endswith("final.mp4") and r["motion_mode"] == "compare"
    m = r["motion"]
    assert m["file"].endswith("final_motion.mp4") and (root / m["file"]).exists()
    assert m["pieces"] == 0 and m["failures"]                    # every piece fell back, video still made
    assert 3.8 <= media.duration(root / m["file"]) <= 4.3


def test_motion_overlays_are_composited(monkeypatch, three_scene_video, tmp_path):
    from backend.app.studio import motion
    pid, root = _render_project(three_scene_video)
    monkeypatch.setattr(stage_render, "END_CARD", 1.0)
    monkeypatch.setattr(stage_render, "motion_mode", lambda: "on")
    used = []

    def fake(kind, values, assets=None):
        used.append(kind)
        dest = tmp_path / f"{kind}.{'mp4' if kind == 'endscreen' else 'mov'}"
        if kind == "endscreen":
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=1920x1080:d=1:r=30",
                            "-pix_fmt", "yuv420p", str(dest)], check=True)
        else:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white@0.5:s=1920x1080:d=1:r=30,format=yuva444p10le",
                            "-c:v", "prores_ks", "-profile:v", "4444", str(dest)], check=True)
        return dest

    monkeypatch.setattr(motion, "render_piece", fake)
    runner.run_one(pid, "render")
    with SessionLocal() as s:
        r = s.get(StudioProject, pid).render
    assert r["file"].endswith("final.mp4") and r["motion"]["file"].endswith("final.mp4")
    assert "intro" in used and "release" in used and "endscreen" in used and not r["motion"]["failures"]
    assert not (root / "render" / "final_static.mp4").exists()
    assert 3.8 <= r["seconds"] <= 4.3


def test_cast_cards_only_on_solo_shots_of_that_actor_and_max_four():
    from backend.app.studio import motion
    shots = {"a": {"people": ["Rachel McAdams"]}, "b": {"people": ["Dylan O'Brien", "Rachel McAdams"]},
             "c": {"people": ["Dylan O'Brien"]}, "d": {"people": ["X"]}}
    plan = [{"kind": "clip", "shot": "b", "start": 0, "duration": 4},
            {"kind": "clip", "shot": "a", "start": 4, "duration": 4},
            {"kind": "still", "shot": "c", "start": 8, "duration": 4},
            {"kind": "still", "shot": "d", "start": 12, "duration": 4}]
    cast = [{"actor": "Rachel McAdams", "character": "Linda"}, {"actor": "Dylan O'Brien", "character": "Bradley"},
            {"actor": "X", "character": "Y"}]
    cards = motion.cast_cards(plan, shots, cast, 2, [])
    assert [(c["actor"], c["start"]) for c in cards] == [("Rachel McAdams", 4.2), ("Dylan O'Brien", 8.2)]
    assert motion.cast_cards(plan, shots, cast, 2, [(8.0, 12.0)])[1:] == []      # never over another overlay
    assert len(motion.cast_cards(plan, shots, cast * 3, 9, [])) <= 4


def test_subscribe_window_moves_off_other_overlays():
    fw = stage_render._free_window
    assert fw(18.0, 5.0, [(20.0, 24.0)], 200) == (24.5, 29.5)
    assert fw(18.0, 5.0, [(30.0, 34.0)], 200) == (18.0, 23.0)
    assert fw(18.0, 5.0, [(17.0, 30.0)], 33) is None          # no room before the closing line


def test_motion_settings_validate_and_default_to_compare(client):
    r = client.get("/api/studio/motion").json()
    assert r["mode"] in ("compare", "on", "off") and 0 <= r["cast_cards"] <= 4
    assert client.put("/api/studio/motion", json={"mode": "sometimes"}).status_code == 400
    assert client.put("/api/studio/motion", json={"cast_cards": 5}).status_code == 400
    r = client.put("/api/studio/motion", json={"mode": "on", "cast_cards": 2}).json()
    assert r["mode"] == "on" and r["cast_cards"] == 2
    assert stage_render.motion_mode() == "on" and stage_render.motion_cast_cards() == 2
    client.put("/api/studio/motion", json={"mode": "compare"})


def test_drive_names_the_real_problem_for_a_my_drive_folder(session, monkeypatch, tmp_path):
    """Google refuses files from a robot account in a My Drive folder (verified on the
    live account 2026-10-02/03). The upload must not be attempted; the message says why."""
    from backend.app import drive_sync
    from backend.app.models import StudioProject
    from backend.app.studio import drive_store, runner as srunner

    monkeypatch.setattr(srunner, "project_dir", lambda pid: tmp_path)
    (tmp_path / "final.mp4").write_bytes(b"x")
    p = StudioProject(tmdb_id=1, title="X", render={"file": "final.mp4"})
    session.add(p)
    session.commit()
    uploads = []

    class Svc:
        def files(self):
            class F:
                def get(self, **kw):
                    return type("R", (), {"execute": lambda self: {"name": "LongForm Studio", "driveId": None}})()

                def create(self, **kw):
                    uploads.append(kw)
            return F()

        def drives(self):
            return type("D", (), {"list": lambda self, **kw: type("R", (), {"execute": lambda self: {"drives": []}})()})()

    monkeypatch.setattr(drive_sync, "get_drive_service", lambda: Svc())
    monkeypatch.setattr(drive_store, "folder_id", lambda svc: "F")
    with pytest.raises(drive_store.DriveStoreError, match="member of 0 Shared Drive"):
        drive_store.save(p.id)
    assert uploads == []


def test_drive_uploads_to_shared_drive_folder(session, monkeypatch, tmp_path):
    from backend.app import drive_sync
    from backend.app.models import StudioProject
    from backend.app.studio import drive_store, runner as srunner

    monkeypatch.setattr(srunner, "project_dir", lambda pid: tmp_path)
    (tmp_path / "final.mp4").write_bytes(b"x")
    p = StudioProject(tmdb_id=1, title="X", render={"file": "final.mp4"})
    session.add(p)
    session.commit()

    class Svc:
        def files(self):
            class F:
                def get(self, **kw):
                    return type("R", (), {"execute": lambda self: {"name": "LongForm Studio", "driveId": "SD1"}})()

                def create(self, **kw):
                    done = (None, {"id": "uploaded_123", "webViewLink": "https://drive.google.com/file/d/uploaded_123"})
                    return type("R", (), {"next_chunk": lambda self: done})()
            return F()

    monkeypatch.setattr(drive_sync, "get_drive_service", lambda: Svc())
    monkeypatch.setattr(drive_store, "folder_id", lambda svc: "F")
    result = drive_store.save(p.id)
    assert result["link"] == "https://drive.google.com/file/d/uploaded_123"


def test_select_and_generate_three_thumbnails(session, tmp_path, monkeypatch):
    from backend.app.models import StudioProject
    from backend.app.studio import stage_render, runner as srunner

    monkeypatch.setattr(stage_render, "project_dir", lambda pid: tmp_path)
    p = StudioProject(tmdb_id=2, title="Thumbnail Test", render={"file": "render/final.mp4"})
    session.add(p)
    session.commit()

    rdir = tmp_path / "render"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / "thumbnail_poster.jpg").write_bytes(b"poster_data")
    (rdir / "thumbnail_shot1.jpg").write_bytes(b"shot1_data")
    (rdir / "thumbnail_shot2.jpg").write_bytes(b"shot2_data")

    # Select poster
    res = stage_render.select_project_thumbnail(p.id, "poster")
    assert res["selected_thumbnail"] == "poster"
    assert (rdir / "thumbnail.jpg").read_bytes() == b"poster_data"

    # Select shot2
    res2 = stage_render.select_project_thumbnail(p.id, "shot2")
    assert res2["selected_thumbnail"] == "shot2"
    assert (rdir / "thumbnail.jpg").read_bytes() == b"shot2_data"


def test_stopped_rerender_keeps_the_previous_video(monkeypatch, three_scene_video):
    from backend.app import control
    pid, root = _render_project(three_scene_video)
    monkeypatch.setattr(stage_render, "END_CARD", 1.0)
    monkeypatch.setattr(stage_render, "motion_mode", lambda: "off")
    runner.run_one(pid, "render")
    with SessionLocal() as s:
        first = s.get(StudioProject, pid).render
    assert (root / first["file"]).exists() and first["file"] == "render/final.mp4"

    def stop_midway(*a, **kw):
        raise control.Cancelled()

    import shutil as _sh
    _sh.rmtree(root / "segcache")                                  # force real work (no cache hits)
    monkeypatch.setattr(stage_render, "seg_clip", stop_midway)   # the re-render is stopped partway
    with pytest.raises(control.Cancelled):
        runner.run_one(pid, "render")
    with SessionLocal() as s:
        assert s.get(StudioProject, pid).render == first
    assert (root / first["file"]).exists()                         # the last good video survived


def test_restart_marks_interrupted_steps_stopped():
    pid = _new_project()
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        p.stage, p.stage_status = "shots", "running"
        s.commit()
    assert runner.recover_interrupted() >= 1
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.stage_status == "stopped" and "restart" in p.stage_message


def test_owner_can_use_an_unusable_shot_and_undo_it(client):
    pid = _new_project(shots=[{"id": "s001", "usable": False, "card": True, "thumb": "t.jpg", "still": "s.jpg"},
                              {"id": "s002", "usable": True, "thumb": "t2.jpg", "still": "s2.jpg"}])
    r = client.put(f"/api/studio/projects/{pid}/shots/s001", json={"usable": True})
    assert r.status_code == 200
    sh = next(x for x in r.json()["shots"] if x["id"] == "s001")
    assert sh["usable"] is True and sh["auto_usable"] is False and sh["owner_set"] is True
    assert client.post("/api/undo", json={"scope": f"studio:{pid}"}).status_code == 200
    with SessionLocal() as s:
        assert next(x for x in s.get(StudioProject, pid).shots if x["id"] == "s001")["usable"] is False


# --- plan QA: no repeats, no look-alikes back to back ---------------------------------
def test_lookalike_shots_are_grouped(tmp_path):
    from PIL import Image, ImageDraw
    def frame(name, x):
        im = Image.new("RGB", (320, 180), (40, 40, 40))
        ImageDraw.Draw(im).rectangle((x, 40, x + 80, 140), fill=(230, 200, 160))
        im.save(tmp_path / name)
    frame("a.jpg", 100); frame("b.jpg", 102)          # same interview framing, a hair apart
    frame("c.jpg", 230)                                # a different picture
    shots = [{"id": "s1", "still": "a.jpg"}, {"id": "s2", "still": "b.jpg"}, {"id": "s3", "still": "c.jpg"}]
    g = stage_plan.look_groups(shots, tmp_path)
    assert g["s1"] == g["s2"] == "s1" and g["s3"] == "s3"
    # Same interview set-up a few seconds apart, the person moved: grouped only as the same set-up.
    frame("d.jpg", 120)
    near = [{"id": "s7", "still": "a.jpg", "source": "v1", "start": 43.7, "people": ["Lead"]},
            {"id": "s8", "still": "d.jpg", "source": "v1", "start": 51.8, "people": ["Lead"]},
            {"id": "s9", "still": "d.jpg", "source": "v2", "start": 51.8, "people": ["Lead"]}]
    bits = bin(stage_plan.dhash(tmp_path / "a.jpg") ^ stage_plan.dhash(tmp_path / "d.jpg")).count("1")
    assert stage_plan.LOOK_ALIKE < bits <= stage_plan.SAME_SETUP
    assert stage_plan.look_groups(near[:2], tmp_path)["s8"] == "s7"            # same set-up -> one look
    assert stage_plan.look_groups([near[0], near[2]], tmp_path)["s9"] == "s9"  # same distance, other video -> separate


def test_validate_refuses_repeats_and_lookalikes_while_fresh_shots_remain():
    catalog = [{"id": f"s{i}", "people": (["Lead"] if i in (2, 5) else [])} for i in range(1, 9)]
    groups = {c["id"]: c["id"] for c in catalog}
    groups["s4"] = "s3"                                # s3 and s4 look identical
    slots = [{"slot": n} for n in range(1, 7)]
    picks = [{"slot": 1, "shot": "s3"}, {"slot": 2, "shot": "s4"},      # look-alike back to back
             {"slot": 3, "shot": "s2"}, {"slot": 4, "shot": "s2"},      # true repeat
             {"slot": 5, "shot": "s1"}, {"slot": 6, "shot": "s3"}]      # repeat within 6 slots
    out, repaired = stage_plan._validate(picks, slots, catalog, groups)
    looks = [groups[o["shot"]] for o in out]
    assert len(set(looks)) == 6 and repaired == 4     # slot 2 (look-alike) takes s1, so slot 5's s1 is a repeat too
    assert out[3]["shot"] == "s5"                      # replacement keeps the same person on screen
    assert stage_plan.plan_issues([{"slot": o["slot"], "shot": o["shot"], "kind": "still"} for o in out], groups) == []


def test_plan_issues_flags_back_to_back_and_near_repeats():
    groups = {"a": "a", "b": "a", "c": "c"}
    items = [{"slot": 1, "shot": "a", "kind": "still"}, {"slot": 2, "shot": "b", "kind": "still"},
             {"slot": 3, "shot": "c", "kind": "clip"}, {"slot": 4, "shot": "c", "kind": "still"}]
    kinds = [(i["slot"], i["kind"]) for i in stage_plan.plan_issues(items, groups)]
    assert kinds == [(2, "back_to_back"), (4, "back_to_back")]


def test_rerender_reuses_unchanged_segments(monkeypatch, three_scene_video):
    pid, root = _render_project(three_scene_video)
    monkeypatch.setattr(stage_render, "END_CARD", 1.0)
    monkeypatch.setattr(stage_render, "motion_mode", lambda: "off")
    first = runner.run_one(pid, "render")
    assert "0 reused" in first
    calls = []
    real = stage_render.seg_still
    monkeypatch.setattr(stage_render, "seg_still", lambda *a, **k: (calls.append(a), real(*a, **k)))
    second = runner.run_one(pid, "render")
    assert "0 rendered" in second and calls == []                 # nothing changed -> nothing re-encoded
    with SessionLocal() as s:
        assert (root / s.get(StudioProject, pid).render["file"]).exists()


# --- 14-day archive -------------------------------------------------------------------
def _archivable(posted_days_ago: float, drive_ok: bool = True, origin: str = "imdb"):
    import datetime as dt
    from backend.app.models import QueueItem
    now = dt.datetime.now(dt.timezone.utc)
    with SessionLocal() as s:
        it = QueueItem(title="t", status="posted", pipeline="LongForm", accounts=[],
                       published_at=now - dt.timedelta(days=posted_days_ago))
        s.add(it); s.commit(); qid = it.id
    pid = _new_project()
    root = runner.project_dir(pid)
    for d in ("footage", "stills", "render", "segcache", "thumbs"):
        (root / d).mkdir(exist_ok=True)
        (root / d / "x.bin").write_bytes(b"0" * 300_000)
    (root / "render" / "thumbnail.jpg").write_bytes(b"jpg")
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        p.queue_item_id = qid
        p.render = {"file": "render/final.mp4", "thumbnail": "render/thumbnail.jpg", "rendered_at": "r1"}
        p.drive = {"status": "saved", "rendered_at": "r1" if drive_ok else "r0", "link": "https://drive/x"}
        p.trailer = {"origin": origin, "sources": [{"id": "vi1", "origin": origin, "file": str(root / "footage" / "x.bin")}]}
        p.plan = [{"slot": 1}]
        s.commit()
    return pid, root


def test_archive_only_after_14_days_with_a_current_drive_copy():
    from backend.app.studio import archive
    young, _ = _archivable(3)
    stale_drive, _ = _archivable(30, drive_ok=False)
    old, root = _archivable(15)
    rows = {r["id"]: r for r in archive.preview()["projects"]}
    assert not rows[young]["eligible"] and "3 day" in rows[young]["reason"]
    assert not rows[stale_drive]["eligible"] and "older render" in rows[stale_drive]["reason"]
    assert rows[old]["eligible"] and rows[old]["frees_mb"] > 0
    out = archive.archive_project(old)
    assert out["archived"] is True
    assert not (root / "footage").exists() and not (root / "stills").exists() and not (root / "render").exists()
    assert (root / "thumbs").exists() and (root / "thumbnail.jpg").exists()     # kept
    with SessionLocal() as s:
        p = s.get(StudioProject, old)
        assert p.archive["archived_at"] and p.plan == [{"slot": 1}]            # script/plan/metadata kept
    import pytest as _pt
    with _pt.raises(RuntimeError, match="archived"):
        runner.run_one(old, "render")                                          # must restore first


def test_uploaded_footage_is_never_deleted():
    from backend.app.studio import archive
    pid, root = _archivable(20, origin="upload")
    assert archive.archive_project(pid)["archived"]
    assert (root / "footage" / "x.bin").exists() and not (root / "stills").exists()


def test_posted_date_survives_the_4_day_queue_cleanup(monkeypatch):
    import datetime as dt
    from backend.app.models import QueueItem
    from backend.app.social import queue_manager as qmm
    from backend.app.studio import archive
    pid, root = _archivable(5)
    monkeypatch.setattr(qmm.settings, "archive_delete_days", 4)
    with SessionLocal() as s:
        qmm.sweep_archive(s, dt.datetime.now(dt.timezone.utc))
        p = s.get(StudioProject, pid)
        assert s.get(QueueItem, p.queue_item_id) is None                         # the row is gone…
        assert p.archive["posted_at"]                                            # …but the project remembers


def test_restore_rebuilds_stills_without_ai(monkeypatch, three_scene_video):
    import shutil as _sh
    from backend.app.studio import archive, imdb
    pid = _new_project(shots=[{**sh, "still": f"stills/{sh['id']}.jpg"} for sh in _shots(three_scene_video, 2)])
    root = runner.project_dir(pid)
    dest = root / "footage" / "vi9.mp4"
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        p.trailer = {"origin": "imdb", "sources": [{"id": "vi9", "origin": "imdb", "file": str(dest)}]}
        p.shots = [{**sh, "file": str(dest)} for sh in p.shots]
        p.archive = {"archived_at": "2026-10-01T00:00:00+00:00"}
        s.commit()
    monkeypatch.setattr(imdb, "download", lambda vid, d: (pathlib.Path(d).parent.mkdir(exist_ok=True), _sh.copy(three_scene_video, d)))
    archive.restore(pid)
    assert dest.exists() and all((root / f"stills/s00{i}.jpg").exists() for i in (1, 2))
    with SessionLocal() as s:
        a = s.get(StudioProject, pid).archive
        assert a["archived_at"] is None and a["restored_at"]


def test_thumbnail_refresh_keeps_newer_render_info_and_refuses_while_running(session, tmp_path, monkeypatch):
    from backend.app.models import StudioProject
    from backend.app.studio import stage_render

    monkeypatch.setattr(stage_render, "project_dir", lambda pid: tmp_path)
    p = StudioProject(tmdb_id=3, title="Merge Test", render={"file": "render/final.mp4", "rendered_at": "old"})
    session.add(p)
    session.commit()

    real_thumbnail = stage_render.thumbnail

    def thumb_then_render_finishes(still, poster, title, dest):
        # A render lands while the options are being drawn.
        with stage_render.SessionLocal() as s:
            row = s.get(StudioProject, p.id)
            row.render = {**(row.render or {}), "rendered_at": "new", "seconds": 99}
            s.commit()
        return real_thumbnail(still, poster, title, dest)

    monkeypatch.setattr(stage_render, "thumbnail", thumb_then_render_finishes)
    out = stage_render.generate_thumbnails_for_project(p.id)
    assert out["rendered_at"] == "new" and out["seconds"] == 99
    assert out["thumbnails_updated_at"]

    session.refresh(p)
    p.stage_status = "running"
    session.commit()
    with pytest.raises(RuntimeError, match="running"):
        stage_render.generate_thumbnails_for_project(p.id)
    with pytest.raises(RuntimeError, match="running"):
        stage_render.select_project_thumbnail(p.id, "poster")


def test_thumbnail_falls_back_to_poster_when_still_is_missing(tmp_path):
    from PIL import Image
    from backend.app.studio import stage_render

    poster = tmp_path / "poster.jpg"
    Image.new("RGB", (1280, 720), (200, 30, 30)).save(poster)
    out = stage_render.thumbnail(tmp_path / "missing.jpg", poster, "", tmp_path / "t.jpg")
    with Image.open(out) as im:
        r, g, b = im.convert("RGB").getpixel((640, 100))
    assert r > 150 and g < 80  # poster pixels, not a black canvas


def test_thumbnail_is_the_plain_picture(tmp_path):
    from PIL import Image
    from backend.app.studio import stage_render

    src = tmp_path / "still.jpg"
    Image.new("RGB", (1920, 1080), (20, 120, 220)).save(src)
    out = stage_render.thumbnail(src, None, "Some Title", tmp_path / "t.jpg")
    with Image.open(out) as im:
        im = im.convert("RGB")
        for xy in [(100, 600), (640, 650), (200, 430)]:   # where the band, title and badge used to be
            r, g, b = im.getpixel(xy)
            assert abs(r - 20) < 12 and abs(g - 120) < 12 and abs(b - 220) < 12, xy


def test_ask_json_retries_a_cut_off_answer_with_more_room(monkeypatch):
    from backend.app.studio import gemini
    sent = []
    answers = [
        {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"a": "cut'}]}}]},
        {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"a": 1}'}]}}]},
    ]

    def fake_post(body, model=None, retries=3):
        sent.append(body["generationConfig"]["maxOutputTokens"])
        return answers[len(sent) - 1]

    monkeypatch.setattr(gemini, "_post", fake_post)
    assert gemini.ask_json("x", max_tokens=1000) == {"a": 1}
    assert sent == [1000, 2000]


def test_ask_json_gives_up_with_the_reason(monkeypatch):
    from backend.app.studio import gemini
    monkeypatch.setattr(gemini, "_post", lambda body, model=None, retries=3: {
        "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "not json"}]}}]})
    with pytest.raises(gemini.GeminiError, match="finish reason STOP, attempt 3 of 3"):
        gemini.ask_json("x")


def test_scene_cuts_fails_loudly_when_ffmpeg_fails(monkeypatch, tmp_path):
    import subprocess
    from backend.app import control
    from backend.app.studio import media
    monkeypatch.setattr(control, "run", lambda cmd, timeout=0, text=True:
                        subprocess.CompletedProcess(cmd, -9, "", "Killed"))
    with pytest.raises(RuntimeError, match="scene detection failed"):
        media.scene_cuts(tmp_path / "x.mp4")


def test_scene_cuts_lowers_the_threshold_for_dark_footage(monkeypatch, tmp_path):
    import subprocess
    from backend.app import control
    from backend.app.studio import media

    def log(pairs):
        return "\n".join(f"[Parsed_metadata_1] frame:{i} pts:{int(t*1000)} pts_time:{t}\n"
                         f"[Parsed_metadata_1] lavfi.scene_score={sc}" for i, (t, sc) in enumerate(pairs))

    # bright trailer: plenty of strong cuts -> 0.3 is kept (weak changes ignored)
    bright = [(i * 2.0, 0.5) for i in range(1, 60)] + [(i * 2.0 + 1, 0.1) for i in range(1, 60)]
    monkeypatch.setattr(control, "run", lambda cmd, timeout=0, text=True: subprocess.CompletedProcess(cmd, 0, "", log(bright)))
    assert len(media.scene_cuts(tmp_path / "b.mp4")) == 59
    # dark trailer: 2 strong cuts in 2 min, many 0.15 ones -> steps down to 0.12
    dark = [(10.0, 0.4), (70.0, 0.4)] + [(i * 3.0 + 0.5, 0.15) for i in range(1, 40)]
    monkeypatch.setattr(control, "run", lambda cmd, timeout=0, text=True: subprocess.CompletedProcess(cmd, 0, "", log(dark)))
    assert len(media.scene_cuts(tmp_path / "d.mp4")) == 41


def test_refresh_options_rotates_to_different_shots(session, tmp_path, monkeypatch):
    from PIL import Image
    from backend.app.models import StudioProject
    from backend.app.studio import stage_render

    monkeypatch.setattr(stage_render, "project_dir", lambda pid: tmp_path)
    shots = []
    for i in range(4):
        f = tmp_path / f"st{i}.jpg"
        Image.new("RGB", (64, 36), (i * 60, 10, 10)).save(f)
        shots.append({"id": f"s{i}", "usable": True, "still": f.name, "size": "close", "people": ["Lead"]})
    p = StudioProject(tmdb_id=3, title="Rotate", facts={"cast": [{"actor": "Lead"}]}, shots=shots,
                      render={"file": "render/final.mp4"})
    session.add(p)
    session.commit()
    picks = []
    for _ in range(3):
        r = stage_render.generate_thumbnails_for_project(p.id)
        picks.append(r["thumb_shots"]["shot1"])
    assert picks == ["s0", "s1", "s2"]          # a new shot every refresh


def test_ask_json_batch_submits_polls_and_records_half_price(monkeypatch, tmp_path):
    import json as _json
    from backend.app import costs
    from backend.app.studio import gemini
    monkeypatch.setattr(gemini.settings, "gemini_api_key", "mock-key")
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    sent = {}

    class R:
        def __init__(self, data, ok=True):
            self._d, self.is_success, self.status_code, self.text = data, ok, 200 if ok else 500, _json.dumps(data)

        def json(self):
            return self._d

    def fake_post(url, content=None, headers=None, timeout=None, **kw):
        sent["url"], sent["body"] = url, _json.loads(content)
        return R({"name": "batches/abc"})

    states = iter(["JOB_STATE_PENDING", "JOB_STATE_SUCCEEDED"])
    usage = {"promptTokenCount": 1_000_000, "candidatesTokenCount": 0}

    def fake_get(url, headers=None, timeout=None):
        st = next(states)
        d = {"metadata": {"state": st}}
        if "SUCCEEDED" in st:
            d["response"] = {"inlinedResponses": {"inlinedResponses": [
                {"metadata": {"key": "1"}, "response": {"usageMetadata": usage, "candidates": [{"content": {"parts": [{"text": "[2]"}]}}]}},
                {"metadata": {"key": "0"}, "response": {"usageMetadata": usage, "candidates": [{"content": {"parts": [{"text": "[1]"}]}}]}},
            ]}}
        return R(d)

    monkeypatch.setattr(gemini.httpx, "post", fake_post)
    monkeypatch.setattr(gemini.httpx, "get", fake_get)
    with SessionLocal() as s:
        from backend.app.models import UsageEvent
        s.query(UsageEvent).delete()
        s.commit()
    out = gemini.ask_json_batch([{"key": "0", "prompt": "a"}, {"key": "1", "prompt": "b"}])
    assert out == {"0": [1], "1": [2]}
    assert sent["url"].endswith(":batchGenerateContent")
    assert [r["metadata"]["key"] for r in sent["body"]["batch"]["input_config"]["requests"]["requests"]] == ["0", "1"]
    d = costs.summary()
    assert d["by_service"]["gemini-batch"]["cost"] == pytest.approx(2 * 0.75 * 0.5)   # half price


def test_automation_shots_use_batch_and_fall_back(monkeypatch, three_scene_video):
    pid = _new_project(facts={"cast": [{"actor": "Rachel McAdams", "character": "Linda"}], "local": {"cast": {}}},
                       trailer={"file": str(three_scene_video), "sources": [
                           {"id": "vi1", "type": "Trailer", "file": str(three_scene_video)}]})
    with SessionLocal() as s:
        s.get(StudioProject, pid).review = {"auto": True}
        s.commit()

    def tags(n):
        return [{"i": k + 1, "description": f"shot {k + 1}", "people": ["Rachel McAdams"], "size": "wide",
                 "card": False, "text": False, "quality": "good"} for k in range(n)]

    normal = []
    monkeypatch.setattr(gemini, "ask_json", lambda prompt, images=None, **kw: normal.append(1) or tags(len(images)))
    monkeypatch.setattr(gemini, "ask_json_batch", lambda items, **kw: {it["key"]: tags(len(it["images"])) for it in items})
    summary = runner.run_one(pid, "shots")
    assert "batch (1/1 groups at half price)" in summary and normal == []
    with SessionLocal() as s:
        assert all(sh["usable"] for sh in s.get(StudioProject, pid).shots)

    def down(items, **kw):
        raise gemini.BatchUnavailable("refused")

    monkeypatch.setattr(gemini, "ask_json_batch", down)
    summary = runner.run_one(pid, "shots")
    assert "batch unavailable" in summary and normal == [1]                       # asked the normal way


def test_lookalike_neighbours_are_tagged_once(monkeypatch, three_scene_video):
    pid = _new_project(facts={"cast": [], "local": {"cast": {}}},
                       trailer={"file": str(three_scene_video), "sources": [
                           {"id": "vi1", "type": "Trailer", "file": str(three_scene_video)}]})
    # Pretend there's an extra cut inside the blue scene: two near-identical shots.
    monkeypatch.setattr(media, "scene_cuts", lambda f, threshold=None: [2.0, 3.0, 4.0])
    seen = []

    def fake_tag(prompt, images=None, **kw):
        seen.append(len(images))
        return [{"i": k + 1, "description": f"d{k}", "people": [], "size": "wide", "card": False,
                 "text": False, "quality": "good"} for k in range(len(images))]

    monkeypatch.setattr(gemini, "ask_json", fake_tag)
    summary = runner.run_one(pid, "shots")
    with SessionLocal() as s:
        shots = s.get(StudioProject, pid).shots
    assert len(shots) == 4 and "1 look-alikes reused" in summary
    assert shots[2]["tags_from"] == shots[1]["id"] and shots[2]["description"] == shots[1]["description"]


def test_only_cards_are_unusable_and_recompute_keeps_owner_choices(session):
    from backend.app.models import StudioProject
    from backend.app.studio import stage_shots
    shots = [
        {"id": "a", "description": "x", "card": True},
        {"id": "b", "description": "x", "text": True, "usable": False},          # watermark: now usable
        {"id": "c", "description": "x", "quality": "dark", "usable": False},     # dark: now usable
        {"id": "d", "description": "x", "usable": False, "owner_set": True},     # owner left it out
        {"id": "e", "tag_error": "boom", "usable": False},                       # never tagged
    ]
    assert [stage_shots.auto_usable(sh) for sh in shots] == [False, True, True, True, False]
    p = StudioProject(tmdb_id=77, title="Rule", shots=shots)
    session.add(p)
    session.commit()
    assert stage_shots.recompute_usable(p.id) == (0, 2)
    session.refresh(p)
    assert [sh["usable"] for sh in p.shots] == [False, True, True, False, False]


def test_free_footage_only_after_drive_has_the_current_render(session, tmp_path, monkeypatch):
    from backend.app.models import StudioProject
    from backend.app.studio import archive
    monkeypatch.setattr(archive, "project_dir", lambda pid: tmp_path)
    (tmp_path / "footage").mkdir()
    (tmp_path / "footage" / "vi1.mp4").write_bytes(b"x" * 1000)
    (tmp_path / "segcache").mkdir()
    (tmp_path / "render").mkdir()
    (tmp_path / "render" / "final.mp4").write_bytes(b"v")
    p = StudioProject(tmdb_id=78, title="Free", render={"file": "render/final.mp4", "rendered_at": "r2"},
                      drive={"status": "saved", "rendered_at": "r1"},
                      trailer={"sources": [{"id": "vi1", "origin": "imdb", "file": str(tmp_path / "footage" / "vi1.mp4")}]})
    session.add(p)
    session.commit()
    assert archive.free_footage(p.id) is None                     # Drive has an OLDER render: keep everything
    p.drive = {"status": "saved", "rendered_at": "r2"}
    session.commit()
    out = archive.free_footage(p.id)
    assert out and not (tmp_path / "footage").exists() and not (tmp_path / "segcache").exists()
    assert (tmp_path / "render" / "final.mp4").exists()            # the queue still posts this
    calls = []
    monkeypatch.setattr(archive, "restore", lambda pid: calls.append(pid) or "ok")
    assert archive.ensure_footage(p.id) and calls == [p.id]        # a re-render brings it back first
