"""Owner 2026-11-26: a Screen Central short blocked on YouTube is replaced by the next Ready video, YouTube only."""
import asyncio
import datetime

import pytest
from fastapi.testclient import TestClient

from backend.app.models import QueueItem
from backend.app.social import queue_manager as qm, upload_post

from .test_queue import add, et

UTC = datetime.timezone.utc


def blocked(s, vid="csRKTg7ACaA", source="Movie Clips / @VynixAE"):
    (it,) = add(s, 1, source=source, status="archived", accounts=["default:*"])
    it.catchup = {"done": [["default:youtube", f"https://www.youtube.com/watch?v={vid}"],
                           ["default:facebook", "https://fb/1"]]}
    s.commit()
    return it


def test_next_ready_video_is_pinned_now_and_call_is_idempotent(session):
    now = et(2026, 11, 26, 12, 50)
    b = blocked(session)
    due, nxt, later = add(session, 3, accounts=["default:*"])
    due.scheduled_at = now - datetime.timedelta(minutes=5)         # due row keeps its own slot
    nxt.scheduled_at, later.scheduled_at = et(2026, 11, 26, 14), et(2026, 11, 26, 16)
    session.commit()
    res = qm.replace_blocked_youtube(session, b.id, "csRKTg7ACaA", now)
    assert res == {"replaced": True, "replacement_id": nxt.id, "already": False, "reason": None}
    assert qm._aware(nxt.pinned_at) == qm._aware(nxt.scheduled_at) == now.replace(microsecond=0)
    assert nxt.catchup["yt_replacement_for"] == b.id and "YouTube replacement" in nxt.notes
    assert b.catchup["yt_blocked"]["replacement"] == nxt.id and b.notes.startswith("⛔ YouTube blocked")
    assert due.pinned_at is None and later.pinned_at is None
    again = qm.replace_blocked_youtube(session, b.id, "csRKTg7ACaA", now)
    assert again["already"] and again["replacement_id"] == nxt.id
    assert session.query(QueueItem).filter(QueueItem.pinned_at.isnot(None)).count() == 1


def test_at_most_two_replacements_a_day(session):
    now = et(2026, 11, 26, 12, 50)
    add(session, 5, accounts=["default:*"])
    got = [qm.replace_blocked_youtube(session, blocked(session, vid).id, vid, now)
           for vid in ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc")]
    assert [g["replaced"] for g in got] == [True, True, False]
    assert "2 replacements" in got[2]["reason"]


def test_only_screen_central_and_only_its_own_upload(session):
    now = et(2026, 11, 26, 12, 50)
    dr = blocked(session, source="DramaRama")
    with pytest.raises(qm.ReplaceRefused, match="Only Screen Central"):
        qm.replace_blocked_youtube(session, dr.id, "csRKTg7ACaA", now)
    sc = blocked(session)
    with pytest.raises(qm.ReplaceRefused, match="no YouTube upload"):
        qm.replace_blocked_youtube(session, sc.id, "zzzzzzzzzzz", now)


def test_replacement_upload_goes_to_youtube_only(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    calls = []

    async def accounts():
        return [{"id": "default:youtube", "profile": "default"}, {"id": "default:facebook", "profile": "default"},
                {"id": "default:x", "profile": "default"}]

    async def upload(path, **kw):
        calls.append(kw["platforms"])
        return "r1"

    monkeypatch.setattr(upload_post, "list_accounts", accounts)
    monkeypatch.setattr(upload_post, "upload_video", upload)
    entries = asyncio.run(qm.submit_upload(video, ["default:*"], title="t", description="", tags=[],
                                           allow=qm._YouTubeOnly()))
    assert calls == [["youtube"]]
    assert entries[0]["skipped"] == ["default:facebook", "default:x"]


def test_after_the_youtube_post_other_platforms_go_back_to_the_normal_plan(session):
    (it,) = add(session, 1, status="posting", accounts=["default:*"])
    it.pinned_at = it.scheduled_at = et(2026, 11, 26, 12, 50)
    it.catchup = {"yt_replacement_for": 953}
    it.publish_requests = [{"skipped": ["default:facebook", "default:x"]},
                           {"profile": "default", "platforms": ["youtube"], "request_id": "r1"}]
    session.commit()
    qm.apply_result(it, {"ok": [("default:youtube", "https://www.youtube.com/watch?v=xxxxxxxxxxx")], "failed": {}},
                    et(2026, 11, 26, 13))
    assert it.status == "ready" and it.accounts == ["default:facebook", "default:x"]
    assert it.pinned_at is None and it.scheduled_at is None
    assert it.catchup["yt_replaced"] == 953 and "yt_replacement_for" not in it.catchup


def test_endpoint(session):
    from backend.app.main import app
    client = TestClient(app, headers={"x-access-token": "test-token"})
    b = blocked(session)
    add(session, 1, accounts=["default:*"])
    assert client.post(f"/api/queue/{b.id}/youtube-blocked", json={"video_id": "bad"}).status_code == 400
    r = client.post(f"/api/queue/{b.id}/youtube-blocked", json={"video_id": "csRKTg7ACaA"})
    assert r.status_code == 200 and r.json()["replaced"] is True
    assert client.post("/api/queue/999999/youtube-blocked", json={"video_id": "csRKTg7ACaA"}).status_code == 404
