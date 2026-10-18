"""Real image artifacts and exact-export gates, with fake transport only."""
import asyncio
from datetime import datetime, timezone
import io
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import CreativeCampaign, CreativeExport, EditorialRecord, MediaAsset, QueueItem
from backend.app.social import queue_manager as qm, upload_post


@pytest.fixture
def client():
    return TestClient(app, headers={"x-access-token": "test-token"})


def research(client):
    return client.post("/api/editorial/opportunities", json={"title": "An original shape story",
        "angle": "Explain original shapes through three deliberately different visual beats.",
        "audience": "Viewers learning animation", "formats": ["short", "carousel"], "evidence": [
            {"url": "https://example.org/cleared-fixture", "excerpt": "These colored shapes were originally created for this synthetic test.",
             "entity": "Synthetic shapes", "retrieved_at": datetime.now(timezone.utc).isoformat()}]}).json()["id"]


def brief(oid):
    return {"opportunity_id": oid, "title": "A shape tells a story", "formats": ["short", "carousel"],
        "beats": [{"role": "hook", "text": "Can one simple shape change your whole story?", "source_refs": [1]},
                  {"role": "body", "text": "Changing its position gives each original frame a different emphasis.", "source_refs": [1]},
                  {"role": "payoff", "text": "The payoff comes from the change. Which frame would you choose?", "source_refs": [1]}],
        "short_caption": "Watch three original shapes change the emphasis of a scene.",
        "carousel_caption": "Swipe through the three frames, then choose the clearest payoff.",
        "community_question": "Which visual change made the biggest difference?"}


def rendered(client):
    oid = research(client)
    assert client.post(f"/api/editorial/opportunities/{oid}/approve").status_code == 200
    result = client.post("/api/editorial/campaigns", json=brief(oid))
    assert result.status_code == 200, result.text
    cid = result.json()["id"]
    result = client.post(f"/api/editorial/campaigns/{cid}/render/carousel")
    assert result.status_code == 200, result.text
    return oid, cid, result.json()


def test_campaign_requires_reviewed_research_and_valid_beat_references(client):
    oid = research(client)
    assert client.post("/api/editorial/campaigns", json=brief(oid)).status_code == 409
    client.post(f"/api/editorial/opportunities/{oid}/approve")
    data = brief(oid); data["beats"][1]["source_refs"] = [99]
    assert client.post("/api/editorial/campaigns", json=data).status_code == 409
    data = brief(oid); data["beats"][0]["role"] = "body"
    assert client.post("/api/editorial/campaigns", json=data).status_code == 422


def test_actual_carousel_approval_enqueue_and_simulated_photo_receipts(client, monkeypatch):
    oid, cid, result = rendered(client)
    eid = result["id"]
    manifest = result["manifest"]
    assert len(manifest["cards"]) == 3 and manifest["quality"]["decode"] == "passed"
    for card in manifest["cards"]:
        with Image.open(card["file"]) as image:
            image.load(); assert image.size == (1080, 1350)
        assert 1 <= card["caption_lines"] <= 5 and card["font_px"] == 54
    data = {"accounts": ["default:instagram"]}
    assert client.post(f"/api/editorial/exports/{eid}/enqueue", json=data).status_code == 409
    assert client.post(f"/api/editorial/exports/{eid}/approve", json={"reviewed": False}).status_code == 422
    assert client.post(f"/api/editorial/exports/{eid}/approve", json={"reviewed": True}).status_code == 200
    assert client.post(f"/api/editorial/exports/{eid}/enqueue", json={"accounts": ["default:youtube"]}).status_code == 400
    first = client.post(f"/api/editorial/exports/{eid}/enqueue", json=data).json()
    second = client.post(f"/api/editorial/exports/{eid}/enqueue", json=data).json()
    assert first["created"] and not second["created"] and first["queue_item_id"] == second["queue_item_id"]
    assert client.patch(f"/api/queue/{first['queue_item_id']}", json={"description": "Changed after approval"}).status_code == 409
    calls = []
    async def accounts(): return [{"id": "default:instagram", "profile": "default"}]
    async def upload(paths, **kwargs):
        with SessionLocal() as check:
            saved = check.get(QueueItem, first["queue_item_id"])
            entry = saved.publish_requests[-1]
            assert entry["state"] == "intent" and entry["client_request_id"] == kwargs["idempotency_key"]
        calls.append(paths)
        return "fake-image-receipt"
    monkeypatch.setattr(upload_post, "list_accounts", accounts)
    monkeypatch.setattr(upload_post, "upload_images", upload)
    with SessionLocal() as s:
        item = asyncio.run(qm.publish_queue_item(first["queue_item_id"], s))
        assert item.status == "posting" and item.publish_requests[0]["request_id"] == "fake-image-receipt"
    assert [[str(p) for p in paths] for paths in calls] == [[c["file"] for c in manifest["cards"]]]
    # A second render is a different immutable version; it never overwrites the first.
    again = client.post(f"/api/editorial/campaigns/{cid}/render/carousel").json()
    assert again["id"] != eid and Path(manifest["cards"][0]["file"]).is_file()
    assert client.get(f"/api/editorial/exports/{eid}/file/card-1.png").status_code == 200
    Path(manifest["cards"][0]["file"]).write_bytes(b"tampered")
    assert client.post(f"/api/editorial/exports/{eid}/approve", json={"reviewed": True}).status_code == 409


def test_asset_index_strips_metadata_deduplicates_and_checks_attestation(client):
    image = io.BytesIO(); Image.new("RGB", (640, 360), "blue").save(image, format="PNG")
    data = {"license": "owner_created", "attestation": "Original synthetic blue frame created by this local test", "tags": "shape, blue"}
    a = client.post("/api/editorial/assets", data=data, files={"file": ("../outside.png", image.getvalue(), "image/png")})
    b = client.post("/api/editorial/assets", data=data, files={"file": ("same.png", image.getvalue(), "image/png")})
    assert a.status_code == 200 and b.json() == {"id": a.json()["id"], "duplicate": True}
    assert client.post("/api/editorial/assets", data={**data, "attestation": "no"}, files={"file": ("x", image.getvalue())}).status_code == 422
    assert client.post("/api/editorial/assets", data=data, files={"file": ("x", b"not an image")}).status_code == 400


def test_photo_transport_uses_ordered_multipart_and_persistable_request_key(tmp_path, monkeypatch):
    paths = []
    for i in range(3):
        path = tmp_path / f"slide-{i}.png"; Image.new("RGB", (10, 10), "red").save(path); paths.append(path)
    requests = []
    def capture(request):
        requests.append(request)
        assert request.url.path == "/api/upload_photos" and request.headers["Idempotency-Key"] == "synthetic-key"
        body = request.read()
        assert body.count(b'name="photos[]"') == 3 and b'synthetic-key' in body
        return httpx.Response(200, json={"request_id": "synthetic-key"})
    real = httpx.AsyncClient
    monkeypatch.setattr(upload_post.settings, "uploadpost_api_key", "synthetic-key")
    monkeypatch.setattr(upload_post.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(capture), **kw))
    assert asyncio.run(upload_post.upload_images(paths, profile="default", platforms=["instagram"], title="Synthetic",
        description="Original test slides", idempotency_key="synthetic-key")) == "synthetic-key"
    assert len(requests) == 1


def test_expired_native_render_can_recover_without_overwriting_old_export(client):
    from backend.app.models import WorkerLease
    from backend.app import worker_leases as leases
    from datetime import timedelta
    oid,cid,first=rendered(client)
    with SessionLocal() as s:
        row=s.get(CreativeCampaign,cid); row.stage_status='running'; row.work_token='dead-process'
        lease=s.get(WorkerLease,f'native:{cid}'); lease.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        s.commit()
    result=client.post(f'/api/editorial/campaigns/{cid}/render/carousel')
    assert result.status_code==200, result.text
    assert result.json()['id']!=first['id']
    assert Path(first['manifest']['cards'][0]['file']).is_file()
