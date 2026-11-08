"""DramaRama on the regular scheduler (owner + dot handoff, 2026-11-03) and the X safeguards. No provider calls."""
import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.app.models import QueueItem
from backend.app.social import queue_manager as qm, upload_post as up

ET = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc
FB = ["10:00", "13:20", "16:40", "20:00"]
X = ["11:40", "18:20"]
YT = ["12:30", "19:00"]


def et(d, h, mi=0):
    return datetime.datetime(2026, 11, d, h, mi, tzinfo=ET).astimezone(UTC)


def local(it):
    return qm._aware(it.scheduled_at).astimezone(ET).strftime("%d %H:%M") if it.scheduled_at else None


@pytest.fixture
def drama(session):
    cfg = qm.load_schedule(session)
    qm.save_schedule(session, {**cfg, "timezone": "America/New_York", "pipelines": {
        "DramaRama": {"posts_per_day": 4, "platforms": {"facebook": FB, "x": X, "youtube": YT}}}})
    yield
    qm.save_schedule(session, None)


def ep(s, series, n, status="ready", accounts=("Dramarama:*",), **kw):
    kw.setdefault("video_path", "video.mp4")
    it = QueueItem(pipeline="DramaRama", source=f"DramaRama / {series}", status=status, accounts=list(accounts),
                   video_name=f"20260904_{series}_EP{n:03d}_x_7681780498561027349.mp4",
                   title=f"Reviewed hook | {series} Ep. {n}", description="d", tags="#DramaRama", **kw)
    s.add(it)
    s.commit()
    return it


def test_four_facebook_a_day_two_series_two_each_in_episode_order(session, drama):
    a = [ep(session, "Alpha", n) for n in range(1, 7)] + [ep(session, "Alpha", n, "review") for n in (7, 8)]
    b = [ep(session, "Beta", n) for n in (1, 2, 3)]
    c = [ep(session, "Gamma", n) for n in (1, 2)]                       # third series waits for a free lane
    qm.plan_schedule(session, et(3, 7))
    assert [local(x) for x in a[:6]] == ["03 10:00", "03 16:40", "04 10:00", "04 16:40", "05 10:00", "05 16:40"]
    assert [local(x) for x in b] == ["03 13:20", "03 20:00", "04 13:20"]
    assert [local(x) for x in c] == [None, None]                        # Beta isn't delivered yet: Gamma waits
    assert local(a[6]) is None                                          # EP7 still in review
    assert qm.drama_held["facebook"] == {"Alpha": "waiting for EP7"}    # Ready ran out: waiting, not finished
    assert qm.drama_held["x"] == {"Alpha": "waiting for EP1 on Facebook first", "Beta": "waiting for EP1 on Facebook first",
                                  "Gamma": "waiting for EP1 on Facebook first"}   # every blocked series is listed (2026-11-08)
    assert all(local(x) not in [f"03 {h}" for h in X] for x in a + b)   # X waits for Facebook first


def test_x_and_youtube_go_strictly_in_episode_order_passing_only_what_is_already_there(session, drama):
    """Owner, 2026-11-08: X/YouTube post each series' owed episodes Ep.1, 2, 3…; only an episode already on that
    platform is passed over (Prime Suspect Ep.3 on X); one that isn't there holds every later one."""
    fb = {"done": [["Dramarama:facebook", "f"]]}
    p = [ep(session, "Prime", 1, accounts=("Dramarama:x", "Dramarama:youtube"), catchup=fb),
         ep(session, "Prime", 2, accounts=("Dramarama:x", "Dramarama:youtube"), catchup=fb),
         ep(session, "Prime", 3, accounts=("Dramarama:youtube",), catchup={"done": fb["done"] + [["Dramarama:x", "x3"]]}),
         ep(session, "Prime", 4, accounts=("Dramarama:x", "Dramarama:youtube"), catchup=fb)]
    held = ep(session, "Xmas", 1, accounts=("Dramarama:youtube",),                        # EP1 never reached X
              catchup={**fb, "held": {"Dramarama:x": "X text too long"}})
    later = ep(session, "Xmas", 2, accounts=("Dramarama:x", "Dramarama:youtube"), catchup=fb)
    qm.plan_schedule(session, et(3, 7))
    _, _, plans, held_by = qm.drama_net_plans(session, et(3, 7))
    x = [it.id for it, _ in plans["x"]]
    assert [i for i in x if i in {q.id for q in p}] == [p[0].id, p[1].id, p[3].id]       # EP3 already on X: passed over
    assert later.id not in x and held_by["x"]["Xmas"] == "waiting for EP1"                # EP2 never before EP1 on X
    yt = [it.id for it, _ in plans["youtube"]]
    assert yt.index(held.id) < yt.index(later.id) and [i for i in yt if i in {q.id for q in p}] == [q.id for q in p]


def test_a_series_held_on_x_doesnt_take_an_x_slot_from_a_ready_one(session, drama):
    fb = {"done": [["Dramarama:facebook", "f"]]}
    ep(session, "Aaa", 1, "archived", accounts=("Dramarama:facebook",), catchup={**fb, "held": {"Dramarama:x": "held"}})
    ep(session, "Aaa", 2, accounts=("Dramarama:x",), catchup=fb)
    b = [ep(session, "Bbb", n, accounts=("Dramarama:x",), catchup=fb) for n in (1, 2)]
    c = [ep(session, "Ccc", n, accounts=("Dramarama:x",), catchup=fb) for n in (1, 2)]
    qm.plan_schedule(session, et(3, 7))
    assert sorted(local(x) for x in b + c) == ["03 11:40", "03 18:20", "04 11:40", "04 18:20"]   # still 2 a day
    assert [local(x) for x in b] == sorted(local(x) for x in b) and [local(x) for x in c] == sorted(local(x) for x in c)


def test_missing_episode_holds_only_that_series(session, drama):
    d1, d3 = ep(session, "Delta", 1), ep(session, "Delta", 3)
    e1 = ep(session, "Echo", 1)
    qm.plan_schedule(session, et(3, 7))
    assert local(d1) == "03 10:00" and local(d3) is None and local(e1) == "03 13:20"
    assert qm.drama_held["facebook"]["Delta"] == "waiting for EP2 (not in the queue)"


def test_series_order_continues_after_delivered_episodes_and_skips_duplicates(session, drama):
    done = ep(session, "Alpha", 1, "archived", accounts=("Dramarama:facebook",),
              catchup={"done": [["Dramarama:facebook", "u1"], ["Dramarama:x", "x1"]]})
    dup = ep(session, "Alpha", 1)                                        # same episode again: never re-sent
    two = ep(session, "Alpha", 2)
    qm.plan_schedule(session, et(3, 7))
    assert done.status == "archived" and local(two) == "03 10:00"
    assert local(dup) == "03 12:30"            # EP1 is on Facebook + X already: only YouTube (never had it) takes it


def test_x_catch_up_takes_x_times_and_never_a_facebook_slot(session, drama):
    owed = ep(session, "Alpha", 1, accounts=("Dramarama:x",), catchup={"done": [["Dramarama:facebook", "u"]]})
    fresh = ep(session, "Alpha", 2)
    qm.plan_schedule(session, et(3, 7))
    assert local(owed) == "03 11:40"                                    # X's own time
    assert local(fresh) == "03 10:00"                                   # Facebook's slot goes to the next episode


def _connected(monkeypatch, sent, tmp_path):
    async def list_accounts():
        return [{"id": f"Dramarama:{n}", "profile": "Dramarama"} for n in ("facebook", "x", "youtube")]

    async def upload_video(path, *, profile, platforms, **kw):
        sent.append(sorted(platforms))
        return f"req{len(sent)}"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return str(video)


def test_whole_profile_means_facebook_x_and_youtube(session, drama, monkeypatch, tmp_path):
    sent = []
    it = ep(session, "Alpha", 1, video_path=_connected(monkeypatch, sent, tmp_path))
    it.scheduled_at = et(3, 10)
    session.commit()
    asyncio.run(qm.publish_queue_item(it.id, session, regular=True))
    assert sent == [["facebook"]]                                       # 10:00 is Facebook's; X and YouTube at theirs
    qm.apply_result(it, {"ok": [("Dramarama:facebook", "https://fb/1")], "failed": {}}, et(3, 10, 10))
    assert it.status == "ready" and sorted(it.accounts) == ["Dramarama:x", "Dramarama:youtube"]


def test_dot_approved_rows_keep_their_frozen_destinations_plus_x_and_youtube(session, drama, monkeypatch, tmp_path):
    from backend.app import drama_dialogue
    sent = []
    it = ep(session, "Prime", 1, video_path=_connected(monkeypatch, sent, tmp_path))
    monkeypatch.setattr(qm, "drama_approval", lambda s, item: (["Dramarama:facebook"], None))
    monkeypatch.setattr(qm, "x_media_copy", lambda p: (None, None))
    monkeypatch.setattr(qm, "x_posts_last_day", lambda s, profile, now: 0)
    copy = tmp_path / "dialogue.mp4"
    copy.write_bytes(b"vocals")
    monkeypatch.setattr(drama_dialogue, "fetch", lambda item: (copy, None))
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))   # even a manual Post now
    assert sent == [["facebook"], ["x"], ["youtube"]]                   # dot's targets, plus the owner's X and YouTube


def test_review_rows_and_unreviewed_copy_are_refused_before_download(session, drama, monkeypatch):
    monkeypatch.setattr("backend.app.drive_sync.download_drive_file", lambda *a: pytest.fail("downloaded"))
    rev = ep(session, "Alpha", 1, "review", drive_link="https://drive.google.com/file/d/abc/view", video_path=None)
    with pytest.raises(up.UploadPostError, match="Ready"):
        asyncio.run(qm.publish_queue_item(rev.id, session))
    raw = ep(session, "Alpha", 2, drive_link="https://drive.google.com/file/d/abd/view", video_path=None)
    raw.title = "20260904_Alpha_EP002_x.mp4"
    session.commit()
    with pytest.raises(up.UploadPostError, match="reviewed"):
        asyncio.run(qm.publish_queue_item(raw.id, session))


def test_x_over_the_guard_is_held_with_its_reason_and_facebook_is_never_resent(session, drama, monkeypatch, tmp_path):
    sent = []
    it = ep(session, "Alpha", 1, accounts=("Dramarama:x",), video_path=_connected(monkeypatch, sent, tmp_path),
            catchup={"done": [["Dramarama:facebook", "https://fb/1"]]})
    it.scheduled_at = et(3, 11, 40)
    session.commit()
    monkeypatch.setattr(qm, "x_media_problem", lambda p: "172s is over Scrapper's 140s X guard (longer X uploads not yet verified)")
    asyncio.run(qm.publish_queue_item(it.id, session, regular=True))
    session.refresh(it)
    assert sent == [] and it.status == "archived" and "140s X guard" in it.notes
    assert "172s" in it.catchup["held"]["Dramarama:x"]


def test_x_hold_never_blocks_facebook(session, drama, monkeypatch, tmp_path):
    sent = []
    it = ep(session, "Alpha", 1, accounts=("Dramarama:facebook", "Dramarama:x"), video_path=_connected(monkeypatch, sent, tmp_path))
    monkeypatch.setattr(qm, "x_media_problem", lambda p: "172s is over Scrapper's 140s X guard (longer X uploads not yet verified)")
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))   # Post now: both platforms due
    session.refresh(it)
    assert sent == [["facebook"]] and it.accounts == ["Dramarama:facebook"]


def test_wrong_format_for_x_sends_x_a_converted_copy_and_deletes_it(session, drama, monkeypatch, tmp_path):
    files = {}

    async def list_accounts():
        return [{"id": f"Dramarama:{n}", "profile": "Dramarama"} for n in ("facebook", "x")]

    async def upload_video(path, *, profile, platforms, **kw):
        files[platforms[0]] = str(path)
        return "r"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    copy = tmp_path / "v.x.mp4"
    copy.write_bytes(b"h264")
    monkeypatch.setattr(qm, "x_media_copy", lambda p: (copy, None))
    monkeypatch.setattr(qm, "x_posts_last_day", lambda s, profile, now: 0)   # other tests' X sends share the journal
    it = ep(session, "Alpha", 1, accounts=("Dramarama:facebook", "Dramarama:x"), video_path=str(video))
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    assert files == {"facebook": str(video), "x": str(copy)}             # Facebook keeps the original
    assert not copy.exists()


def test_x_media_copy_converts_only_a_wrong_format(monkeypatch, tmp_path):
    from backend.app.studio import media
    ran = []
    monkeypatch.setattr(media, "run", lambda cmd, timeout=0: ran.append(cmd))
    monkeypatch.setattr(qm, "x_media_problem", lambda p: "200s is over Scrapper's 140s X guard")
    assert qm.x_media_copy(tmp_path / "a.mp4") == (None, "200s is over Scrapper's 140s X guard") and ran == []
    monkeypatch.setattr(qm, "x_media_problem", lambda p: None)
    assert qm.x_media_copy(tmp_path / "a.mp4") == (None, None) and ran == []
    checks = iter(["X needs H.264 + AAC-LC; this is hevc + aac HE-AACv2", None])
    monkeypatch.setattr(qm, "x_media_problem", lambda p: next(checks))
    out, why = qm.x_media_copy(tmp_path / "a.mp4")
    assert why is None and out.suffix == ".mp4" and "libx264" in ran[0] and "aac_low" in ran[0]
    out.unlink()


def test_x_text_over_280_is_held_not_shortened():
    it = QueueItem(title="T" * 300, description="d", tags="#DramaRama")
    assert "280" in qm.x_text_problem(it, None)
    assert qm.x_text_problem(QueueItem(title="Short hook | Alpha Ep. 1", description="d" * 400, tags="#DramaRama"), None) is None


def test_x_refused_after_facebook_is_held_rate_limit_waits_for_next_slot(session):
    a = ep(session, "Alpha", 1, "posting", accounts=("Dramarama:facebook", "Dramarama:x"))
    qm.apply_result(a, {"ok": [("Dramarama:facebook", "https://fb/1")],
                        "failed": {"Dramarama:x": "upload not accepted: media rejected"}}, et(3, 10, 10))
    assert a.status == "archived" and "media rejected" in a.catchup["held"]["Dramarama:x"]
    b = ep(session, "Alpha", 2, "posting", accounts=("Dramarama:facebook", "Dramarama:x"))
    qm.apply_result(b, {"ok": [("Dramarama:facebook", "https://fb/2")],
                        "failed": {"Dramarama:x": "429 Too Many Requests"}}, et(3, 10, 10))
    assert b.status == "ready" and b.accounts == ["Dramarama:x"] and b.scheduled_at is None
    c = ep(session, "Alpha", 3, "posting", accounts=("Dramarama:facebook", "Dramarama:x"))
    qm.apply_result(c, {"ok": [], "failed": {"Dramarama:facebook": "page error", "Dramarama:x": "media rejected"}},
                    et(3, 10, 10))
    assert c.status == "retry" and c.accounts == ["Dramarama:facebook"]   # only what failed; series waits


def test_x_daily_cap_defers_x_without_a_retry_every_tick(session, drama, monkeypatch, tmp_path):
    sent = []
    it = ep(session, "Alpha", 1, accounts=("Dramarama:x",), video_path=_connected(monkeypatch, sent, tmp_path),
            catchup={"done": [["Dramarama:facebook", "https://fb/1"]]})
    it.scheduled_at = et(3, 11, 40)
    session.commit()
    monkeypatch.setattr(qm, "x_media_problem", lambda p: None)
    monkeypatch.setattr(qm, "x_posts_last_day", lambda s, profile, now: qm.X_DAILY_CAP)
    asyncio.run(qm.publish_queue_item(it.id, session, regular=True))
    session.refresh(it)
    assert sent == [] and it.status == "ready" and it.scheduled_at is None   # slot spent; next X slot later
    assert it.accounts == ["Dramarama:x"]


def test_uncertain_outcome_is_held_not_retried():
    entry = {"profile": "Dramarama", "platforms": ["facebook"], "state": "unknown"}
    assert qm.resolve_results([entry], {}, et(3, 12)) is None


def test_dramarama_rows_are_kept_as_series_history(session, monkeypatch):
    old = ep(session, "Alpha", 1, "archived", published_at=et(1, 10))
    clip = QueueItem(pipeline="@VynixAE", source="Movie Clips / @VynixAE", title="t", status="archived",
                     published_at=et(1, 10), accounts=["Cinama:*"])
    session.add(clip)
    session.commit()
    monkeypatch.setattr("backend.app.delivery_archive.retain", lambda s, item: "kept")
    monkeypatch.setattr(qm.settings, "archive_delete_days", 4)
    assert qm.sweep_archive(session, et(10, 10)) == 1                   # the clip goes
    assert session.get(QueueItem, old.id) is not None                   # the episode stays


def test_ick_x_uses_the_saved_reviewed_variant(session, monkeypatch):
    from backend.app.social import queue_payloads
    seen = {}
    real = queue_payloads.preview

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)
    monkeypatch.setattr(queue_payloads, "preview", spy)
    monkeypatch.setattr(qm, "ick_x_variant", lambda s, item: "Short #tonightsconversation #theickroom")
    sent = []

    async def list_accounts():
        return [{"id": f"TheIckRoom:{n}", "profile": "TheIckRoom"} for n in ("facebook", "x")]

    async def upload_video(path, *, profile, platforms, **kw):
        sent.append(sorted(platforms))
        return "r"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    it = QueueItem(pipeline=qm.ICK_ROOM, source="The Ick Room / The ICK Room", status="ready", accounts=["TheIckRoom:x"],
                   title="T" * 200, description="D" * 200, tags="#tonightsconversation #theickroom",
                   video_path=__file__)
    session.add(it)
    session.commit()
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    assert seen.get("x_variant_text") == "Short #tonightsconversation #theickroom" and sent == [["x"]]


def test_ick_x_times_take_only_x_catch_up_and_facebook_keeps_its_four(session):
    cfg = qm.load_schedule(session)
    qm.save_schedule(session, {**cfg, "timezone": "America/New_York", "pipelines": {
        qm.ICK_ROOM: {"posts_per_day": 4, "platforms": {"facebook": FB, "x": X}}}})
    try:
        def row(**kw):
            it = QueueItem(pipeline=qm.ICK_ROOM, source="The Ick Room / The ICK Room", status="ready", title="t",
                           video_path="v.mp4", **kw)
            session.add(it)
            return it
        owed = row(accounts=["TheIckRoom:x"], catchup={"done": [["TheIckRoom:facebook", "u"]]})
        fresh = [row(accounts=["TheIckRoom:*"]) for _ in range(5)]
        session.commit()
        qm.plan_schedule(session, et(3, 7))
        assert local(owed) == "03 11:40"                                # X catch-up at X's time
        assert [local(x) for x in fresh] == ["03 10:00", "03 13:20", "03 16:40", "03 20:00", "04 10:00"]
    finally:
        qm.save_schedule(session, None)


def test_dot_approved_series_go_first_and_unapproved_youtube_has_nothing_to_wait_for(session, drama):
    fx = ("Dramarama:facebook", "Dramarama:x")                          # explicitly not YouTube
    early = [ep(session, "Early", n, accounts=fx) for n in (1, 2)]
    prime = [ep(session, "Prime", n, accounts=fx) for n in (1, 2)] + [ep(session, "Prime", 3, "review", accounts=fx)]
    lanes, held = qm.drama_lanes(early + prime, "facebook", {"Prime"})
    assert [qm.drama_series_episode(lane[0])[0] for lane in lanes] == ["Prime", "Early"]
    assert held == {"Prime": "waiting for EP3"}
    assert qm.drama_lanes(early + prime, "youtube") == ([], {})        # nothing owes YouTube: no lane, no wait


def test_whole_profile_screen_central_post_sends_x_the_short_copy(session, monkeypatch):
    sent = {}

    async def list_accounts():
        return [{"id": f"Cinama:{n}", "profile": "Cinama"} for n in ("youtube", "facebook", "x")]

    async def upload_video(path, *, profile, platforms, title, description, **kw):
        sent[platforms[0]] = (title, description)
        return "r"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    it = QueueItem(pipeline="Movie Clips", source="Movie Clips / test", status="ready", accounts=["Cinama:*"],
                   title="A title", description="D" * 300, tags="#shorts #movieclips", video_path=__file__)
    session.add(it)
    session.commit()
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    assert sent["x"] == ("A title\n\n#MovieClips", "")                 # X: 2 tags max, no #shorts (owner, 2026-11-07)
    assert sent["facebook"][0] == "A title" and "D" * 300 in sent["facebook"][1]


def test_youtube_follows_facebook_in_episode_order_at_its_own_times(session, drama):
    done = ep(session, "Alpha", 1, accounts=("Dramarama:x", "Dramarama:youtube"), catchup={"done": [["Dramarama:facebook", "u"]]})
    nxt = ep(session, "Alpha", 2)
    qm.plan_schedule(session, et(3, 7))
    assert local(done) == "03 11:40" and local(nxt) == "03 10:00"
    lanes, _ = qm.drama_lanes([done, nxt], "youtube")
    assert lanes and lanes[0][0] is done                                # EP1 goes to YouTube first, EP2 after Facebook


@pytest.mark.parametrize("hm,expect_cut", [("10:00", True), ("13:20", False)])
def test_ick_reel_test_slot_sends_facebook_the_highlight_cut(session, monkeypatch, tmp_path, hm, expect_cut):
    cut = tmp_path / "cut.mp4"; cut.write_bytes(b"cut")
    sent, intents = {}, []

    async def list_accounts():
        return [{"id": "TheIckRoom:facebook", "profile": "TheIckRoom"}]

    async def upload_video(path, *, profile, platforms, title, description, **kw):
        sent[platforms[0]] = path
        return "r"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    monkeypatch.setattr(qm, "ick_highlight_cut", lambda p: (cut, {"cut_end": 75.0, "cut_rule": "pause nearest 75 s"}))
    monkeypatch.setattr(qm, "facebook_media_type", lambda p: ("REELS" if p == cut else "VIDEO", 1.0))
    h, m = map(int, hm.split(":"))
    it = QueueItem(pipeline=qm.ICK_ROOM, status="ready", accounts=["TheIckRoom:facebook"], title="Hook",
                   description="Body #tonightsconversation #theickroom", tags="#tonightsconversation #theickroom",
                   video_path=__file__, scheduled_at=et(7, h, m))
    session.add(it)
    session.commit()
    asyncio.run(qm.publish_queue_item(it.id, session, regular=True))
    session.refresh(it)
    fb = next(e for e in it.publish_requests if e.get("platforms") == ["facebook"])
    assert (sent["facebook"] == cut) is expect_cut
    assert fb["ick_test"]["arm"] == ("cut" if expect_cut else "full")
    assert fb["facebook_route"]["facebook_media_type"] == ("REELS" if expect_cut else "VIDEO")


def _paths(monkeypatch, tmp_path):
    files = {}

    async def list_accounts():
        return [{"id": f"Dramarama:{n}", "profile": "Dramarama"} for n in ("facebook", "x", "youtube")]

    async def upload_video(path, *, profile, platforms, **kw):
        files[platforms[0]] = str(path)
        return "r"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    monkeypatch.setattr(qm, "x_posts_last_day", lambda s, profile, now: 0)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return files, str(video)


def test_youtube_gets_the_dialogue_only_copy_and_facebook_keeps_the_original(session, drama, monkeypatch, tmp_path):
    from backend.app import drama_dialogue
    files, video = _paths(monkeypatch, tmp_path)
    copy = tmp_path / "dialogue.mp4"
    copy.write_bytes(b"vocals")
    monkeypatch.setattr(drama_dialogue, "fetch", lambda item: (copy, None))
    it = ep(session, "Alpha", 1, accounts=("Dramarama:facebook", "Dramarama:youtube"), video_path=video)
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    session.refresh(it)
    assert files == {"facebook": video, "youtube": str(copy)}
    assert not copy.exists()                                            # temporary download removed
    yt = next(e for e in it.publish_requests if e.get("platforms") == ["youtube"])
    assert yt["audio"].startswith("dialogue-only")


def test_youtube_waits_without_a_dialogue_copy_and_stays_owed(session, drama, monkeypatch, tmp_path):
    from backend.app import drama_dialogue
    files, video = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(drama_dialogue, "fetch", lambda item: (None, "0 dialogue-only copies named abc.mp4"))
    it = ep(session, "Alpha", 1, accounts=("Dramarama:facebook", "Dramarama:youtube"), video_path=video)
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    assert files == {"facebook": video}                                 # never the original audio on YouTube
    qm.apply_result(it, {"ok": [("Dramarama:facebook", "https://fb/1")], "failed": {}}, et(3, 10, 10))
    assert it.status == "ready" and it.accounts == ["Dramarama:youtube"]


def test_paused_youtube_never_fetches_a_copy(session, monkeypatch, tmp_path):
    from backend.app import drama_dialogue
    cfg = qm.load_schedule(session)
    qm.save_schedule(session, {**cfg, "timezone": "America/New_York", "pipelines": {
        "DramaRama": {"posts_per_day": 4, "platforms": {"facebook": FB, "x": X, "youtube": 0}}}})
    try:
        files, video = _paths(monkeypatch, tmp_path)
        monkeypatch.setattr(drama_dialogue, "fetch", lambda item: pytest.fail("fetched while YouTube is paused"))
        it = ep(session, "Alpha", 1, accounts=("Dramarama:facebook", "Dramarama:youtube"), video_path=video)
        it.scheduled_at = et(3, 10)
        session.commit()
        asyncio.run(qm.publish_queue_item(it.id, session, regular=True))
        assert files == {"facebook": video}
    finally:
        qm.save_schedule(session, None)


def test_x_goes_out_even_when_dots_frozen_targets_left_it_out(session, drama, monkeypatch, tmp_path):
    files, video = _paths(monkeypatch, tmp_path)
    monkeypatch.setattr(qm, "drama_approval", lambda s, item: (["Dramarama:facebook"], None))
    monkeypatch.setattr(qm, "x_media_copy", lambda p: (None, None))
    it = ep(session, "Alpha", 1, accounts=("Dramarama:facebook", "Dramarama:x"), video_path=video)
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    assert set(files) == {"facebook", "x"}


def test_dialogue_copy_name_is_the_source_drive_id():
    from backend.app import drama_dialogue
    row = QueueItem(drive_link="https://drive.google.com/file/d/1AbC_def-GHIjkl012/view?usp=drivesdk")
    assert drama_dialogue.copy_name(row) == "1AbC_def-GHIjkl012.mp4"
    assert drama_dialogue.copy_name(QueueItem(drive_link=None)) is None
    assert drama_dialogue.fetch(QueueItem(drive_link=None)) == (None, "no Drive source to match a dialogue-only copy")
