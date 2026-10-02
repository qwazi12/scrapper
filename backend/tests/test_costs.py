"""Cost tracker: usage -> estimated $, free tiers, budget hard stop."""

import pytest
from fastapi.testclient import TestClient

from backend.app import costs
from backend.app.db import SessionLocal
from backend.app.models import AppSetting, UsageEvent


@pytest.fixture(autouse=True)
def clean():
    with SessionLocal() as s:
        s.query(UsageEvent).delete()
        row = s.get(AppSetting, "costs")
        if row:
            s.delete(row)
        s.commit()
    yield


@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


def test_gemini_cost_counts_thinking_as_output():
    with costs.operation("studio:script", ref="studio:1"):
        c = costs.record_gemini("gemini-3.8-flash", {"promptTokenCount": 1_000_000,
                                                     "candidatesTokenCount": 100_000, "thoughtsTokenCount": 100_000})
    assert c == pytest.approx(0.75 + 0.2 * 3.75)  # 2026 prices
    assert costs.ref_cost("studio:1") == pytest.approx(c)
    assert costs.summary()["by_operation"][0]["operation"] == "studio:script"


def test_tts_free_tier_then_billed():
    assert costs.record_tts(999_000, "v") == 0
    assert costs.record_tts(3_000, "v") == pytest.approx(2_000 / 1e6 * 30)


def test_grounded_search_free_then_charged(monkeypatch):
    monkeypatch.setitem(costs.DEFAULTS, "grounding_free_per_month", 1)
    first = costs.record_gemini("gemini-3.8-flash", {}, grounded=True)
    second = costs.record_gemini("gemini-3.8-flash", {}, grounded=True)
    assert first == 0 and second == pytest.approx(0.014)


def test_budget_hard_stop_blocks_paid_calls(client):
    client.put("/api/costs/settings", json={"budget_usd": 10, "hard_stop": True, "upload_post_plan": "Free"})
    costs.record_gemini("gemini-3.8-flash", {"promptTokenCount": 14_000_000})  # $10.50
    with pytest.raises(costs.BudgetExceeded, match="Monthly budget reached"):
        costs.check_budget()
    client.put("/api/costs/settings", json={"hard_stop": False})
    costs.check_budget()  # no exception


def test_summary_includes_fixed_costs_and_plan_limits(client):
    d = client.put("/api/costs/settings", json={"upload_post_plan": "Free", "budget_usd": 20,
                                                 "fixed_costs": [{"name": "Railway", "usd": 5}]}).json()
    costs.record_free("upload-post", requests=3)
    d = client.get("/api/costs").json()
    assert d["fixed_usd"] == 5 and d["free_tiers"]["uploads"] == {"used": 3, "plan": "Free", "limit": 10, "plan_usd": 0}
    assert d["budget_used_pct"] == 25.0


def test_bad_cost_settings_rejected(client):
    assert client.put("/api/costs/settings", json={"upload_post_plan": "Gold"}).status_code == 400
    assert client.put("/api/costs/settings", json={"budget_usd": -1}).status_code == 400
    assert client.put("/api/costs/settings", json={"fixed_costs": [{"name": "", "usd": 1}]}).status_code == 400


def test_cost_settings_change_is_undoable(client):
    client.put("/api/costs/settings", json={"budget_usd": 50})
    client.post("/api/undo", json={"scope": "settings"})
    assert client.get("/api/costs").json()["budget_usd"] == 0
