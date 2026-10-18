from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import pytest

from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import EditorialRecord, PerformanceObservation

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def empty_editorial():
    with SessionLocal() as s:
        s.query(PerformanceObservation).delete(); s.query(EditorialRecord).delete(); s.commit()


@pytest.fixture
def client():
    return TestClient(app, headers={"x-access-token": "test-token"})


def opportunity_data():
    return {"title": "Synthetic shape story", "angle": "Compare the changing shapes through an original visual analogy.",
            "audience": "Animation curious viewers", "formats": ["short", "carousel"], "evidence": [
                {"url": "https://example.org/owner-created", "excerpt": "These are original synthetic shapes made for local testing.",
                 "entity": "Synthetic shapes", "retrieved_at": NOW.isoformat()}]}


def observation(content="video1", hours=24, fmt="short", metrics=None):
    return {"platform": "youtube", "content_id": content, "format": fmt, "published_at": (NOW - timedelta(hours=hours + 1)).isoformat(),
            "observed_at": (NOW - timedelta(hours=1)).isoformat(), "metrics": metrics or {"views": 100},
            "source": "Synthetic authorized export fixture"}


def test_opportunity_calendar_and_edit_invalidate_approval(client):
    data = opportunity_data()
    row = client.post("/api/editorial/opportunities", json=data).json(); oid = row["id"]
    assert row["status"] == "draft"
    assert client.post(f"/api/editorial/opportunities/{oid}/approve").json()["approval"]["fingerprint"]
    changed = client.put(f"/api/editorial/opportunities/{oid}", json={**data, "planned_for": (NOW + timedelta(days=2)).isoformat()}).json()
    assert changed["status"] == "draft" and "approval" not in changed
    rows = client.get("/api/editorial/opportunities").json()
    assert rows[0]["assessment"]["selection"] == "manual"
    assert rows[0]["assessment"]["recognized_sources"] == 0
    assert "not verified" in rows[0]["assessment"]["uncertainty"][0]


def test_unavailable_private_metrics_are_not_zero(client):
    report = client.get("/api/editorial/insights").json()
    assert all(a["status"] == "unavailable" and a["live_metrics"] is None for a in report["adapters"])
    assert report["cohorts"] == []
    assert client.post("/api/editorial/observations", json=observation()).status_code == 200
    report = client.get("/api/editorial/insights").json()
    assert report["adapters"][0]["status"] == "unavailable"  # an import is not OAuth authorization
    cohort = report["cohorts"][0]
    assert cohort["medians"]["engaged_views"] is None and cohort["medians"]["views"] == 100
    assert cohort["metric_samples"]["engaged_views"] == 0 and cohort["confidence"] == "small_sample"


def test_import_is_idempotent_and_cohorts_never_mix_age_or_format(client):
    for content, age, fmt, views in [("a", 24, "short", 100), ("b", 24, "short", 200),
                                     ("c", 168, "short", 900), ("d", 24, "longform", 10), ("e", 40, "short", 99999)]:
        data = observation(content, age, fmt, {"views": views})
        assert client.post("/api/editorial/observations", json=data).json()["created"] is True
        assert client.post("/api/editorial/observations", json=data).json()["created"] is False
    data = observation("a", 24, "short", {"views": 500})
    assert client.post("/api/editorial/observations", json=data).status_code == 409
    cohorts = client.get("/api/editorial/insights").json()["cohorts"]
    assert len(cohorts) == 3
    shorts = next(c for c in cohorts if c["format"] == "short" and c["age_hours"] == 24)
    assert shorts["n"] == 2 and shorts["medians"]["views"] == 150


@pytest.mark.parametrize("patch", [{"metrics": {"views": -1}}, {"metrics": {"views": True}}, {"metrics": {}},
                                 {"published_at": "2026-01-01T00:00:00"}, {"observed_at": "2099-01-01T00:00:00Z"},
                                 {"metrics": {"clicks": 2, "impressions": 1}}])
def test_bad_measurements_are_rejected(client, patch):
    assert client.post("/api/editorial/observations", json={**observation(), **patch}).status_code == 422


def test_editorial_auth_and_missing_research_approval(client):
    assert TestClient(app).get("/api/editorial/insights").status_code == 401
    row = client.post("/api/editorial/opportunities", json={**opportunity_data(), "evidence": []}).json()
    assert client.post(f"/api/editorial/opportunities/{row['id']}/approve").status_code == 409
