"""Owner 2026-10-19: a definite refusal goes to Retry; only lost responses stay uncertain,
and the owner can clear an uncertain one in one tap. Never calls a posting provider."""
import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.models import QueueItem
from backend.app.social import queue_manager as qm, upload_post


def test_only_answered_refusals_count_as_definite():
    assert qm.definitely_rejected(upload_post.UploadPostError("Instagram description is too long", 400))
    assert qm.definitely_rejected(upload_post.UploadPostError("bad request", 422))
    for code in (408, 409, 500, 502):
        assert not qm.definitely_rejected(upload_post.UploadPostError("unsure", code))
    assert not qm.definitely_rejected(httpx.ReadTimeout("lost response"))


def _accounts(monkeypatch, nets=("youtube", "instagram")):
    async def accounts():
        return [{"id": f"Cinama:{n}", "profile": "Cinama"} for n in nets]
    monkeypatch.setattr(upload_post, "list_accounts", accounts)


def test_refused_platform_is_rejected_and_lost_one_stays_unknown(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"x")
    _accounts(monkeypatch)
    async def upload(path, **kw):
        if kw["platforms"] == ["instagram"]:
            raise upload_post.UploadPostError("Instagram description is too long (3723)", 400)
        raise httpx.ReadTimeout("response lost")
    monkeypatch.setattr(upload_post, "upload_video", upload)
    out = asyncio.run(qm.submit_upload(video, ["Cinama:*"], title="t", description="d", tags=[]))
    assert {e["platforms"][0]: e["state"] for e in out} == {"youtube": "unknown", "instagram": "rejected"}


def test_every_platform_refusing_sends_the_video_to_retry_and_it_can_post_again(session, monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"synthetic")
    item = QueueItem(title="Refused", video_path=str(video), accounts=["Cinama:*"], status="ready",
                     description="caption", research={})
    session.add(item); session.commit()
    _accounts(monkeypatch)
    async def refuse(path, **kw):
        raise upload_post.UploadPostError("Instagram description is too long (3723)", 400)
    monkeypatch.setattr(upload_post, "upload_video", refuse)
    with pytest.raises(upload_post.UploadPostError):
        asyncio.run(qm.publish_queue_item(item.id, session))
    session.refresh(item)
    assert item.status == "retry" and item.notes.startswith("Not posted")
    assert all(e.get("profile") and e["state"] == "rejected" for e in item.publish_requests)   # no batch claim left

    sent = []
    async def accept(path, **kw):
        sent.append(kw["platforms"][0]); return f"rid-{kw['platforms'][0]}"
    monkeypatch.setattr(upload_post, "upload_video", accept)
    item.status = "ready"; session.commit()                  # what the owner's Retry does
    asyncio.run(qm.publish_queue_item(item.id, session))
    session.refresh(item)
    assert item.status == "posting" and sorted(sent) == ["instagram", "youtube"]


def test_lost_response_still_waits_for_the_owner(session, monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"synthetic")
    item = QueueItem(title="Lost", video_path=str(video), accounts=["Cinama:youtube"], status="ready",
                     description="caption", research={})
    session.add(item); session.commit()
    _accounts(monkeypatch, ("youtube",))
    async def lost(path, **kw):
        raise httpx.ReadTimeout("response lost")
    monkeypatch.setattr(upload_post, "upload_video", lost)
    asyncio.run(qm.publish_queue_item(item.id, session))
    session.refresh(item)
    assert item.status == "posting" and item.publish_requests[-1]["state"] == "unknown"


def test_owner_one_tap_not_posted_returns_only_those_platforms_to_retry(session):
    from backend.app.main import app
    item = QueueItem(title="Uncertain", accounts=["Cinama:*"], status="posting", publish_requests=[
        {"state": "intent", "client_request_id": "batch", "accounts": ["Cinama:*"]},
        {"state": "unknown", "profile": "Cinama", "platforms": ["instagram"], "client_request_id": "a1"},
        {"state": "rejected", "profile": "Cinama", "platforms": ["facebook"], "client_request_id": "a2", "error": "400"}])
    session.add(item); session.commit()
    client = TestClient(app, headers={"x-access-token": "test-token"})
    r = client.post(f"/api/queue/{item.id}/not-posted")
    assert r.status_code == 200, r.text
    session.refresh(item)
    assert item.status == "retry" and item.accounts == ["Cinama:instagram"]
    assert [e["state"] for e in item.publish_requests] == ["confirmed_not_accepted", "rejected"]
    assert client.post(f"/api/queue/{item.id}/not-posted").status_code == 409          # nothing uncertain now


def test_not_posted_keeps_waiting_when_another_platform_was_accepted(session):
    from backend.app.main import app
    item = QueueItem(title="Half", accounts=["Cinama:*"], status="posting", publish_requests=[
        {"state": "accepted", "profile": "Cinama", "platforms": ["youtube"], "client_request_id": "y", "request_id": "r"},
        {"state": "unknown", "profile": "Cinama", "platforms": ["instagram"], "client_request_id": "i"}])
    session.add(item); session.commit()
    r = TestClient(app, headers={"x-access-token": "test-token"}).post(f"/api/queue/{item.id}/not-posted")
    assert r.status_code == 200
    session.refresh(item)
    assert item.status == "posting" and item.publish_requests[1]["state"] == "confirmed_not_accepted"
