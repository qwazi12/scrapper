"""(Named to run after test_drama_media: a send here adds Dramarama journal events, which are append-only.)
DramaRama Long: cumulative 45-min compilations per series, Facebook + YouTube, every other day (owner, 2026-11-26)."""
import asyncio
import datetime
import json
import pathlib
import subprocess
from zoneinfo import ZoneInfo

import pytest

from backend.app.models import QueueItem
from backend.app.social import queue_manager as qm, upload_post as up
from backend.app.studio.compilations import drama_long as dl

ET = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc
MIN = 60.0


def et(d, h, mi=0):
    return datetime.datetime(2026, 11, d, h, mi, tzinfo=ET).astimezone(UTC)


@pytest.fixture
def paced(session):
    cfg = qm.load_schedule(session)
    qm.save_schedule(session, {**cfg, "timezone": "America/New_York", "pipelines": {
        "DramaRama Long": {"platforms": {"facebook": ["20:00"], "youtube": ["22:00"]}, "every_days": 2}}})
    yield
    qm.save_schedule(session, None)


# --- part boundaries ----------------------------------------------------------------------------------
def test_parts_are_cumulative_45_minute_whole_episodes_and_the_last_is_the_full_series():
    durs = [2 * MIN] * 60                                 # 120 min
    assert dl.part_size(durs, 1) == 22                    # 44 min
    assert dl.part_size(durs, 2) == 45                    # 90 min
    assert dl.part_size(durs, 3) == 60                    # the rest: full series
    assert dl.part_size(durs, 4) == 60


def test_a_short_tail_joins_the_part_before_and_a_short_series_is_one_part():
    durs = [2 * MIN] * 26                                 # 52 min: 8 min left after Part 1
    assert dl.part_size(durs, 1) == 26
    assert dl.part_size([2 * MIN] * 10, 1) == 10
    assert dl.part_size([50 * MIN, 50 * MIN], 1) == 1     # one long episode still makes progress
    assert dl.part_size([50 * MIN, 50 * MIN], 2) == 2


# --- every other day ---------------------------------------------------------------------------------
def test_every_other_day_skips_the_off_days(session, paced):
    slots = qm.slots_after(et(1, 0), 6, pipeline="DramaRama Long")
    days = {x.astimezone(ET).date() for x in slots}
    assert len(days) == 3 and all(d.toordinal() % 2 == 0 for d in days)
    assert sorted({x.astimezone(ET).strftime("%H:%M") for x in slots}) == ["20:00", "22:00"]
    assert qm.slots_after(et(1, 0), 3, pipeline="Movie Clips")[0].astimezone(ET).date().day == 1   # others unchanged


def test_every_days_is_validated():
    base = qm.default_schedule()
    for bad in (0, 8):
        with pytest.raises(ValueError, match="every_days"):
            qm.validate_schedule({**base, "pipelines": {"DramaRama Long": {"times": ["20:00"], "every_days": bad}}})
    ok = qm.validate_schedule({**base, "pipelines": {"DramaRama Long": {"times": ["20:00"], "every_days": 2}}})
    assert ok["pipelines"]["DramaRama Long"]["every_days"] == 2


# --- destinations and sending --------------------------------------------------------------------------
def test_only_dramarama_long_may_use_the_dramarama_profile_besides_dramarama():
    assert qm.blocked_accounts(["Dramarama:facebook"], "DramaRama Long") == []
    assert qm.blocked_accounts(["Cinama:youtube"], "DramaRama Long") == ["Cinama:youtube"]
    assert qm.blocked_accounts(["Dramarama:facebook"], "Movie Clips") == ["Dramarama:facebook"]


def _long(s, accounts, video_path, **kw):
    it = QueueItem(pipeline="DramaRama Long", source="DramaRama / Alpha [123]", video_name="DRL|123|P1|facebook",
                   title="Alpha — Part 1 (Episodes 1–20)", description="Watch Alpha\n\nChapters:\n0:00 Episode 1",
                   tags="#DramaRama #shorts #revenge", accounts=accounts, status="ready", video_path=video_path,
                   research={"kind": "drama_long"}, **kw)
    s.add(it)
    s.commit()
    return it


def test_a_long_row_posts_without_dot_permits_and_without_shorts_tags(session, paced, monkeypatch, tmp_path):
    sent = []

    async def list_accounts():
        return [{"id": f"Dramarama:{n}", "profile": "Dramarama"} for n in ("facebook", "x", "youtube")]

    async def upload_video(path, *, profile, platforms, **kw):
        sent.append((sorted(platforms), kw.get("description", ""), json.dumps(kw.get("platform_overrides") or {})))
        return "req1"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    it = _long(session, ["Dramarama:facebook"], str(v))
    it.scheduled_at = qm.slots_after(et(1, 0), 1, pipeline="DramaRama Long")[0]   # a Facebook 20:00 posting day
    session.commit()
    assert not qm.drama_guarded(session, it)
    asyncio.run(qm.publish_queue_item(it.id, session, regular=True))
    assert [p for p, _, _ in sent] == [["facebook"]]
    assert "#shorts" not in (sent[0][1] + sent[0][2]).lower()


@pytest.mark.parametrize("acc", ["Dramarama:x", "Dramarama:*"])
def test_x_and_whole_profile_are_refused(session, paced, monkeypatch, tmp_path, acc):
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    it = _long(session, [acc], str(v))
    with pytest.raises(up.UploadPostError, match="only to Dramarama:facebook"):
        asyncio.run(qm.publish_queue_item(it.id, session, regular=True))


def test_long_rows_never_count_toward_the_shorts_day(session, paced):
    it = _long(session, ["Dramarama:facebook"], "v.mp4")
    it.status, it.scheduled_at = "posted", et(4, 20)
    it.publish_requests = [{"platforms": ["facebook"]}]
    session.commit()
    assert qm.posted_today(session, "DramaRama", et(4, 21)) == 0
    assert qm.posted_today(session, "DramaRama Long", et(4, 21)) == 1


# --- planner -------------------------------------------------------------------------------------------
def ep(s, series, n, status="review", sid="777"):
    it = QueueItem(pipeline="DramaRama", source=f"DramaRama / {series} [{sid}]", status=status,
                   accounts=["Dramarama:*"], video_name=f"x_{series}_EP{n:03d}.mp4",
                   title=f"Hook | {series} Ep. {n}", description="d", tags="#DramaRama #shorts #revenge",
                   drive_link=f"https://drive.google.com/file/d/{sid}ep{n:04d}xx/view")
    s.add(it)
    s.commit()
    return it


def test_planner_order_one_facebook_part_ahead_youtube_follows(session, paced):
    for n in range(1, 4):
        ep(session, "Alpha", n, sid="111")
        ep(session, "Beta", n, sid="222")
    now = et(4, 12)
    assert dl.next_job(session, now) == ("DramaRama / Alpha [111]", 1, "facebook")
    fb = _long(session, ["Dramarama:facebook"], "v.mp4")
    fb.source, fb.video_name = "DramaRama / Alpha [111]", dl.key("111", 1, "facebook")
    fb.research = {"kind": "drama_long", "series": fb.source, "part": 1, "net": "facebook", "final": False}
    session.commit()
    assert dl.next_job(session, now) == ("DramaRama / Alpha [111]", 1, "youtube")
    dl._failed[dl.key("111", 1, "youtube")] = now                   # its dialogue copies aren't there yet
    assert dl.next_job(session, now) is None                         # and Part 2 waits for Part 1 to post
    fb.status = "posted"
    session.commit()
    assert dl.next_job(session, now) == ("DramaRama / Alpha [111]", 2, "facebook")
    fb.research = {**fb.research, "final": True}
    session.commit()
    assert dl.next_job(session, now) == ("DramaRama / Beta [222]", 1, "facebook")
    dl._failed.clear()


def test_nothing_is_made_without_the_every_other_day_schedule(session, monkeypatch):
    ep(session, "Alpha", 1)
    monkeypatch.setattr(dl.control, "start_thread", lambda *a, **k: pytest.fail("started"))
    assert dl.maybe_start(session, et(4, 12)) is None
    monkeypatch.setenv("DRAMA_LONG", "off")
    assert dl.maybe_start(session, et(4, 12)) is None


# --- render (real ffmpeg on tiny clips; downloads faked) -----------------------------------------------
def _clip(path: pathlib.Path, seconds: float, size="180x320"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_render_makes_cumulative_parts_with_chapters_and_youtube_uses_dialogue_copies(session, monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "root", lambda: tmp_path / "drl")
    monkeypatch.setattr(dl, "PART_SECONDS", 5)          # "45 min" = 5 s here
    monkeypatch.setattr(dl, "TAIL_SECONDS", 1)
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for n in range(1, 6):
        ep(session, "Alpha", n, sid="111", status="ready" if n < 3 else "review")
        _clip(src_dir / f"{n}.mp4", 2, size="180x320" if n != 4 else "200x320")   # Ep. 4 a different size
    pulled = []

    def fake_download(link, dest_dir=None):
        n = int(link.split("ep")[1][:4])
        out = pathlib.Path(dest_dir) / f"raw{n}.mp4"
        out.write_bytes((src_dir / f"{n}.mp4").read_bytes())
        pulled.append(n)
        return out
    monkeypatch.setattr("backend.app.drive_sync.download_drive_file", fake_download)
    from backend.app import drama_dialogue
    missing = {"on": True}

    def fake_dialogue(item):
        if missing["on"]:
            return None, "not made yet"
        n = qm.drama_series_episode(item)[1]
        out = tmp_path / f"dlg{n}.mp4"
        out.write_bytes((src_dir / f"{n}.mp4").read_bytes())
        return out, None
    monkeypatch.setattr(drama_dialogue, "fetch", fake_dialogue)

    src = "DramaRama / Alpha [111]"
    p1 = session.get(QueueItem, dl.render(src, 1, "facebook"))
    assert p1.research["episodes"] == [1, 2] and not p1.research["final"]
    assert p1.accounts == ["Dramarama:facebook"] and p1.status == "ready" and p1.pipeline == "DramaRama Long"
    assert p1.title == "Alpha — Part 1 (Episodes 1–2)"
    assert "0:00 Episode 1" in p1.description and "0:02 Episode 2" in p1.description
    assert "#shorts" not in p1.tags and "#DramaRama" in p1.tags
    assert 3.5 < p1.research["seconds"] < 4.6
    assert dl.render(src, 1, "facebook") is None                     # idempotent
    with pytest.raises(dl._Wait):
        dl.render(src, 1, "youtube")                                  # waits, never the original audio
    missing["on"] = False
    y1 = session.get(QueueItem, dl.render(src, 1, "youtube"))
    assert y1.accounts == ["Dramarama:youtube"] and y1.research["episodes"] == [1, 2]
    n_pulled = len(pulled)
    p2 = session.get(QueueItem, dl.render(src, 2, "facebook"))
    assert len(pulled) == n_pulled                                   # episodes cached, not downloaded again
    assert p2.research["episodes"] == [1, 2, 3, 4, 5] and p2.research["final"]
    assert p2.title == "Alpha — Full Series (All 5 Episodes)"
    assert pathlib.Path(p2.video_path).is_file()
