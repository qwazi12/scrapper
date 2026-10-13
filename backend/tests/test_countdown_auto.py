"""Countdown topics + automation (2026-10-13)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.models import AppSetting, CountdownProject
from backend.app.studio import countdown, countdown_topics


@pytest.fixture(autouse=True)
def _fresh():
    def clear():
        with SessionLocal() as s:
            s.query(CountdownProject).delete()
            for k in (countdown_topics.KEY, countdown_topics.LEDGER, countdown.AUTO_KEY):
                row = s.get(AppSetting, k)
                if row:
                    s.delete(row)
            s.commit()
    clear()
    yield
    clear()          # no leftover topics/countdowns for the background scheduler to start


@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


def _titles(n, base=100):
    return [{"title": f"Film {i}", "year": 2000 + i} for i in range(n)]


def _build(ai_topics, demand=None):
    resolve = lambda e: ({"tmdb_id": 1000 + int(e["title"].split()[-1]), "media_type": "movie", "title": e["title"]}
                         if e["title"].startswith("Film") else None)
    with patch("backend.app.studio.gemini.ask_json", return_value=ai_topics), \
         patch("backend.app.studio.compilations.engine._resolve_title", side_effect=resolve), \
         patch("backend.app.studio.compilations.engine._gather_market_context",
               return_value={"upcoming": [], "trending": [], "tv_airing": [], "today": "2026-10-13"}), \
         patch("backend.app.vidiq.configured", return_value=True), \
         patch("backend.app.vidiq.breakout_videos", return_value=[]), \
         patch("backend.app.vidiq.trending", return_value=[]), \
         patch("backend.app.vidiq.keyword_metrics", return_value=demand):
        return countdown_topics.build()


def test_build_checks_sizes_demand_and_skips_made():
    countdown_topics.mark(countdown_topics.sig("Top 10 Already Made Movies"), "made")
    ai = [
        {"topic": "Top 10 Horror Movies on Netflix", "keyword": "netflix horror", "group": "month", "debate": 8,
         "best_format": "top10", "titles": _titles(12) + [{"title": "A Theory"}]},
        {"topic": "Top 5 Tiny List", "keyword": "tiny", "group": "evergreen", "debate": 4, "titles": _titles(3)},
        {"topic": "Top 10 Already Made Movies", "keyword": "x", "group": "trend", "debate": 9, "titles": _titles(15)},
    ]
    demand = {"overall": 64.0, "volume": 69.0, "competition": 44.0, "monthly_searches": 44631, "growth_pct": 42.8,
              "related": [{"keyword": "best horror movies", "overall": 61, "monthly_searches": 1}]}
    assert _build(ai, demand) == {"topics": 1}                      # tiny can't fill a Top 5; made one is skipped
    t = countdown_topics.listing()["topics"][0]
    assert t["sizes"] == [5, 10] and t["checks"]["demand"]["status"] == "pass"
    assert "44,631 searches/month" in t["checks"]["demand"]["why"] and t["keywords"] == ["best horror movies"]


def test_automation_starts_one_a_day_and_stops_before_render(client):
    _build([{"topic": "Top 10 Sci-Fi Movies", "keyword": "sci-fi", "group": "evergreen", "debate": 7,
             "best_format": "top10", "titles": _titles(15)}],
           {"overall": 70, "volume": 70, "competition": 30, "monthly_searches": 9000, "growth_pct": 5, "related": []})
    with patch.object(countdown, "start_research") as sr, patch("backend.app.costs.today_spend", return_value=0.0):
        started = countdown.auto_tick()
        assert started and started["fmt"] == "top10" and sr.called
        assert countdown.auto_tick() is None                         # 1 a day
        assert "today's limit" in countdown.auto_state["last_result"]
    with SessionLocal() as s:
        c = s.query(CountdownProject).one()
        assert c.review["auto"] is True and "Film 0" in (c.notes or "")
        c.ranking = {"entries": [{"rank": 1, "title": "Film 0", "tmdb_id": 1000, "media_type": "movie"},
                                 {"rank": 2, "title": "Not real"}]}
        c.stage, c.stage_status = "research", "done"
        s.commit()
        cid = c.id
    countdown.auto_titles(cid)                                       # fills from the topic's checked titles
    c = client.get(f"/api/countdowns/{cid}").json()
    assert c["stage"] == "segments" and c["entry_count"] == 10 and c["unmatched"] == 0


def test_auto_titles_stops_for_owner_when_too_few(client):
    with SessionLocal() as s:
        c = CountdownProject(topic="t", fmt="top10", review={"auto": True},
                             ranking={"entries": [{"rank": 1, "title": "A", "tmdb_id": 1, "media_type": "movie"}]},
                             stage="research", stage_status="done")
        s.add(c)
        s.commit()
        cid = c.id
    countdown.auto_titles(cid)
    c = client.get(f"/api/countdowns/{cid}").json()
    assert c["stage"] == "research" and "Only 1 real titles" in c["stage_message"]


def test_start_topic_refuses_a_size_it_cant_fill(client):
    _build([{"topic": "Top 10 Westerns", "keyword": "westerns", "group": "evergreen", "debate": 6, "titles": _titles(7)}])
    t = client.get("/api/countdowns/topics").json()["topics"][0]
    r = client.post("/api/countdowns/topics/start", json={"sig": t["sig"], "fmt": "top10"})
    assert r.status_code == 400 and "7 real titles" in r.json()["detail"]
    with patch.object(countdown, "start_research"):
        r = client.post("/api/countdowns/topics/start", json={"sig": t["sig"], "fmt": "top5"})
    assert r.status_code == 200 and r.json()["fmt"] == "top5"
    assert client.get("/api/countdowns/topics").json()["topics"] == []      # made → never shown again


def test_auto_status_and_controls(client):
    st = client.get("/api/countdowns/auto").json()
    assert st["settings"]["per_day"] == 1 and st["next"] is None
    st = client.put("/api/countdowns/auto", json={"enabled": False}).json()
    assert st["settings"]["enabled"] is False
    st = client.post("/api/countdowns/auto/pause-today", json={"on": True}).json()
    assert st["settings"]["paused_until"]
