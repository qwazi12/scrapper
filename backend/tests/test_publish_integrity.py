"""Durability and duplicate-submission tests; never calls a posting provider."""
import asyncio
from datetime import datetime, timezone

import pytest

from backend.app.db import SessionLocal
from backend.app.models import QueueItem
from backend.app.social import queue_manager as qm, upload_post


def test_concurrent_click_and_crash_intent_are_durable(session, monkeypatch, tmp_path):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"synthetic transport fixture")
    item = QueueItem(title="Synthetic", video_path=str(video), accounts=["default:youtube"],
                     status="ready", description="Owner caption", research={})
    session.add(item); session.commit(); item_id = item.id
    calls = []
    async def submit(*args, on_intent=None, **kwargs):
        with SessionLocal() as other:
            saved = other.get(QueueItem, item_id)
            assert saved.status == "posting" and saved.publish_requests[0]["state"] == "intent"
        calls.append("provider accepted")
        with SessionLocal() as other:
            with pytest.raises(upload_post.UploadPostError, match="unresolved"):
                await qm.publish_queue_item(item_id, other)
        raise SystemExit("crash after acceptance before request ID persisted")
    monkeypatch.setattr(qm, "submit_upload", submit)
    with pytest.raises(SystemExit):
        asyncio.run(qm.publish_queue_item(item_id, session))
    assert calls == ["provider accepted"]
    with SessionLocal() as other:
        saved = other.get(QueueItem, item_id)
        assert saved.status == "posting" and saved.publish_requests[0]["state"] == "intent"
        saved.status = "ready"  # even an accidental generic requeue cannot bypass intent
        other.commit()
        with pytest.raises(upload_post.UploadPostError, match="unresolved"):
            asyncio.run(qm.publish_queue_item(item_id, other))


def test_each_platform_records_intent_and_result_independently(tmp_path, monkeypatch):
    video = tmp_path / "fixture.mp4"; video.write_bytes(b"fixture")
    events = []
    async def accounts(): return [{"id": f"default:{p}", "profile": "default"} for p in ("youtube", "instagram")]
    async def upload(path, **kwargs):
        platform = kwargs["platforms"][0]
        assert events[-1] == ("intent", platform)
        if platform == "instagram": raise TimeoutError("accepted but response lost")
        return "synthetic-youtube-id"
    monkeypatch.setattr(upload_post, "list_accounts", accounts)
    monkeypatch.setattr(upload_post, "upload_video", upload)
    result = asyncio.run(qm.submit_upload(video, ["default:*"], title="fixture", description="caption", tags=[],
        on_intent=lambda e: events.append(("intent", e["platforms"][0])),
        on_result=lambda e: events.append((e["state"], e["platforms"][0]))))
    assert events == [("intent", "youtube"), ("accepted", "youtube"), ("intent", "instagram"), ("unknown", "instagram")]
    assert result[0]["request_id"] == "synthetic-youtube-id" and result[1]["state"] == "unknown"
    assert qm.resolve_results(result, {}, datetime.now(timezone.utc)) is None
