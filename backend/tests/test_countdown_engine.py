"""Unit tests for Film/TV Countdown Compilations Research Engine."""

import pytest
from backend.app.studio.compilations import engine
from backend.app.studio import gemini


def test_demand_triggers_structure():
    triggers = engine.DEMAND_TRIGGERS
    assert len(triggers) == 5
    ids = {t["id"] for t in triggers}
    assert ids == {"trending", "calendar", "streaming", "debate", "evergreen"}


def test_discover_topics_fallback_scoring():
    topics = engine._default_candidate_topics()
    assert len(topics) >= 5
    for t in topics:
        assert "topic" in t
        assert t["trigger"] in {"trending", "calendar", "streaming", "debate", "evergreen"}
        assert 1.0 <= t["total_score"] <= 10.0
        assert "format" in t
        assert t["format"] in {"top5", "top10", "top15"}


def test_discover_topics_with_gemini(monkeypatch):
    mock_candidates = [
        {
            "id": "mock-1",
            "topic": "Top 10 Mindfuck Movies",
            "trigger": "evergreen",
            "trigger_name": "Evergreen Classics",
            "format": "top10",
            "why_now": "Trending heavily on Letterboxd",
            "demand": 9.0,
            "momentum": 8.0,
            "debate": 9.0,
            "gap": 8.5,
            "visuals": 9.0,
            "total_score": 8.7,
            "suggested_entries": ["Memento", "Shutter Island"],
        }
    ]
    monkeypatch.setattr(gemini, "ask_json", lambda prompt, **kw: mock_candidates)
    res = engine.discover_topics()
    assert len(res) == 1
    assert res[0]["topic"] == "Top 10 Mindfuck Movies"
    assert res[0]["total_score"] == 8.7


def test_generate_countdown_fallback():
    data = engine._generate_fallback_countdown(
        topic="Sci-Fi Movies",
        format_type="top10",
        count=10,
    )
    assert data["topic"] == "Sci-Fi Movies"
    assert data["format"] == "top10"
    assert len(data["entries"]) == 10
    # Ranked #10 down to #1
    ranks = [e["rank"] for e in data["entries"]]
    assert ranks == list(range(10, 0, -1))
    assert len(data["title_options"]) == 3
    assert data["thumbnail_text"]
    assert data["hook_script"]
    assert data["closing_question"]
    assert len(data["honorable_mentions"]) >= 2


def test_saved_countdowns_ledger(session):
    mock_cd = {
        "topic": "Top 10 Christopher Nolan Films",
        "format": "top10",
        "entries": [{"rank": 1, "title": "Oppenheimer", "year": 2023}],
    }
    saved = engine.save_countdown_item(mock_cd)
    assert "id" in saved
    cid = saved["id"]

    all_saved = engine.list_saved_countdowns()
    assert any(x["id"] == cid for x in all_saved)

    # Delete
    deleted = engine.delete_saved_countdown(cid)
    assert deleted is True
    assert not any(x["id"] == cid for x in engine.list_saved_countdowns())
