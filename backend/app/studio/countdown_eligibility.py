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


def category_terms(text):
    """Longest aliases first; each recognized word belongs to one filter axis."""
    terms=[]
    groups=[('language',{word+'-language':code for word,code in LANGUAGES.items()}),
        ('country',COUNTRIES),('genre',GENRES),('provider',PROVIDERS),
        ('medium',{'movie':'movie','movies':'movie','film':'movie','films':'movie',
                   'tv':'tv','show':'tv','shows':'tv','series':'tv'})]
    candidates=[]
    for axis,aliases in groups:
        for word,value in aliases.items():
            for match in re.finditer(r'(?<!\w)'+re.escape(word)+r'(?!\w)',text):
                candidates.append((match.start(),match.end(),axis,value))
    for term in sorted(candidates,key=lambda t:(t[0],-(t[1]-t[0]))):
        if not any(term[0]<other[1] and term[1]>other[0] for other in terms):terms.append(term)
    return sorted(terms)


def filter_modes(text,terms,unsupported):
    """Only local, unambiguous lists; no inferred Boolean precedence."""
    operators={axis:set() for axis in ('genre','country','provider','language','medium')}
    for alternative in re.finditer(r'\bor\b',text):
        left=[term for term in terms if term[1]<=alternative.start()]
        right=[term for term in terms if term[0]>=alternative.end()]
        if not left or not right or left[-1][2]!=right[0][2]:
            unsupported.append('cross-filter or unrecognized alternatives; rephrase one category per list')
    for left,right in zip(terms,terms[1:]):
        gap=text[left[1]:right[0]]
        ops=set(re.findall(r'\b(and|or)\b',gap))
        if left[2]==right[2]:operators[left[2]].update(ops)
    for axis,ops in operators.items():
        if len(ops)>1:unsupported.append(f'mixed and/or {axis} conditions; rephrase one unambiguous list')
    return {axis:'any' if ops=={'or'} else 'all' for axis,ops in operators.items()}


def criteria(topic: str, territory: str, today: dt.date | None = None) -> dict[str, Any]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    text = topic.casefold().replace("–", "-").replace("—", "-")
    unsupported=[];terms=category_terms(text);modes=filter_modes(text,terms,unsupported)
    required, excluded = set(), set()
    supported_negations=set()
    for start,end,axis,value in terms:
        negation=re.search(r'\b(?:not|no|without|excluding)\s+$',text[:start])
        if axis=='genre':
            (excluded if negation else required).add(value)
            if negation:supported_negations.add(negation.start())
    for negation in re.finditer(r'\b(?:not|no|without|excluding|except)\b',text):
        following=[term for term in terms if term[0]>=negation.end()]
        if negation.start() not in supported_negations or (len(following)>1 and following[0][2]==following[1][2]
                and re.search(r'\b(?:and|or)\b|,',text[following[0][1]:following[1][0]])):
            unsupported.append('compound or unsupported exclusion; rephrase an explicit single genre exclusion')
    movie = bool(re.search(r"\b(?:movies?|films?)\b", text))
    tv = bool(re.search(r"\b(?:tv|shows?|series)\b", text))
    medium = "movie" if movie and not tv else "tv" if tv and not movie else None
    languages = sorted({value for _,_,axis,value in terms if axis=='language'})
    countries = sorted({value for _,_,axis,value in terms if axis=='country'})
    if len(languages)>1 and modes['language']=='all':unsupported.append('multiple required original languages; use an explicit alternative')
    if movie and tv and modes['medium']!='any':unsupported.append('movie/TV conjunction; choose one medium or an explicit alternative')
    years = [int(y) for y in re.findall(r"\b((?:18|19|20|21)\d{2})\b", text)]
    decade = re.search(r"\b((?:18|19|20|21)\d0)s\b", text)
    lo = hi = None
    decades=re.findall(r'\b(?:18|19|20|21)\d0s\b',text)
    if len(decades)>1 or (decade and years):
        unsupported.append('compound release decades/years; use one explicit inclusive range')
    if decade:
        lo, hi = int(decade[1]), int(decade[1]) + 9
    elif years:
        lo, hi = years[0], years[-1]
        if len(years)>1:
            matches=list(re.finditer(r'\b(?:18|19|20|21)\d{2}\b',text))
            gap=text[matches[0].end():matches[-1].start()].strip()
            inclusive=(len(years)==2 and (gap in ('-','to','through') or
                (gap=='and' and re.search(r'\bbetween\s*$',text[:matches[0].start()]))))
            if not inclusive or lo>hi or re.search(r'\b(?:before|after)\b',text):
                unsupported.append('compound release-year conditions; use one explicit inclusive range')
        if len(years) == 1 and re.search(r"\bafter\s+" + str(lo), text):
            lo, hi = lo + 1, None
        elif len(years) == 1 and re.search(r"\bbefore\s+" + str(hi), text):
            lo, hi = None, hi - 1
        elif len(years)==1 and re.search(r'\bsince\s+'+str(lo),text):hi=None
        elif len(years)==1 and re.search(r'\buntil\s+'+str(hi),text):lo=None
        elif len(years)==1 and re.search(r'\b(?:before|after|since|until|earlier|later|older|newer)\b',text):
            unsupported.append('unrecognized release-year bound; use before/after/since/until followed by one year')
    if "this year" in text:
        if years or decade:unsupported.append('absolute and relative year conditions; use one explicit window')
        lo = hi = today.year
    if sum(phrase in text for phrase in ('this year','this month','this week'))>1:
        unsupported.append('multiple relative date windows; choose one explicit window')
    date_from = date_to = None
    if "this month" in text:
        date_from = today.replace(day=1)
        date_to = (date_from.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    if "this week" in text:
        date_from = today - dt.timedelta(days=today.weekday())
        date_to = date_from + dt.timedelta(days=6)
    if "upcoming" in text:
        date_from = max(date_from or today, today)
    unsupported.extend(label for pattern, label in [
        (r"\b(?:highest.grossing|box.office|adjusted for inflation)\b", "box-office ranking"),
        (r"\b(?:oscar|academy award|award.winning|award winners?)\b", "award eligibility"),
        (r"\b(?:rotten tomatoes|imdb.rating|100%)", "rating threshold"),
        (r"\b(?:exclusive|originals)\b", "provider exclusivity/original production"),
        (r"\b(?:anime|psychological|mind.bending|based on true|true.story)\b", "specialized category or real-event claim"),
    ] if re.search(pattern, text))
    if re.search(r"\b(?:new|latest|recent)\b", text) and not (years or date_from or date_to):
        unsupported.append("an explicit date window for new/recent releases")
    return {"version": 2, "territory": territory, "medium": medium,
        "genres": sorted(required), "genre_mode": modes['genre'],
        "excluded_genres": sorted(excluded), "production_countries": countries, "original_languages": languages,
        "country_mode":modes['country'],"provider_mode":modes['provider'],
        "year_min": lo, "year_max": hi, "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "providers": sorted({provider for word, provider in PROVIDERS.items() if contains(text, word)}),
        "requires_independent_review": sorted(set(unsupported)),
        "interpretation": "Country words refer to production country; -language refers to original language. Each filter uses its own explicit and/or list; mixed or cross-filter alternatives require rephrasing. Year ranges are inclusive. Ranking order is editorial unless independently evidenced."}


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
    expected_countries=set(rule['production_countries']);actual_countries=set(facts.get('production_countries') or [])
    if expected_countries and (not expected_countries.intersection(actual_countries) if rule.get('country_mode')=='any' else not expected_countries.issubset(actual_countries)):
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
        if (not set(rule['providers']).intersection(available) if rule.get('provider_mode')=='any' else not set(rule['providers']).issubset(available)):
            errors.append("current subscription availability in the selected territory is unconfirmed; rental does not qualify")
    errors.extend("independent evidence required for " + label for label in rule["requires_independent_review"])
    if len(str(entry.get("why_it_ranks") or "").strip()) < 20:
        errors.append("a substantive editorial ranking rationale is missing")
    return errors


def verify(entries: list[dict], topic: str, territory: str, *, checked_at: dt.datetime | None = None) -> dict:
    checked_at=checked_at or dt.datetime.now(dt.timezone.utc)
    if checked_at.tzinfo is None:raise ValueError('Category retrieval time needs an explicit timezone')
    checked_at=checked_at.astimezone(dt.timezone.utc)
    rule = criteria(topic, territory, today=checked_at.date())
    if rule['requires_independent_review']:
        raise ValueError('; '.join('independent evidence required for '+label for label in rule['requires_independent_review']))
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
    return {"criteria": rule, "records": checked, "checked_at": checked_at.isoformat(),
            "scope": "canonical category metadata and current territory availability; ranking preference remains editorial"}
