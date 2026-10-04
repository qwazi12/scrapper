import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.models import QueueItem
from backend.app.social import metadata, queue_manager as qm, upload_post

ET = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc


def et(y, mo, d, h, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=ET).astimezone(UTC)


def add(s, n, source="Movie Clips / @VynixAE", status="ready", **kw):
    kw.setdefault("accounts", ["default:youtube"])   # profile "mk" is never used for queue videos
    items = []
    for _ in range(n):
        it = QueueItem(pipeline=source.split(" / ")[-1], source=source, title="t",
                       status=status, **kw)
        s.add(it)
        items.append(it)
    s.commit()
    for it in items:
        if it.position is None:
            it.position = it.id
    s.commit()
    return items


# --- slots ------------------------------------------------------------------
def test_slots_are_every_2h_8am_to_10pm_eastern():
    slots = qm.slots_after(et(2026, 10, 1, 7, 30), 9)
    hours = [s.astimezone(ET).hour for s in slots]
    assert hours == [8, 10, 12, 14, 16, 18, 20, 22, 8]
    assert slots[-1].astimezone(ET).day == 2


def test_slots_skip_the_current_slot_and_overnight():
    slots = qm.slots_after(et(2026, 10, 1, 22, 0), 2)  # exactly at the last slot
    assert [s.astimezone(ET).hour for s in slots] == [8, 10]


# --- planner ----------------------------------------------------------------
def test_plan_assigns_one_slot_per_pipeline_in_position_order(session):
    a = add(session, 3, "Movie Clips / @VynixAE")
    b = add(session, 2, "The ICK Room")
    a[0].position, a[2].position = a[2].position, a[0].position  # reorder
    session.commit()
    now = et(2026, 10, 1, 9, 0)
    qm.plan_schedule(session, now)
    hrs = lambda it: it.scheduled_at.astimezone(ET).hour
    assert [hrs(a[2]), hrs(a[1]), hrs(a[0])] == [10, 12, 14]
    assert [hrs(x) for x in b] == [10, 12]  # independent pipeline, same slots


def test_plan_ignores_review_items(session):
    (r,) = add(session, 1, status="review")
    qm.plan_schedule(session, et(2026, 10, 1, 9))
    assert r.scheduled_at is None


def test_due_item_keeps_its_slot(session):
    (it,) = add(session, 1)
    slot = et(2026, 10, 1, 10)
    it.scheduled_at = slot
    session.commit()
    qm.plan_schedule(session, slot + datetime.timedelta(minutes=1))
    assert qm._aware(it.scheduled_at) == slot


def test_stale_slot_is_replanned_not_backfilled(session):
    (it,) = add(session, 1)
    it.scheduled_at = et(2026, 9, 28, 10)  # server was down for days
    session.commit()
    now = et(2026, 10, 1, 9)
    qm.plan_schedule(session, now)
    assert qm._aware(it.scheduled_at) == et(2026, 10, 1, 10)


def test_tick_posts_one_per_pipeline(session, monkeypatch):
    a = add(session, 2, "Movie Clips / @VynixAE")
    b = add(session, 1, "The ICK Room")
    now = datetime.datetime.now(UTC)
    for it in a + b:
        it.scheduled_at = now - datetime.timedelta(minutes=1)
    session.commit()
    posted = []

    async def fake_publish(item_id, s):
        posted.append(item_id)
        s.get(QueueItem, item_id).status = "posted"
        s.commit()

    monkeypatch.setattr(qm, "publish_queue_item", fake_publish)
    monkeypatch.setattr(qm, "sweep_archive", lambda s, now: 0)
    qm.run_scheduler_tick()
    assert sorted(posted) == sorted([a[0].id, b[0].id])


# --- archive sweep ----------------------------------------------------------
def test_sweep_trashes_drive_file_and_deletes_after_4_days(session, monkeypatch):
    now = datetime.datetime.now(UTC)
    old = add(session, 1, status="posted", drive_link="https://drive.google.com/file/d/OLD/view",
              published_at=now - datetime.timedelta(days=4, minutes=1))[0]
    fresh = add(session, 1, status="posted", drive_link="https://drive.google.com/file/d/NEW/view",
                published_at=now - datetime.timedelta(days=3))[0]
    trashed = []
    import backend.app.drive_sync as ds
    monkeypatch.setattr(ds, "trash_drive_file", lambda link: trashed.append(link) or True)
    old_id, fresh_id = old.id, fresh.id
    assert qm.sweep_archive(session, now) == 1
    assert trashed == ["https://drive.google.com/file/d/OLD/view"]
    assert session.get(QueueItem, old_id) is None
    assert session.get(QueueItem, fresh_id) is not None


def test_sweep_keeps_row_when_drive_trash_fails(session, monkeypatch):
    now = datetime.datetime.now(UTC)
    it = add(session, 1, status="archived", drive_link="https://drive.google.com/file/d/X/view",
             published_at=now - datetime.timedelta(days=10))[0]
    import backend.app.drive_sync as ds

    def boom(link):
        raise RuntimeError("drive down")

    monkeypatch.setattr(ds, "trash_drive_file", boom)
    assert qm.sweep_archive(session, now) == 0
    assert session.get(QueueItem, it.id) is not None


# --- API ----------------------------------------------------------------------
@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


def test_shuffle_persists_order_and_keeps_notes(session, client):
    items = add(session, 6, notes="keep me")
    before = [it.id for it in items]
    for _ in range(5):  # random can return the same order; try a few times
        r = client.post("/api/queue/shuffle", json={"mode": "random"})
        assert r.status_code == 200
        order = [i["id"] for i in client.get("/api/queue").json()]
        if order != before:
            break
    assert sorted(order) == sorted(before) and order != before
    assert all(i["notes"] == "keep me" for i in client.get("/api/queue").json())


def test_list_is_in_posting_order(session, client):
    a, b = add(session, 2)
    a.position, b.position = 20, 10
    session.commit()
    assert [i["id"] for i in client.get("/api/queue").json()] == [b.id, a.id]


def test_bulk_edit_changes_only_sent_fields(session, client):
    a, b = add(session, 2, status="review")
    r = client.post("/api/queue/bulk-action",
                    json={"ids": [a.id, b.id], "action": "edit", "tags": "#new"})
    assert r.status_code == 200
    got = {i["id"]: i for i in client.get("/api/queue").json()}
    assert got[a.id]["tags"] == "#new" and got[a.id]["title"] == "t"


def test_bulk_posted_starts_archive_clock(session, client):
    (a,) = add(session, 1, status="ready")
    client.post("/api/queue/bulk-action", json={"ids": [a.id], "action": "posted"})
    session.refresh(a)
    assert a.published_at is not None


def test_generate_ai_reports_missing_key_instead_of_template(session, client):
    (a,) = add(session, 1)
    r = client.post(f"/api/queue/{a.id}/generate-ai")
    assert r.status_code == 502 and "GEMINI_API_KEY" in r.json()["detail"]


def test_schedule_endpoint(session, client):
    add(session, 2, "Movie Clips / @VynixAE")
    d = client.get("/api/schedule").json()
    assert d["slots_per_day"] == 8 and d["interval_hours"] == 2
    assert d["pipelines"]["Movie Clips"]["ready"] == 2


# --- helpers -------------------------------------------------------------------
def test_metadata_non_strict_still_falls_back():
    res = asyncio.run(metadata.generate_social_metadata(["a"]))
    assert res["model"] == "template"


def test_bulk_edit_pipeline_moves_schedule_group(session, client):
    (a,) = add(session, 1, "Movie Clips / @VynixAE")
    client.post("/api/queue/bulk-action", json={"ids": [a.id], "action": "edit", "pipeline": "The ICK Room"})
    session.refresh(a)
    assert a.source == "The ICK Room / @VynixAE"
    assert qm.pipeline_group(a) == "The ICK Room"


# --- Upload-Post publishing --------------------------------------------------
def test_planner_never_schedules_items_without_picked_accounts(session):
    (it,) = add(session, 1, accounts=[])
    qm.plan_schedule(session, et(2026, 10, 1, 9))
    assert it.scheduled_at is None


def test_post_now_without_accounts_is_refused(session, client):
    (it,) = add(session, 1, accounts=[])
    r = client.post(f"/api/queue/{it.id}/publish")
    assert r.status_code == 400 and "No accounts picked" in r.json()["detail"]


def test_group_by_profile_sends_one_upload_per_profile():
    g = upload_post.group_by_profile(["mk:youtube", "default:youtube", "mk:tiktok"])
    assert g == {"mk": ["youtube", "tiktok"], "default": ["youtube"]}


def test_submit_one_request_per_profile_and_rejects_unconnected(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    calls = []

    async def accounts():
        return [{"id": "mk:youtube", "profile": "mk"}, {"id": "default:youtube", "profile": "default"}]

    async def upload(path, **kw):
        calls.append((kw["profile"], kw["platforms"], kw["privacy"]))
        return f"req-{kw['profile']}"

    monkeypatch.setattr(upload_post, "list_accounts", accounts)
    monkeypatch.setattr(upload_post, "upload_video", upload)
    entries = asyncio.run(qm.submit_upload(video, ["mk:youtube", "default:youtube"],
                                           title="t", description="d", tags=[]))
    assert sorted(c[0] for c in calls) == ["default", "mk"]
    assert {e["request_id"] for e in entries} == {"req-mk", "req-default"}
    with pytest.raises(upload_post.UploadPostError, match="Not connected"):
        asyncio.run(qm.submit_upload(video, ["old:youtube"], title="t", description="", tags=[]))


CONNECTED = [
    {"id": "default:youtube", "profile": "default"},
    {"id": "default:tiktok", "profile": "default"},
    {"id": "mk:youtube", "profile": "mk"},
]


def test_whole_profile_expands_to_its_channels():
    assert upload_post.expand_targets(["default:*"], CONNECTED) == ["default:youtube", "default:tiktok"]


def test_profile_plus_specific_channel_dedupes():
    got = upload_post.expand_targets(["default:*", "default:youtube", "mk:youtube"], CONNECTED)
    assert got == ["default:youtube", "default:tiktok", "mk:youtube"]


def test_empty_or_unknown_profile_is_rejected():
    with pytest.raises(upload_post.UploadPostError, match="no channels connected"):
        upload_post.expand_targets(["ghost:*"], CONNECTED)


def test_whole_profile_submits_one_request_with_all_its_platforms(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    calls = []

    async def accounts():
        return CONNECTED

    async def upload(path, **kw):
        calls.append((kw["profile"], kw["platforms"]))
        return "r1"

    monkeypatch.setattr(upload_post, "list_accounts", accounts)
    monkeypatch.setattr(upload_post, "upload_video", upload)
    asyncio.run(qm.submit_upload(video, ["default:*"], title="t", description="", tags=[]))
    assert calls == [("default", ["youtube", "tiktok"])]


def _entry(profile, rid, nets=("youtube",), minutes_ago=1):
    t = datetime.datetime.now(UTC) - datetime.timedelta(minutes=minutes_ago)
    return {"profile": profile, "platforms": list(nets), "request_id": rid, "submitted_at": t.isoformat()}


def test_still_processing_waits():
    now = datetime.datetime.now(UTC)
    assert qm.resolve_results([_entry("mk", "r1")], {"r1": {"status": "processing"}}, now) is None


def test_completed_success_and_failure_split_per_account(session):
    (it,) = add(session, 1, status="posting", accounts=["mk:youtube", "default:youtube"])
    now = datetime.datetime.now(UTC)
    res = qm.resolve_results(
        [_entry("mk", "r1"), _entry("default", "r2")],
        {"r1": {"status": "completed", "results": [
            {"profile_username": "mk", "platform": "youtube", "success": True, "post_url": "https://y/1"}]},
         "r2": {"status": "completed", "results": [
            {"profile_username": "default", "platform": "youtube", "success": False,
             "error_message": "Daily cap reached"}]}},
        now)
    qm.apply_result(it, res, now)
    assert it.status == "retry" and it.accounts == ["default:youtube"] and it.published_at is None
    assert "https://y/1" in it.notes and "Daily cap" in it.notes


def test_all_success_marks_posted_and_starts_clock(session):
    (it,) = add(session, 1, status="posting", accounts=["mk:youtube"])
    now = datetime.datetime.now(UTC)
    res = qm.resolve_results([_entry("mk", "r1")], {"r1": {"status": "completed", "results": [
        {"profile_username": "mk", "platform": "youtube", "success": True, "post_url": "u"}]}}, now)
    qm.apply_result(it, res, now)
    assert it.status == "posted" and it.published_at == now


def test_rejected_profile_upload_is_a_failure_not_silence():
    now = datetime.datetime.now(UTC)
    res = qm.resolve_results([{"profile": "mk", "platforms": ["youtube"], "error": "413"}], {}, now)
    assert "mk:youtube" in res["failed"] and not res["ok"]


def test_stuck_request_times_out():
    now = datetime.datetime.now(UTC)
    res = qm.resolve_results([_entry("mk", "r1", minutes_ago=200)], {"r1": {"status": "processing"}}, now)
    assert "mk:youtube" in res["failed"]


def test_reconcile_polls_upload_post(session, monkeypatch):
    (it,) = add(session, 1, status="posting", accounts=["mk:youtube"])
    it.publish_requests = [_entry("mk", "r9")]
    session.commit()

    async def status(rid):
        assert rid == "r9"
        return {"status": "completed", "results": [
            {"profile_username": "mk", "platform": "youtube", "success": True, "post_url": "u"}]}

    monkeypatch.setattr(upload_post, "get_status", status)
    assert qm.reconcile_posting(session, datetime.datetime.now(UTC)) == 1
    assert it.status == "posted"


def test_accounts_endpoint_blocks_when_key_missing(client):
    d = client.get("/api/social/accounts").json()
    assert d["configured"] is False and "UPLOADPOST_API_KEY" in d["message"]


# --- guards: Outstand must not come back --------------------------------------
def test_outstand_module_is_gone():
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backend.app.social.outstand")


def test_no_outstand_text_in_ui_or_backend():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    hits = []
    for base, pats in ((root / "frontend" / "app", ("*.tsx",)), (root / "frontend" / "lib", ("*.ts",)),
                       (root / "backend" / "app", ("*.py",))):
        for pat in pats:
            for f in base.rglob(pat):
                text = f.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if "outstand" in line.lower() and "outstand_post_id" not in line:
                        hits.append(f"{f.name}: {line.strip()[:80]}")
    assert hits == []


def test_ai_check_reports_missing_key(client):
    r = client.post("/api/social/ai-check")
    assert r.status_code == 502 and "GEMINI_API_KEY" in r.json()["detail"]


def test_schedule_reports_scheduler_heartbeat(client):
    sc = client.get("/api/schedule").json()["scheduler"]
    assert sc["tick_seconds"] == 300 and "last_tick_at" in sc and sc["enabled"] is False  # web_only in tests


# --- catch-up slot & UTC serialization ----------------------------------------
def test_made_ready_just_after_slot_catches_that_slot(session):
    (it,) = add(session, 1)
    qm.plan_schedule(session, et(2026, 10, 1, 18, 2))  # 6:02pm ET
    assert qm._aware(it.scheduled_at) == et(2026, 10, 1, 18)


def test_catch_up_skipped_when_pipeline_already_posted_in_slot(session):
    (done,) = add(session, 1, status="posted")
    done.scheduled_at = et(2026, 10, 1, 18)
    (it,) = add(session, 1)
    session.commit()
    qm.plan_schedule(session, et(2026, 10, 1, 18, 2))
    assert qm._aware(it.scheduled_at) == et(2026, 10, 1, 20)


def test_no_catch_up_after_grace(session):
    (it,) = add(session, 1)
    qm.plan_schedule(session, et(2026, 10, 1, 18, 45))
    assert qm._aware(it.scheduled_at) == et(2026, 10, 1, 20)


def test_catch_up_takes_only_one_item_per_pipeline(session):
    a, b = add(session, 2)
    qm.plan_schedule(session, et(2026, 10, 1, 18, 2))
    assert [qm._aware(a.scheduled_at), qm._aware(b.scheduled_at)] == [et(2026, 10, 1, 18), et(2026, 10, 1, 20)]


def test_api_times_are_explicitly_utc(session, client):
    (it,) = add(session, 1)
    it.scheduled_at = datetime.datetime(2026, 10, 2, 0, 0)  # naive, as Postgres returns it
    session.commit()
    row = next(i for i in client.get("/api/queue").json() if i["id"] == it.id)
    assert row["scheduled_at"].endswith(("Z", "+00:00"))


# --- editable schedule ---------------------------------------------------------
@pytest.fixture()
def reset_schedule(session):
    yield
    qm.save_schedule(session, None)


def test_schedule_edit_changes_slots_and_replans(session, client, reset_schedule):
    (it,) = add(session, 1)
    r = client.put("/api/schedule/config",
                   json={"timezone": "America/New_York", "start_hour": 9, "end_hour": 21, "interval_hours": 3})
    assert r.status_code == 200 and r.json()["slots_per_day"] == 5 and r.json()["customized"] is True
    hours = [s.astimezone(ET).hour for s in qm.slots_after(et(2026, 10, 1, 8), 6)]
    assert hours == [9, 12, 15, 18, 21, 9]
    session.refresh(it)
    assert it.scheduled_at.replace(tzinfo=UTC).astimezone(ET).hour in (9, 12, 15, 18, 21)


@pytest.mark.parametrize("bad,why", [
    ({"start_hour": 22, "end_hour": 8}, "before the last"),
    ({"interval_hours": 0}, "Interval"),
    ({"timezone": "Mars/Base"}, "Unknown timezone"),
    ({"end_hour": 24}, "between 0 and 23"),
])
def test_schedule_edit_rejects_bad_values(client, reset_schedule, bad, why):
    body = {"timezone": "America/New_York", "start_hour": 8, "end_hour": 22, "interval_hours": 2, **bad}
    r = client.put("/api/schedule/config", json=body)
    assert r.status_code == 400 and why in r.json()["detail"]


def test_schedule_reset_returns_to_defaults(client, reset_schedule):
    client.put("/api/schedule/config", json={"timezone": "UTC", "start_hour": 0, "end_hour": 0, "interval_hours": 1})
    d = client.put("/api/schedule/config", json={"reset": True}).json()
    assert d["customized"] is False and d["start_hour"] == 8 and d["interval_hours"] == 2


# --- bulk AI ---------------------------------------------------------------------
def test_bulk_ai_needs_key(session, client):
    (it,) = add(session, 1)
    r = client.post("/api/queue/bulk-ai", json={"ids": [it.id], "action": "ai"})
    assert r.status_code == 400 and "GEMINI_API_KEY" in r.json()["detail"]


def test_bulk_ai_rewrites_every_item_and_reports_failures(session, monkeypatch):
    from backend.app.social import ai_bulk
    a, b, c = add(session, 3)
    calls = []

    from backend.app.social import clip_research
    from backend.app.studio import gemini as sgem

    def fake(item, cache=None):
        calls.append(item.id)
        if len(calls) == 2:
            raise sgem.GeminiError("Gemini returned HTTP 500")
        return {"title": "AI title", "caption": "AI caption", "hashtags": ["#x", "#y"]}, {"matched": False}

    monkeypatch.setattr(clip_research, "generate", fake)
    ai_bulk.status.update(running=False)
    ai_bulk.start([a.id, b.id, c.id])
    import time
    for _ in range(100):
        if not ai_bulk.status["running"]:
            break
        time.sleep(0.05)
    assert ai_bulk.status["done"] == 2 and ai_bulk.status["failed"] == 1
    assert "HTTP 500" in ai_bulk.status["errors"][0]
    session.expire_all()
    assert sum(1 for it in (a, b, c) if session.get(QueueItem, it.id).title == "AI title") == 2



# --- TMDB research for queue clips --------------------------------------------
from backend.app.social import clip_research
from backend.app.studio import gemini as sgem, tmdb as stmdb


def _clip(session, **kw):
    it = QueueItem(title="Superman's son gets too excited with his new powers", video_name="superman_son.mp4",
                   tags="#superman", source="Movie Clips / @SolarrEditss", status="review", **kw)
    session.add(it)
    session.commit()
    return it


def test_clip_research_uses_tmdb_facts_in_the_prompt(session, monkeypatch):
    it = _clip(session)
    monkeypatch.setattr(stmdb.settings, "tmdb_read_token", "tok")
    prompts = []

    def fake_ask(prompt, **kw):
        prompts.append(prompt)
        if "most likely from" in prompt:
            return {"title": "Superman & Lois", "media_type": "tv", "year": 2021, "confidence": 0.9, "reason": "names"}
        return {"title": "Jon Kent loses control of his powers | Superman & Lois", "caption": "c",
                "hashtags": ["#shorts", "#Superman & Lois"]}

    monkeypatch.setattr(sgem, "ask_json", fake_ask)
    monkeypatch.setattr(stmdb, "search", lambda q: [{"tmdb_id": 95057, "media_type": "tv", "date": "2021-02-23", "popularity": 50}])
    monkeypatch.setattr(clip_research, "lookup", lambda mt, i: {
        "tmdb_id": i, "media_type": mt, "title": "Superman & Lois", "year": "2021", "genres": ["Drama"],
        "overview": "o", "cast": [{"actor": "Tyler Hoechlin", "character": "Clark Kent"}],
        "keywords": ["superhero", "dc comics"], "watch_on": ["Max"], "source": "https://www.themoviedb.org/tv/95057"})
    meta, r = clip_research.generate(it)
    assert r["matched"] and r["title"] == "Superman & Lois"
    assert "Tyler Hoechlin as Clark Kent" in prompts[1] and "superhero" in prompts[1]
    assert "#Superman&Lois" in meta["hashtags"]  # no spaces inside hashtags


def test_low_confidence_clip_is_not_tied_to_a_title(session, monkeypatch):
    it = _clip(session)
    prompts = []

    def fake_ask(prompt, **kw):
        prompts.append(prompt)
        if "most likely from" in prompt:
            return {"title": "Some Show", "media_type": "tv", "confidence": 0.3}
        return {"title": "t", "caption": "c", "hashtags": []}

    monkeypatch.setattr(sgem, "ask_json", fake_ask)
    meta, r = clip_research.generate(it)
    assert r["matched"] is False and "do NOT name any title" in prompts[1]


def test_research_cache_shares_lookups(session, monkeypatch):
    a, b = _clip(session), _clip(session)
    monkeypatch.setattr(stmdb.settings, "tmdb_read_token", "tok")
    monkeypatch.setattr(sgem, "ask_json", lambda p, **kw: {"title": "Superman & Lois", "media_type": "tv",
                                                          "year": 2021, "confidence": 0.9})
    looked = []
    monkeypatch.setattr(stmdb, "search", lambda q: [{"tmdb_id": 1, "media_type": "tv", "date": "2021", "popularity": 1}])
    monkeypatch.setattr(clip_research, "lookup", lambda mt, i: looked.append(i) or {"title": "S", "cast": [],
                        "genres": [], "keywords": [], "watch_on": [], "overview": "", "media_type": mt, "tmdb_id": i})
    cache = {}
    clip_research.research(a, cache)
    clip_research.research(b, cache)
    assert looked == [1]


# --- auto-SEO before posting ---------------------------------------------------
def test_raw_clip_gets_seo_before_posting(session, monkeypatch):
    it = _clip(session, accounts=["default:youtube"])
    it.description = it.title  # raw Drive import
    session.commit()
    monkeypatch.setattr(clip_research, "generate", lambda item, cache=None: (
        {"title": "SEO title", "caption": "SEO caption", "hashtags": ["#shorts"]}, {"matched": True, "title": "Show"}))

    async def fake_submit(*a, **kw):
        return [{"profile": "default", "platforms": ["youtube"], "request_id": "r", "submitted_at": "2026-10-02T00:00:00+00:00"}]

    monkeypatch.setattr(qm, "submit_upload", fake_submit)
    it.video_path = __file__  # any existing file
    session.commit()
    asyncio.run(qm.publish_queue_item(it.id, session))
    session.refresh(it)
    assert it.title == "SEO title" and it.research["title"] == "Show" and it.status == "posting"


def test_owner_edited_clip_is_not_rewritten(session):
    it = _clip(session)
    it.description = "My own hand-written caption"
    session.commit()
    assert qm.needs_auto_seo(it) is False


def test_auto_seo_failure_never_blocks_posting(session, monkeypatch):
    it = _clip(session, accounts=["default:youtube"])
    it.description, it.video_path = it.title, __file__
    session.commit()

    def boom(item, cache=None):
        raise sgem.GeminiError("down")

    monkeypatch.setattr(clip_research, "generate", boom)

    async def fake_submit(*a, **kw):
        return [{"profile": "default", "platforms": ["youtube"], "request_id": "r", "submitted_at": "2026-10-02T00:00:00+00:00"}]

    monkeypatch.setattr(qm, "submit_upload", fake_submit)
    asyncio.run(qm.publish_queue_item(it.id, session))
    session.refresh(it)
    assert it.status == "posting" and it.research is None


def test_longform_tags_include_basics_and_keywords():
    from backend.app.studio import stage_script
    tags = stage_script.seo_tags(["Raimi horror"], {"title": "Send Help", "primary_date": "2026-01-30",
                                                     "cast": [{"actor": "Rachel McAdams"}], "directors": ["Sam Raimi"],
                                                     "keywords": ["island", "survival", "send help"]})
    assert tags[:4] == ["Send Help", "Send Help trailer", "Send Help trailer breakdown", "Send Help release date"]
    assert "Rachel McAdams" in tags and "island" in tags and tags.count("Send Help") == 1


# --- Pacing Throttle & Presets -----------------------------------------------
def test_pacing_presets_single_post_per_day():
    cfg = qm.validate_schedule({
        "timezone": "America/New_York",
        "start_hour": 18,
        "end_hour": 18,
        "interval_hours": 2,
        "posts_per_day": 1,
    })
    assert cfg["posts_per_day"] == 1
    # Save config and test slots
    qm._schedule_cfg = cfg
    slots = qm.slots_after(et(2026, 10, 1, 12, 0), 2)
    assert len(slots) == 2
    assert [s.astimezone(ET).hour for s in slots] == [18, 18]
    assert slots[0].astimezone(ET).day == 1
    assert slots[1].astimezone(ET).day == 2
    assert qm.slots_per_day() == 1


def test_pacing_presets_20_posts_per_day():
    cfg = qm.validate_schedule({
        "timezone": "America/New_York",
        "start_hour": 8,
        "end_hour": 22,
        "interval_hours": 2,
        "posts_per_day": 20,
    })
    qm._schedule_cfg = cfg
    slots = qm.slots_after(et(2026, 10, 1, 7, 30), 20)
    assert len(slots) == 20
    # First slot at 8:00, last slot at 22:00
    assert slots[0].astimezone(ET).hour == 8
    assert slots[0].astimezone(ET).minute == 0
    assert slots[-1].astimezone(ET).hour == 22
    assert slots[-1].astimezone(ET).minute == 0
    assert qm.slots_per_day() == 20


def test_pacing_pipeline_and_account_overrides():
    cfg = qm.validate_schedule({
        "timezone": "America/New_York",
        "start_hour": 8,
        "end_hour": 22,
        "interval_hours": 2,
        "posts_per_day": 8,
        "pipelines": {
            "LongForm": {"posts_per_day": 1, "start_hour": 19, "end_hour": 19},
            "Movie Clips": {"posts_per_day": 20, "start_hour": 8, "end_hour": 22},
        },
        "accounts": {
            "mk": {"posts_per_day": 3, "start_hour": 10, "end_hour": 20},
        }
    })
    qm._schedule_cfg = cfg

    # 1. Global default
    assert qm.slots_per_day() == 8

    # 2. Pipeline LongForm override
    assert qm.slots_per_day(pipeline="LongForm") == 1
    lf_slots = qm.slots_after(et(2026, 10, 1, 10, 0), 1, pipeline="LongForm")
    assert lf_slots[0].astimezone(ET).hour == 19

    # 3. Pipeline Movie Clips override
    assert qm.slots_per_day(pipeline="Movie Clips") == 20

    # 4. Account override takes precedence
    # Item with pipeline Movie Clips but targeting account mk gets mk's pacing (3/day)
    assert qm.slots_per_day(pipeline="Movie Clips", accounts=["mk:*"]) == 3
    mk_slots = qm.slots_after(et(2026, 10, 1, 8, 0), 3, pipeline="Movie Clips", accounts=["mk:youtube"])
    assert len(mk_slots) == 3
    assert mk_slots[0].astimezone(ET).hour == 10
    assert mk_slots[-1].astimezone(ET).hour == 20


def test_pacing_validation_rejects_invalid_values():
    with pytest.raises(ValueError, match="posts_per_day must be an integer between 1 and 48"):
        qm.validate_schedule({
            "timezone": "America/New_York",
            "start_hour": 8,
            "end_hour": 22,
            "interval_hours": 2,
            "posts_per_day": 99,
        })
    with pytest.raises(ValueError, match="Override for test: posts_per_day must be 1-48"):
        qm.validate_schedule({
            "timezone": "America/New_York",
            "start_hour": 8,
            "end_hour": 22,
            "interval_hours": 2,
            "pipelines": {"test": {"posts_per_day": 0}}
        })


def test_x_unavailable_posts_are_permanent_and_readable():
    from backend.core.scraper import classify_error
    assert classify_error("ERROR: [twitter] 2080008291460030561: Suspended") == (
        True, "X has suspended this account, so its posts can't be downloaded.")
    assert classify_error("ERROR: [twitter] 1: No video could be found in this tweet")[0] is True


def test_queue_change_wakes_the_poster(client):
    from backend.app.social import queue_manager as qmod
    assert qmod.TICK_SECONDS == 300
    qmod._wake.clear()
    with SessionLocal() as s:
        it = QueueItem(title="t", status="review", accounts=[])
        s.add(it); s.commit(); iid = it.id
    client.post(f"/api/queue/{iid}/approve")
    assert qmod._wake.is_set()          # no 5-minute wait for a time slot
    qmod._wake.clear()
    client.get("/api/queue")
    assert not qmod._wake.is_set()      # reads don't wake it



# --- 2026-10-04 incident: 8 posts in one slot ---------------------------------
def test_pipeline_with_own_pacing_gets_one_post_per_slot(session, reset_schedule):
    """Replay of Sat 3 Oct: global 3/day 10–20, Movie Clips 3/day 10–22 (16:00 slot).
    The planner checked the GLOBAL slot (15:00), never saw 16:00 as used, and each
    5-min tick gave the 16:00 slot to the next video."""
    qm.save_schedule(session, {"timezone": "America/New_York", "start_hour": 10, "end_hour": 20,
                               "interval_hours": 2, "posts_per_day": 3,
                               "pipelines": {"Movie Clips": {"posts_per_day": 3, "start_hour": 10, "end_hour": 22}}})
    items = add(session, 4, "Movie Clips / @VynixAE")
    qm.plan_schedule(session, et(2026, 10, 3, 16, 0, ))
    first = min(items, key=lambda it: it.scheduled_at)
    assert qm._aware(first.scheduled_at) == et(2026, 10, 3, 16)
    first.status = "posted"                      # it went out at 16:00
    session.commit()
    for minute in (5, 10, 15, 20, 25):           # the following ticks
        qm.plan_schedule(session, et(2026, 10, 3, 16, minute))
        rest = [qm._aware(it.scheduled_at) for it in items if it is not first]
        assert et(2026, 10, 3, 16) not in rest, f"16:00 handed out again at 16:{minute:02d}"
    assert sorted(qm._aware(it.scheduled_at) for it in items if it is not first)[0] == et(2026, 10, 3, 22)


def test_tick_never_posts_more_than_the_daily_limit(session, reset_schedule, monkeypatch):
    qm.save_schedule(session, {"timezone": "America/New_York", "start_hour": 10, "end_hour": 22,
                               "interval_hours": 2, "posts_per_day": 1})
    (done,) = add(session, 1, status="posted")
    done.scheduled_at = et(2026, 10, 3, 10)
    (extra,) = add(session, 1)
    extra.scheduled_at = et(2026, 10, 3, 16)     # somehow due today too
    session.commit()
    posted = []

    async def fake_publish(item_id, s):
        posted.append(item_id)

    monkeypatch.setattr(qm, "publish_queue_item", fake_publish)
    monkeypatch.setattr(qm, "plan_schedule", lambda s, now: 0)
    monkeypatch.setattr(qm, "reconcile_posting", lambda s, now: 0)
    real_now = qm.datetime.datetime

    class FakeDT(real_now):
        @classmethod
        def now(cls, tz=None):
            return et(2026, 10, 3, 16, 5)

    monkeypatch.setattr(qm.datetime, "datetime", FakeDT)
    qm.run_scheduler_tick()
    assert posted == []                          # 1/day and today's one already went out


def test_production_default_is_one_post_a_day():
    from backend.app.config import Settings
    assert Settings.model_fields["post_posts_per_day"].default == 1


# --- profile "mk" is never used for queue videos (owner, 2026-10-04) ----------
def test_mk_destination_is_refused_everywhere(session, client):
    (it,) = add(session, 1, accounts=["default:*"])
    r = client.patch(f"/api/queue/{it.id}", json={"accounts": ["mk:*"]})
    assert r.status_code == 400 and "never used" in r.json()["detail"]
    r = client.post("/api/queue/bulk-action", json={"ids": [it.id], "action": "set_accounts",
                                                   "accounts": ["default:*", "mk:youtube"]})
    assert r.status_code == 400
    session.refresh(it)
    assert it.accounts == ["default:*"]


def test_startup_strips_mk_and_posting_never_sends_it(session, monkeypatch):
    a, b = add(session, 2, accounts=["mk:youtube", "default:*"])
    b.accounts = ["mk:*"]
    session.commit()
    assert qm.strip_blocked_accounts(session) == 2
    session.refresh(a)
    session.refresh(b)
    assert a.accounts == ["default:*"] and b.accounts == []
    b.accounts = ["mk:youtube"]                   # slipped in somehow
    session.commit()
    with pytest.raises(upload_post.UploadPostError, match="never used"):
        asyncio.run(qm.publish_queue_item(b.id, session))
