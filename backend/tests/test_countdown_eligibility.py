"""Category, territory, identity and manual-entry checks use synthetic metadata."""
import datetime as dt

import pytest
from fastapi import HTTPException

from backend.app.db import SessionLocal
from backend.app.models import CountdownProject, StudioProject
from backend.app.studio import countdown, countdown_eligibility as eligibility

ENTRY = {"rank": 1, "title": "Synthetic Film", "tmdb_id": 903301, "media_type": "movie", "year": 2026,
         "why_it_ranks": "An editorial preference for the visual structure of this synthetic example."}
FACTS = {"title": "Synthetic Film", "tmdb_id": 903301, "media_type": "movie", "primary_date": "2026-05-18",
         "genres": ["Thriller"], "production_countries": ["KR"], "original_language": "ko", "source": "synthetic://canonical-fixture"}


@pytest.mark.parametrize("topic,change,expected", [
    ("Top 5 Korean Thriller Movies 2026", {"genres": ["Comedy"]}, "genre"),
    ("Top 5 Korean Thriller Movies 2026", {"production_countries": ["US"]}, "country"),
    ("Top 5 Korean-language Thriller Movies", {"original_language": "ja"}, "language"),
    ("Top 5 Korean Thriller Movies 2025", {}, "period"),
    ("Top 5 Thriller TV Shows", {}, "category"),
    ("Top 5 Thriller Movies", {"title": "Different Film"}, "canonical"),
    ("Top 5 Thriller Movies", {"tmdb_id": 999}, "canonical"),
    ("Top 5 Thriller Movies", {"primary_date": "2025-05-18"}, "canonical"),
    ("Top 5 Movies without Comedy", {"genres": ["Comedy"]}, "excluded"),
    ("Top 5 Award-Winning Movies", {}, "independent evidence"),
    ("Top 5 Highest-Grossing Movies", {}, "independent evidence"),
    ("Top 5 Netflix Originals", {}, "independent evidence"),
    ("Top 5 New Movies", {}, "explicit date window"),
])
def test_identity_is_not_category_evidence(topic, change, expected):
    errors = eligibility.evaluate(ENTRY, {**FACTS, **change}, eligibility.criteria(topic, "US"))
    assert any(expected in error for error in errors), errors


def test_supported_country_language_genre_and_date_constraints():
    rule = eligibility.criteria("Top 5 Korean Thriller Movies 2026", "US")
    assert eligibility.evaluate(ENTRY, FACTS, rule) == []
    rule = eligibility.criteria("Top 5 Korean-language Thriller or Horror Movies after 2020", "US")
    assert rule["production_countries"] == [] and rule["original_languages"] == ["ko"]
    assert eligibility.evaluate(ENTRY, FACTS, rule) == []
    assert eligibility.criteria("Top 5 Films of the 1990s", "US")["year_max"] == 1999
    assert eligibility.criteria("Top 5 Movies before 2000", "US")["year_max"] == 1999
    assert eligibility.criteria("Top 5 Movies this year", "US", dt.date(2026, 1, 2))["year_min"] == 2026


def test_region_specific_subscription_cannot_be_replaced_by_rental_or_another_country(monkeypatch):
    calls = []
    monkeypatch.setattr(eligibility.tmdb, "fact_sheet", lambda *a, **k: FACTS)
    def providers(path):
        calls.append(path)
        return {"results": {"US": {"rent": [{"provider_name": "Netflix"}]},
                             "GB": {"flatrate": [{"provider_name": "Netflix"}]}}}
    monkeypatch.setattr(eligibility.tmdb, "get", providers)
    with pytest.raises(ValueError, match="subscription availability"):
        eligibility.verify([ENTRY], "Top 5 Thriller Movies on Netflix", "US")
    result = eligibility.verify([ENTRY], "Top 5 Thriller Movies on Netflix", "GB")
    assert result["criteria"]["territory"] == "GB" and result["records"][0]["sha256"]
    assert calls == ["/movie/903301/watch/providers"] * 2


def test_territory_release_window_is_not_the_worldwide_primary_date():
    rule = eligibility.criteria("Upcoming Thriller Movies this month", "US", dt.date(2026, 5, 12))
    assert eligibility.evaluate(ENTRY, FACTS, rule) == ["a release in the requested territory/date window is unconfirmed"]
    gb = {**FACTS, "releases": [{"country": "GB", "date": "2026-05-20"}]}
    assert eligibility.evaluate(ENTRY, gb, rule)
    us = {**FACTS, "releases": [{"country": "US", "date": "2026-05-20"}]}
    assert eligibility.evaluate(ENTRY, us, rule) == []
    rule = eligibility.criteria("Thriller Movies this week", "US", dt.date(2026, 5, 12))
    assert rule["date_from"] == "2026-05-11" and rule["date_to"] == "2026-05-17"
    assert eligibility.evaluate(ENTRY, us, rule)


def test_manual_entry_cannot_skip_category_or_rationale_and_no_partial_segments_are_created(monkeypatch):
    from backend.app.studio.compilations import engine
    monkeypatch.setattr(engine, "_resolve_title", lambda e: e)
    def facts(kind, ident, **kwargs):
        return {**FACTS, "tmdb_id": ident, "title": f"Synthetic Film {ident}",
                "genres": ["Comedy"] if ident == 5 else ["Thriller"]}
    monkeypatch.setattr(eligibility.tmdb, "fact_sheet", facts)
    entries = [{**ENTRY, "rank": rank, "tmdb_id": i, "title": f"Synthetic Film {i}"} for rank, i in enumerate(range(1, 6), 1)]
    with SessionLocal() as s:
        p = CountdownProject(topic="Top 5 Korean Thriller Movies 2026", fmt="top5", ranking={"contract_version": 1, "entries": entries})
        s.add(p); s.commit()
        before = s.query(StudioProject).count()
        with pytest.raises(HTTPException, match="genre"):
            countdown.confirm_titles(s, p)
        assert p.segment_ids is None and s.query(StudioProject).count() == before
        monkeypatch.setattr(eligibility.tmdb, "fact_sheet", lambda kind, ident, **kwargs:
            {**FACTS, "tmdb_id": ident, "title": f"Synthetic Film {ident}"})
        entries[-1]["why_it_ranks"] = ""
        p.ranking = {"contract_version": 1, "entries": entries}
        with pytest.raises(HTTPException, match="rationale"):
            countdown.confirm_titles(s, p)
        assert s.query(StudioProject).count() == before
        entries[-1]["why_it_ranks"] = ENTRY["why_it_ranks"]
        p.ranking = {"contract_version": 1, "entries": entries}
        countdown.confirm_titles(s, p); s.commit()
        assert len(p.segment_ids) == 5 and len(p.ranking["eligibility"]["records"]) == 5
        s.expire(p)
        assert {e["segment_id"] for e in p.ranking["entries"]} == set(p.segment_ids)


@pytest.fixture
def reviewable(monkeypatch):
    monkeypatch.setattr(eligibility.tmdb, "fact_sheet", lambda kind, ident, **kwargs:
        {**FACTS, "tmdb_id": ident, "title": f"Synthetic Film {ident}"})
    entries = [{**ENTRY, "rank": rank, "tmdb_id": i, "title": f"Synthetic Film {i}"} for rank, i in enumerate(range(6, 11), 1)]
    with SessionLocal() as s:
        p = CountdownProject(topic="Top 5 Korean Thriller Movies 2026", fmt="top5", ranking={"contract_version": 1, "entries": entries})
        s.add(p); s.commit(); countdown.confirm_titles(s, p); s.commit()
        for pid in p.segment_ids:
            segment = s.get(StudioProject, pid)
            segment.facts = {**segment.facts, **FACTS, "tmdb_id": segment.tmdb_id, "title": segment.title}
            segment.script = {"sentences": [{"text": "Original checked synthetic words.", "check": "ok"}]}
        p.stage, p.stage_status = "script", "done"; s.commit()
        yield p.id


def test_exact_bookends_need_explicit_review_and_words_or_research_changes_revoke_it(reviewable):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    client = TestClient(app, headers={"x-access-token": "test-token"})
    base = f"/api/countdowns/{reviewable}"
    current = client.get(base).json()
    with SessionLocal() as s:
        with pytest.raises(HTTPException, match="Review the exact"):
            countdown.require_bookends(s, s.get(CountdownProject, reviewable))
    args = {"reviewed": True, "expected_version": current["bookend_version"]}
    assert client.post(base + "/review-bookends", json={**args, "reviewed": "yes"}).status_code == 422
    assert client.post(base + "/review-bookends", json={**args, "reviewed": False}).status_code == 409
    accepted = client.post(base + "/review-bookends", json=args)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["bookend_reviewed"]
    with SessionLocal() as s:
        p = s.get(CountdownProject, reviewable)
        countdown.require_bookends(s, p)
        p.script = {**p.script, "intro": "A newer saved introduction."}; s.commit()
        with pytest.raises(HTTPException, match="Review the exact"):
            countdown.require_bookends(s, p)
    assert client.post(base + "/review-bookends", json=args).status_code == 409
    current = client.get(base).json()
    assert client.post(base + "/review-bookends", json={**args, "expected_version": current["bookend_version"]}).status_code == 200
    with SessionLocal() as s:
        p = s.get(CountdownProject, reviewable)
        segment = s.get(StudioProject, p.segment_ids[0])
        segment.facts = {**segment.facts, "genres": ["Comedy"]}; s.commit()
        with pytest.raises(HTTPException, match="eligibility"):
            countdown.require_bookends(s, p)
    assert not client.get(base).json()["bookend_reviewed"]


def test_expired_category_proof_and_empty_bookends_cannot_be_approved(reviewable):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    client = TestClient(app, headers={"x-access-token": "test-token"})
    base = f"/api/countdowns/{reviewable}"
    with SessionLocal() as s:
        p = s.get(CountdownProject, reviewable)
        p.ranking = {**p.ranking, "eligibility": {**p.ranking["eligibility"],
            "checked_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)).isoformat()}}
        s.commit()
    current = client.get(base).json()
    response = client.post(base + "/review-bookends", json={"reviewed": True, "expected_version": current["bookend_version"]})
    assert response.status_code == 409 and "expired" in response.json()["detail"]
    with SessionLocal() as s:
        p = s.get(CountdownProject, reviewable)
        p.script = {**p.script, "intro": ""}; s.commit()
    current = client.get(base).json()
    response = client.post(base + "/review-bookends", json={"reviewed": True, "expected_version": current["bookend_version"]})
    assert response.status_code == 409 and "explicit intro" in response.json()["detail"]


def test_stale_ranking_save_preserves_newer_entries(reviewable):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    client = TestClient(app, headers={"x-access-token": "test-token"})
    base = f"/api/countdowns/{reviewable}"
    old = client.get(base).json()
    entries = [dict(e) for e in old["ranking"]["entries"]]
    entries[0]["why_it_ranks"] = "A newer owner rationale preserved against concurrent stale writes."
    result = client.put(base + "/entries", json={"entries": entries, "expected_ranking_version": old["ranking_version"]})
    assert result.status_code == 200, result.text
    response = client.put(base + "/entries", json={"entries": old["ranking"]["entries"], "expected_ranking_version": old["ranking_version"]})
    assert response.status_code == 409
    assert client.get(base).json()["ranking"]["entries"][0]["why_it_ranks"] == entries[0]["why_it_ranks"]


def test_description_uses_bound_provider_bytes_not_a_forged_draft_availability_label():
    from backend.app.publication_journal import digest
    from backend.app.studio.compilations.engine import countdown_description
    entry = {**ENTRY, "where_to_watch": "Invented Provider With Fabricated Availability",
        "availability_scope": {"status": "available", "provider": "TMDB/JustWatch", "territory": "US",
                               "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat()}}
    assert "Invented Provider" not in countdown_description({}, [entry], [])
    providers = {"flatrate": [{"provider_name": "Synthetic Subscription"}]}
    proof = {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(), "criteria": {"territory": "GB"},
        "records": [{"media_type": "movie", "tmdb_id": ENTRY["tmdb_id"], "facts": FACTS, "providers": providers,
                     "sha256": digest([FACTS, providers])}]}
    rendered = countdown_description({"eligibility": proof}, [entry], [])
    assert "Invented Provider" not in rendered and "GB, checked" in rendered and "Synthetic Subscription" in rendered
    proof["checked_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat()
    assert "Synthetic Subscription" not in countdown_description({"eligibility": proof}, [entry], [])
