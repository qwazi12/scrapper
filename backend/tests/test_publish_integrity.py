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


def test_two_project_enqueue_requests_create_only_one_queue_row(session):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from backend.app.models import StudioProject
    from backend.app.social import enqueue
    p = StudioProject(tmdb_id=998, title="Concurrent enqueue")
    session.add(p); session.commit(); pid = p.id
    barrier = threading.Barrier(2)
    def create():
        with SessionLocal() as s:
            project = s.get(StudioProject, pid)
            barrier.wait(timeout=5)
            item, created = enqueue.claim(s, project, QueueItem(title="one export", status="ready", accounts=[]))
            s.commit()
            return item.id, created
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create) for _ in range(2)]
        results = [f.result(timeout=10) for f in futures]
    assert results[0][0] == results[1][0] and sum(created for _, created in results) == 1
    assert session.query(QueueItem).filter(QueueItem.title == "one export").count() == 1


def test_unknown_submission_cannot_be_edited_deleted_or_reapproved_via_api(session):
    from backend.app.main import app
    from fastapi.testclient import TestClient
    item = QueueItem(title="Uncertain", accounts=["default:youtube"], status="posting",
                     publish_requests=[{"state": "unknown", "profile": "default", "platforms": ["youtube"]}])
    session.add(item); session.commit(); item_id = item.id
    client = TestClient(app, headers={"x-access-token": "test-token"})
    assert client.patch(f"/api/queue/{item_id}", json={"title": "Changed", "status": "ready"}).status_code == 409
    assert client.delete(f"/api/queue/{item_id}").status_code == 409
    assert client.post(f"/api/queue/{item_id}/approve").status_code == 409
    assert client.post("/api/queue/bulk-action", json={"ids": [item_id], "action": "approve"}).status_code == 409
    session.refresh(item)
    assert item.title == "Uncertain" and item.status == "posting"


def test_legacy_direct_submit_is_retired_before_any_provider_io(monkeypatch):
    from backend.app.main import app
    from fastapi.testclient import TestClient
    async def forbidden(*a, **kw): raise AssertionError("Must use durable queue")
    monkeypatch.setattr(qm, "submit_upload", forbidden)
    response = TestClient(app, headers={"x-access-token": "test-token"}).post("/api/social/publish",
        json={"compilation_id": 1, "account_ids": ["default:youtube"], "content": "Synthetic"})
    assert response.status_code == 410 and "review queue" in response.json()["detail"]
