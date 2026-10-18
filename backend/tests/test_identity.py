import pytest
from backend.app.studio import identity, tmdb
from backend.app.studio.compilations import engine


RESULTS = [{"tmdb_id": 1, "title": "Same Title", "date": "1951-01-01", "media_type": "movie", "popularity": 1000},
           {"tmdb_id": 2, "title": "Same Title", "date": "2026-01-01", "media_type": "movie", "popularity": 1}]


def test_year_and_ambiguity_are_not_popularity_fallbacks():
    assert identity.select("Same Title", RESULTS) is None
    assert identity.select("Same Title", RESULTS, year=2026)["tmdb_id"] == 2
    assert identity.select("Same Title", RESULTS, year=2025) is None
    assert identity.select("Unrelated", RESULTS, year=2026) is None
    assert identity.select("Same Title", RESULTS, year=2026, medium="tv") is None
    assert identity.select("Same Title", RESULTS, year=2026, tmdb_id=1) is None


@pytest.mark.parametrize("year", [True, "unknown", "", 20])
def test_malformed_year_fails_closed(year):
    assert identity.select("Same Title", RESULTS, year=year) is None


def test_original_titles_and_declared_aliases_match():
    results = [{"tmdb_id": 1, "title": "Localized Title", "original_title": "Original Title",
                "alternate_titles": ["Alternate Title"], "date": "2026-01-01", "media_type": "movie"}]
    assert identity.select("Original Title", results, year=2026)
    assert identity.select("Alternate Title", results, year=2026)


def test_supplied_id_is_still_checked_and_wrong_year_rejected(monkeypatch):
    monkeypatch.setattr(tmdb, "configured", lambda: True)
    monkeypatch.setattr(tmdb, "search", lambda q: RESULTS[:1])
    assert engine._resolve_title({"title": "Same Title", "year": 2026}) is None
    assert engine._resolve_title({"title": "Unrelated invented title", "tmdb_id": 1, "year": 1951}) is None


def test_owner_edited_countdown_identity_is_rechecked_at_confirmation(session, monkeypatch):
    from fastapi import HTTPException
    from backend.app.models import CountdownProject
    from backend.app.studio import countdown
    entries = [{"rank": i, "title": f"Movie {i}", "year": 2026, "tmdb_id": i, "media_type": "movie"} for i in range(1, 6)]
    c = CountdownProject(topic="Synthetic", fmt="top5", ranking={"contract_version": 1, "entries": entries})
    session.add(c); session.commit()
    checked = []
    def resolve(entry):
        checked.append(entry["tmdb_id"])
        return entry if entry["tmdb_id"] != 3 else None
    monkeypatch.setattr(engine, "_resolve_title", resolve)
    with pytest.raises(HTTPException, match="Canonical"):
        countdown.confirm_titles(session, c)
    assert checked == [1, 2, 3] and not c.segment_ids
