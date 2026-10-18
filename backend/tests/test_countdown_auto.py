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
            for k in (countdown_topics.KEY, countdown_topics.LEDGER, countdown.AUTO_KEY, countdown.LINE_KEY):
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
         patch("backend.app.vidiq.rising", return_value=[]), \
         patch("backend.app.vidiq.safe", return_value=None), \
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



def _two_topics():
    big = {"overall": 70, "volume": 80, "competition": 40, "monthly_searches": 120000, "growth_pct": 10, "related": []}
    small = {"overall": 70, "volume": 30, "competition": 10, "monthly_searches": 3000, "growth_pct": 0, "related": []}
    ai = [{"topic": "Top 10 Big Search Movies", "keyword": "big", "group": "evergreen", "debate": 5, "titles": _titles(12)},
          {"topic": "Top 10 Tiny Search Movies", "keyword": "tiny", "group": "month", "debate": 9, "titles": _titles(12)}]
    with patch("backend.app.vidiq.keyword_metrics", side_effect=lambda kw, **k: big if kw == "big" else small):
        resolve = lambda e: {"tmdb_id": 1000 + int(e["title"].split()[-1]), "media_type": "movie", "title": e["title"]}
        with patch("backend.app.studio.gemini.ask_json", return_value=ai), \
             patch("backend.app.studio.compilations.engine._resolve_title", side_effect=resolve), \
             patch("backend.app.studio.compilations.engine._gather_market_context",
                   return_value={"upcoming": [], "trending": [], "tv_airing": [], "today": "2026-10-13"}), \
             patch("backend.app.vidiq.configured", return_value=True), \
             patch("backend.app.vidiq.breakout_videos", return_value=[]), \
             patch("backend.app.vidiq.trending", return_value=[]), \
             patch("backend.app.vidiq.rising", return_value=[]), \
             patch("backend.app.vidiq.safe", return_value=None):
            countdown_topics.build()


def test_demand_is_judged_by_searches_and_low_demand_is_never_auto_picked():
    _two_topics()
    t = {x["topic"]: x for x in countdown_topics.listing()["topics"]}
    assert t["Top 10 Big Search Movies"]["checks"]["demand"]["status"] == "pass"
    assert t["Top 10 Tiny Search Movies"]["checks"]["demand"]["status"] == "fail"      # 3K/month, even though "overall" is 70
    assert t["Top 10 Big Search Movies"]["score"] > t["Top 10 Tiny Search Movies"]["score"]
    assert countdown.next_pick()["topic"] == "Top 10 Big Search Movies"


def test_line_goes_first_and_can_be_managed(client):
    _two_topics()
    tiny = next(x for x in countdown_topics.listing()["topics"] if "Tiny" in x["topic"])
    st = client.post("/api/countdowns/line", json={"topic": "", "fmt": "top10", "sig": tiny["sig"]}).json()
    assert [i["topic"] for i in st["line"]] == ["Top 10 Tiny Search Movies"] and st["next"]["from_line"]
    assert all("Tiny" not in x["topic"] for x in client.get("/api/countdowns/topics").json()["topics"])   # moved into the line
    st = client.post("/api/countdowns/line", json={"topic": "Top 5 My Own Idea", "fmt": "top5"}).json()
    second = st["line"][1]["id"]
    st = client.post(f"/api/countdowns/line/{second}/up").json()
    assert st["line"][0]["topic"] == "Top 5 My Own Idea"
    with patch.object(countdown, "start_research") as sr, patch("backend.app.costs.today_spend", return_value=0.0):
        started = countdown.auto_tick()
    assert started["from_line"] and started["topic"] == "Top 5 My Own Idea" and sr.called
    assert [i["topic"] for i in countdown.line()] == ["Top 10 Tiny Search Movies"]
    st = client.delete(f"/api/countdowns/line/{countdown.line()[0]['id']}").json()
    assert st["line"] == [] and any("Tiny" in x["topic"] for x in client.get("/api/countdowns/topics").json()["topics"])


def test_research_gets_vidiq_search_and_competitor_data():
    mk = {"keyword": "horror movies", "demand": {"monthly_searches": 44631, "competition": 44, "growth_pct": 4},
          "related": [{"keyword": "best horror movies", "monthly_searches": 134758, "overall": 61}],
          "questions": [{"question": "what is the scariest movie", "monthly_searches": 9000}],
          "competitors": [{"title": "Top 10 Horror Movies of 2026", "views": 2500000}]}
    txt = countdown.market_text(mk)
    assert "44,631 searches/month" in txt and "best horror movies (134,758/mo)" in txt
    assert "what is the scariest movie" in txt and "Top 10 Horror Movies of 2026" in txt
    with SessionLocal() as s:
        c = CountdownProject(topic="Top 10 Horror Movies", fmt="top10")
        s.add(c)
        s.commit()
        cid = c.id
    seen = {}
    def fake_gen(**kw):
        seen.update(kw)
        return {"entries": [{"rank": 1, "title": "Alien", "tmdb_id": 348, "media_type": "movie"}]}
    with patch.object(countdown, "market_data", return_value=mk), \
         patch("backend.app.studio.compilations.engine.generate_countdown", side_effect=fake_gen):
        countdown.research_job(cid)
    assert "44,631" in seen["market"]
    with SessionLocal() as s:
        assert s.get(CountdownProject, cid).ranking["market"]["keyword"] == "horror movies"


def test_topics_use_the_phrase_pool_without_extra_vidiq_calls():
    calls = []
    def km(kw, **k):
        calls.append(kw)
        return {"keyword": kw, "monthly_searches": 500000, "growth_pct": 5, "competition": 50, "overall": 70, "volume": 90,
                "related": [{"keyword": "best horror movies", "monthly_searches": 134758, "overall": 61}]}
    ai = [{"topic": "Top 10 Best Horror Movies", "keyword": "best horror movies", "group": "evergreen", "debate": 7, "titles": _titles(12)}]
    resolve = lambda e: {"tmdb_id": 1000 + int(e["title"].split()[-1]), "media_type": "movie", "title": e["title"]}
    with patch("backend.app.vidiq.keyword_metrics", side_effect=km), \
         patch("backend.app.studio.gemini.ask_json", return_value=ai) as ask, \
         patch("backend.app.studio.compilations.engine._resolve_title", side_effect=resolve), \
         patch("backend.app.studio.compilations.engine._gather_market_context",
               return_value={"upcoming": [], "trending": [], "tv_airing": [], "today": "2026-10-13"}), \
         patch("backend.app.vidiq.configured", return_value=True), \
         patch("backend.app.vidiq.breakout_videos", return_value=[]), \
         patch("backend.app.vidiq.trending", return_value=[]), \
         patch("backend.app.vidiq.rising", return_value=[]), \
         patch("backend.app.vidiq.safe", return_value=None):
        countdown_topics.build()
    assert calls == countdown_topics.SEEDS                               # only the weekly seed lookups
    assert "best horror movies — 134,758/month" in ask.call_args.args[0]  # the AI must pick from real searches
    t = countdown_topics.listing()["topics"][0]
    assert t["demand"]["monthly_searches"] == 134758 and t["checks"]["demand"]["status"] == "pass"
