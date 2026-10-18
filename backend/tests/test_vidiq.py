"""vidIQ client: credit guard, cache, MCP parsing (no real calls)."""

import json

import httpx
import pytest

from backend.app import vidiq
from backend.app.config import settings
from backend.app.db import SessionLocal
from backend.app.models import AppSetting


class FakeResp:
    def __init__(self, payload, sse=False, status=200, sid=None):
        self.status_code = status
        self.headers = {"content-type": "text/event-stream" if sse else "application/json"}
        if sid:
            self.headers["mcp-session-id"] = sid
        self.text = ("event: message\ndata: " + json.dumps(payload)) if sse else json.dumps(payload)
        self._p = payload

    def json(self):
        return self._p


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setattr(settings, "vidiq_api_key", "test-key")
    with SessionLocal() as s:
        for k in ("vidiq_settings", "vidiq_ledger", "vidiq_cache"):
            row = s.get(AppSetting, k)
            if row:
                s.delete(row)
        s.commit()
    vidiq._session.update(id=None, at=0.0)
    vidiq._state.update(down_until=0.0, last_error=None, tools=None, tools_at=0.0)
    calls = []

    def post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        m = json["method"]
        assert headers["Authorization"] == "Bearer test-key"
        if m == "initialize":
            return FakeResp({"jsonrpc": "2.0", "id": json["id"], "result": {"protocolVersion": "2025-06-18"}}, sid="S1")
        if m == "notifications/initialized":
            return FakeResp({}, status=202)
        if m == "tools/list":
            return FakeResp({"jsonrpc": "2.0", "id": json["id"], "result": {"tools": [
                {"name": "vidiq_keyword_research"}, {"name": "vidiq_balance"}]}}, sse=True)
        if m == "tools/call":
            return FakeResp({"jsonrpc": "2.0", "id": json["id"], "result": {
                "content": [{"type": "text", "text": '{"volume": 9000}'}]}}, sse=True)
        raise AssertionError(m)

    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_call_parses_sse_charges_credits_and_caches(fake):
    assert vidiq.call("vidiq_keyword_research", {"q": "horror movies"}) == {"volume": 9000}
    assert vidiq.usage()["today"] == 5
    assert vidiq.call("vidiq_keyword_research", {"q": "horror movies"}) == {"volume": 9000}      # cached: no new call
    assert sum(1 for c in fake if c["method"] == "tools/call") == 1 and vidiq.usage()["today"] == 5


def test_free_tools_cost_nothing_and_daily_cap_blocks(fake):
    vidiq.call("vidiq_balance", {}, cache_hours=0)
    assert vidiq.usage()["today"] == 0
    with SessionLocal() as s:
        s.add(AppSetting(key="vidiq_settings", value={**vidiq.DEFAULTS, "daily_credits": 7}))
        s.commit()
    vidiq.call("vidiq_keyword_research", {"q": "a"}, cache_hours=0)
    with pytest.raises(vidiq.CreditCapReached):
        vidiq.call("vidiq_keyword_research", {"q": "b"}, cache_hours=0)
    assert vidiq.safe("vidiq_keyword_research", {"q": "c"}, cache_hours=0) is None


def test_tools_list_and_find_tool(fake):
    assert vidiq.find_tool("keyword") == "vidiq_keyword_research"
    assert vidiq.find_tool("nothing") is None


def test_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "vidiq_api_key", "")
    vidiq._state.update(down_until=0.0)
    with pytest.raises(vidiq.VidIQError):
        vidiq.call("vidiq_keyword_research", {})
    assert vidiq.status()["configured"] is False


def test_longform_tags_put_vidiq_searches_first_and_cap_at_15(monkeypatch):
    from backend.app.studio import routes
    monkeypatch.setattr(vidiq, "configured", lambda: True)
    monkeypatch.setattr(vidiq, "keyword_metrics", lambda kw, **k: {"related": [{"keyword": "wildwood trailer 2026"}, {"keyword": "wildwood movie"}]})
    tags = routes.longform_tags("Wildwood", [f"tag {i}" for i in range(20)] + ["Wildwood Movie"])
    parts = tags.split()
    assert parts[:2] == ["#wildwoodtrailer2026", "#wildwoodmovie"] and len(parts) == 15


def test_concurrent_credit_calls_reserve_before_dispatch_and_timeout_is_not_retried(fake, monkeypatch):
    import concurrent.futures
    import time
    with SessionLocal() as s:
        s.add(AppSetting(key='vidiq_settings',value={**vidiq.DEFAULTS,'daily_credits':7})); s.commit()
    dispatched=[]
    def provider(*a,**k):
        dispatched.append(1); time.sleep(.03)
        raise httpx.ReadTimeout('Synthetic receipt lost')
    monkeypatch.setattr(vidiq,'_call',provider)
    def invoke(i):
        try: vidiq.call('vidiq_keyword_research',{'q':str(i)},cache_hours=0)
        except (vidiq.CreditCapReached,httpx.HTTPError): return
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool: list(pool.map(invoke,range(5)))
    assert len(dispatched)==1 and vidiq.usage()['held']==5 and vidiq.usage()['today']==0
    assert vidiq.safe('vidiq_keyword_research',{'q':'later'},cache_hours=0) is None


def test_missing_credentials_never_reserve_an_impossible_paid_attempt(monkeypatch):
    from backend.app.models import CreditReservation
    monkeypatch.setattr(settings,'vidiq_api_key','')
    with pytest.raises(vidiq.VidIQError,match='not configured'):
        vidiq.call('vidiq_keyword_research',{'q':'synthetic'},cache_hours=0)
    with SessionLocal() as s:assert s.query(CreditReservation).count()==0
