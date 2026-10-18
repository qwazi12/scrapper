from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import pytest

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import EditorialRecord, PerformanceObservation


@pytest.fixture
def client():
    with SessionLocal() as s:
        s.query(EditorialRecord).filter(EditorialRecord.kind.in_(["feedback", "experiment"])).delete(synchronize_session=False)
        s.query(PerformanceObservation).delete(); s.commit()
    return TestClient(app, headers={"x-access-token": "test-token"})


def test_feedback_is_deidentified_deduped_and_private(client):
    payload = {"platform": "youtube", "content_id": "synthetic-content", "source": "Owner authorized synthetic export",
        "comments": ["How does framing change attention? Email me at person@example.org @viewer +1 (555) 123-4567 https://example.org/me",
                     "Actually that role is incorrect. Please check the credits.", "Click my crypto giveaway link!"]}
    first = client.post("/api/editorial/feedback/import", json=payload).json()
    assert len(first["created"]) == 3 and first["delivery"] == "private_drafts_only"
    assert client.post("/api/editorial/feedback/import", json=payload).json()["created"] == []
    data = client.get("/api/editorial/learning").json()
    text = str(data)
    for private in ("person@example.org", "@viewer", "555", "https://example.org/me"):
        assert private not in text
    assert {r["category"] for r in data["feedback"]} == {"question", "correction", "possible_spam"}
    assert all(not r["approved_for_manual_use"] for r in data["feedback"])
    fid = first["created"][0]
    result = client.put(f"/api/editorial/feedback/{fid}/draft", json={"text": "We will review the framing examples with the cited sources.", "approved_for_manual_use": True}).json()
    assert result["approved_for_manual_use"] is True and result["reviewed_at"]
    # No sending route exists; owner approval is only for manual copy/use.
    assert client.post(f"/api/editorial/feedback/{fid}/send").status_code == 404
    assert client.post("/api/editorial/feedback/import", json={**payload, "author_email": "private@example.org"}).status_code == 422


def experiment_data():
    return {"name": "Original hook experiment", "hypothesis": "A clear visual question may improve engaged viewing at 24 hours.",
        "platform": "youtube", "format": "short", "control_ids": [f"a{i}" for i in range(5)],
        "variant_ids": [f"b{i}" for i in range(5)], "metric": "engaged_views", "age_hours": 24,
        "minimum_per_group": 5, "controls": "Keep subject, length, cadence and audience comparable; vary only the opening question."}


def test_experiments_require_comparable_observations_and_never_invent_winner(client):
    assert client.post("/api/editorial/experiments", json=experiment_data()).status_code == 200
    result = client.get("/api/editorial/learning").json()["experiments"][0]["result"]
    assert result["status"] == "insufficient_comparable_data" and result["relative_median_change_percent"] is None
    now = datetime.now(timezone.utc)
    for group, value in [("a", 100), ("b", 150)]:
        for i in range(5):
            data = {"platform": "youtube", "content_id": f"{group}{i}", "format": "short",
                "published_at": (now - timedelta(hours=25)).isoformat(), "observed_at": (now - timedelta(hours=1)).isoformat(),
                "metrics": {"engaged_views": value}, "source": "Synthetic authorized performance fixture"}
            assert client.post("/api/editorial/observations", json=data).status_code == 200
    result = client.get("/api/editorial/learning").json()["experiments"][0]["result"]
    assert result["status"] == "descriptive_comparison" and result["relative_median_change_percent"] == 50
    assert result["design"] == "retrospective" and result["winner"] is None
    invalid = experiment_data(); invalid["variant_ids"] = invalid["control_ids"]
    assert client.post("/api/editorial/experiments", json=invalid).status_code == 422
