"""Deterministic category constraints; canonical identity alone is insufficient.

These rules validate recognized words, never invent support for subjective
ranking, awards, gross revenue or factual trivia. Unknown qualifications require
an explicit editorial review rather than a fabricated metadata match.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from ..publication_journal import digest
from . import tmdb
from .identity import normalized

GENRES = {
    "action": "Action", "adventure": "Adventure", "animated": "Animation", "animation": "Animation",
    "comedy": "Comedy", "comedies": "Comedy", "crime": "Crime", "documentary": "Documentary",
    "documentaries": "Documentary", "drama": "Drama", "dramas": "Drama", "family": "Family",
    "fantasy": "Fantasy", "history": "History", "historical": "History", "horror": "Horror",
    "music": "Music", "musical": "Music", "mystery": "Mystery", "mysteries": "Mystery",
    "romance": "Romance", "romantic": "Romance", "sci-fi": "Science Fiction", "science fiction": "Science Fiction",
    "thriller": "Thriller", "thrillers": "Thriller", "war": "War", "western": "Western", "westerns": "Western",
}
COUNTRIES = {"south korean": "KR", "korean": "KR", "japanese": "JP", "french": "FR",
    "british": "GB", "american": "US", "indian": "IN", "nigerian": "NG", "ghanaian": "GH",
    "south korea": "KR", "japan": "JP", "france": "FR", "united kingdom": "GB",
    "united states": "US", "india": "IN", "nigeria": "NG", "ghana": "GH"}
LANGUAGES = {"korean": "ko", "japanese": "ja", "french": "fr", "english": "en", "hindi": "hi"}
PROVIDERS = {"netflix": "netflix", "disney+": "disneyplus", "disney plus": "disneyplus",
    "amazon prime video": "amazonprimevideo", "prime video": "amazonprimevideo",
    "apple tv+": "appletvplus", "hulu": "hulu", "paramount+": "paramountplus",
    "peacock": "peacock"}


def contains(text, phrase):
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text))


def criteria(topic: str, territory: str, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    text = topic.casefold().replace("–", "-").replace("—", "-")
    required, excluded = set(), set()
    for word, genre in GENRES.items():
        if not contains(text, word):
            continue
        if re.search(r"\b(?:not|no|without|excluding)\s+" + re.escape(word) + r"\b", text):
            excluded.add(genre)
        else:
            required.add(genre)
    movie = bool(re.search(r"\b(?:movies?|films?)\b", text))
    tv = bool(re.search(r"\b(?:tv|shows?|series)\b", text))
    medium = "movie" if movie and not tv else "tv" if tv and not movie else None
    languages = sorted({code for word, code in LANGUAGES.items() if contains(text, word + "-language")})
    countries = sorted({code for word, code in COUNTRIES.items()
        if contains(text, word) and not contains(text, word + "-language")})
    years = [int(y) for y in re.findall(r"\b((?:18|19|20|21)\d{2})\b", text)]
    decade = re.search(r"\b((?:18|19|20|21)\d0)s\b", text)
    lo = hi = None
    if decade:
        lo, hi = int(decade[1]), int(decade[1]) + 9
    elif years:
        lo, hi = min(years), max(years)
        if len(years) == 1 and re.search(r"\bafter\s+" + str(lo), text):
            lo, hi = lo + 1, None
        elif len(years) == 1 and re.search(r"\bbefore\s+" + str(hi), text):
            lo, hi = None, hi - 1
    if "this year" in text:
        lo = hi = today.year
    date_from = date_to = None
    if "this month" in text:
        date_from = today.replace(day=1)
        date_to = (date_from.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    if "this week" in text:
        date_from = today - dt.timedelta(days=today.weekday())
        date_to = date_from + dt.timedelta(days=6)
    if "upcoming" in text:
        date_from = max(date_from or today, today)
    unsupported = [label for pattern, label in [
        (r"\b(?:highest.grossing|box.office|adjusted for inflation)\b", "box-office ranking"),
        (r"\b(?:oscar|academy award|award.winning|award winners?)\b", "award eligibility"),
        (r"\b(?:rotten tomatoes|imdb.rating|100%)", "rating threshold"),
        (r"\b(?:exclusive|originals)\b", "provider exclusivity/original production"),
        (r"\b(?:anime|psychological|mind.bending|based on true|true.story)\b", "specialized category or real-event claim"),
    ] if re.search(pattern, text)]
    if re.search(r"\b(?:new|latest|recent)\b", text) and not (years or date_from or date_to):
        unsupported.append("an explicit date window for new/recent releases")
    return {"version": 1, "territory": territory, "medium": medium,
        "genres": sorted(required), "genre_mode": "any" if re.search(r"\bor\b", text) else "all",
        "excluded_genres": sorted(excluded), "production_countries": countries, "original_languages": languages,
        "year_min": lo, "year_max": hi, "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "providers": sorted({provider for word, provider in PROVIDERS.items() if contains(text, word)}),
        "requires_independent_review": unsupported,
        "interpretation": "Country words refer to production country; -language refers to original language. Ranking order is editorial unless independently evidenced."}


def provider_key(name: str) -> str:
    return normalized(name.replace("+", "plus"))


def evaluate(entry: dict, facts: dict, rule: dict, providers: dict | None = None) -> list[str]:
    errors = []
    if (facts.get("tmdb_id") != entry.get("tmdb_id") or facts.get("media_type") != entry.get("media_type", "movie")
            or normalized(facts.get("title", "")) != normalized(entry.get("title", ""))
            or str(facts.get("primary_date", ""))[:4] != str(entry.get("year", ""))):
        errors.append("canonical title/year/medium disagrees with the retrieved category record")
    if rule["medium"] and facts.get("media_type") != rule["medium"]:
        errors.append("wrong movie/TV category")
    genre_aliases = {"Action & Adventure": ["Action", "Adventure"],
                     "Sci-Fi & Fantasy": ["Science Fiction", "Fantasy"], "War & Politics": ["War", "Politics"]}
    genres = {name for genre in facts.get("genres") or [] for name in genre_aliases.get(genre, [genre])}
    expected = set(rule["genres"])
    if expected and (not expected.intersection(genres) if rule["genre_mode"] == "any" else not expected.issubset(genres)):
        errors.append("required genre is not supported by canonical metadata")
    if genres.intersection(rule["excluded_genres"]):
        errors.append("an excluded genre is present")
    if rule["production_countries"] and not set(rule["production_countries"]).intersection(facts.get("production_countries") or []):
        errors.append("production country is missing or does not match")
    if rule["original_languages"] and facts.get("original_language") not in rule["original_languages"]:
        errors.append("original language is missing or does not match")
    try:
        date = dt.date.fromisoformat(facts.get("primary_date", ""))
        if ((rule["year_min"] and date.year < rule["year_min"]) or (rule["year_max"] and date.year > rule["year_max"])):
            errors.append("release date is outside the requested period")
    except (ValueError, TypeError):
        errors.append("canonical release date is missing")
    if rule["date_from"] or rule["date_to"]:
        dates = []
        for release in facts.get("releases") or []:
            if release.get("country") != rule["territory"]:
                continue
            try:
                dates.append(dt.date.fromisoformat(release.get("date", "")).isoformat())
            except (ValueError, TypeError):
                continue
        if not any((not rule["date_from"] or date >= rule["date_from"]) and
                (not rule["date_to"] or date <= rule["date_to"]) for date in dates):
            errors.append("a release in the requested territory/date window is unconfirmed")
    if rule["providers"]:
        available = {provider_key(p.get("provider_name", "")) for p in (providers or {}).get("flatrate", [])}
        if not set(rule["providers"]).issubset(available):
            errors.append("current subscription availability in the selected territory is unconfirmed; rental does not qualify")
    errors.extend("independent evidence required for " + label for label in rule["requires_independent_review"])
    if len(str(entry.get("why_it_ranks") or "").strip()) < 20:
        errors.append("a substantive editorial ranking rationale is missing")
    return errors


def verify(entries: list[dict], topic: str, territory: str) -> dict:
    rule = criteria(topic, territory)
    checked, errors = [], []
    for e in entries:
        facts = tmdb.fact_sheet(e.get("media_type") or "movie", int(e["tmdb_id"]), region=territory)
        providers = None
        if rule["providers"]:
            response = tmdb.get(f"/{e.get('media_type') or 'movie'}/{int(e['tmdb_id'])}/watch/providers")
            providers = (response.get("results") or {}).get(territory) or {}
        issues = evaluate(e, facts, rule, providers)
        errors.extend(f"#{e.get('rank')} {e.get('title')}: {issue}" for issue in issues)
        checked.append({"tmdb_id": e["tmdb_id"], "media_type": e.get("media_type") or "movie",
            "facts": facts, "providers": providers, "sha256": digest([facts, providers]), "source": facts.get("source")})
    if errors:
        raise ValueError("; ".join(errors))
    return {"criteria": rule, "records": checked, "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scope": "canonical category metadata and current territory availability; ranking preference remains editorial"}
