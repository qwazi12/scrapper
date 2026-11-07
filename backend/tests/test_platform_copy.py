"""Per-platform copy and the Posting Review reader (owner, 2026-11-07)."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import posting_review
from backend.app.main import app
from backend.app.models import QueueItem
from backend.app.social import platform_copy as pc
from backend.app.social import queue_manager as qm
from backend.app.social import upload_post as up


@pytest.fixture
def client():
    return TestClient(app, headers={"x-access-token": "test-token"})


def test_clean_keeps_known_fields_and_refuses_hashtags_and_long_x():
    assert pc.clean({"youtube": {"title": " T ", "description": "D"}, "x": {"text": ""}}) == {"youtube": {"title": "T", "description": "D"}}
    assert pc.clean({}) is None
    for bad in ({"tiktok": {"text": "a"}}, {"x": {"caption": "a"}}, {"x": {"text": "a\n\n#tag"}}, {"x": {"text": "a" * 281}},
                {"youtube": {"title": "t" * 101}}):
        with pytest.raises(ValueError):
            pc.clean(bad)


def test_x_drops_its_hashtags_rather_than_go_over_280():
    assert pc.fit_x("words", "words\n\n#a #b") == "words\n\n#a #b"
    assert pc.fit_x("w" * 275, "w" * 275 + "\n\n#Disturbia #MovieClips") == "w" * 275


def test_rows_by_tab_uses_the_queue_id_header_row():
    got = posting_review.rows_by_tab({"How to use": [["What this is"]],
                                      "ICK": [["#", "Queue ID", "Decision"], ["1", "4873", "Approve"], [], ["2", "4867"]]})
    assert got == {"ICK": [{"#": "1", "Queue ID": "4873", "Decision": "Approve"}, {"#": "2", "Queue ID": "4867", "Decision": ""}]}


def test_patch_validates_platform_copy(client, session):
    it = QueueItem(pipeline="Movie Clips", source="Movie Clips / t", status="review", title="t", description="d", video_path=__file__)
    session.add(it)
    session.commit()
    ok = client.patch(f"/api/queue/{it.id}", json={"platform_copy": {"x": {"text": "Hello"}}})
    assert ok.status_code == 200 and ok.json()["platform_copy"] == {"x": {"text": "Hello"}}
    assert client.patch(f"/api/queue/{it.id}", json={"platform_copy": {"x": {"text": "Hi #tag"}}}).status_code == 200
    assert client.patch(f"/api/queue/{it.id}", json={"platform_copy": {"x": {"text": "Hi\n\n#tag"}}}).status_code == 400


def test_each_platform_gets_its_own_words_plus_its_hashtags(session, monkeypatch):
    sent = {}

    async def list_accounts():
        return [{"id": f"Cinama:{n}", "profile": "Cinama"} for n in ("youtube", "facebook", "instagram", "x")]

    async def upload_video(path, *, profile, platforms, title, description, **kw):
        sent[platforms[0]] = (title, description)
        return "r"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    it = QueueItem(pipeline="Movie Clips", source="Movie Clips / test", status="ready", accounts=["Cinama:*"],
                   title="FB title", description="FB words.", tags="#shorts #Disturbia #ShiaLaBeouf #Thriller", video_path=__file__,
                   platform_copy={"youtube": {"title": "YT title", "description": "YT words."},
                                  "instagram": {"caption": "IG words."}, "x": {"text": "X words."}})
    session.add(it)
    session.commit()
    asyncio.run(qm.publish_queue_item(it.id, session, regular=False))
    assert sent["youtube"] == ("YT title", "YT words.\n\n#Disturbia #MovieClips #ShiaLaBeouf #Thriller #shorts")
    assert sent["facebook"] == ("FB title", "FB words.\n\n#Disturbia #MovieClips #ShiaLaBeouf")
    assert sent["instagram"][1] == "IG words.\n\n#Disturbia #MovieClips #ShiaLaBeouf #Thriller"
    assert sent["x"] == ("X words.\n\n#Disturbia #MovieClips", "")
