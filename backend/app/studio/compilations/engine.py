"""Content Strategist and List Engine for Film/TV Countdown Compilations.

Implements the 5-Step Agentic Countdown Methodology:
  STEP 1 — Topic Discovery (15 candidates scored across 5 demand triggers)
  STEP 2 — Ranking Logic (defensible composite scoring 0-100: rating, impact, buzz, sentiment)
  STEP 3 — Entertainment Ordering (retention-scripted order, tease #1, controversial #3/#4, honorable mentions)
  STEP 4 — Fact Rules (verifiable box office, awards, dates, and JustWatch streaming availability)
  STEP 5 — Output (standardized JSON with YouTube titles, thumbnail text, retention hook, and closing question)
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any

from ...config import settings
from ...db import SessionLocal
from ...models import AppSetting, StudioProject
from .. import gemini, tmdb
from . import cinemeta

logger = logging.getLogger("scrapper.studio.compilations.engine")
UTC = datetime.timezone.utc

# 5 Demand Triggers
DEMAND_TRIGGERS = [
    {
        "id": "trending",
        "name": "Trending & Viral",
        "icon": "🔥",
        "desc": "Rising search queries, trailers, casting news & controversies in last 7 days",
    },
    {
        "id": "calendar",
        "name": "Upcoming & Anniversaries",
        "icon": "📅",
        "desc": "Releases in next 14 days, award shows, milestone anniversaries, seasonal holidays",
    },
    {
        "id": "streaming",
        "name": "JustWatch Streaming",
        "icon": "📺",
        "desc": "New arrivals & departing gems on Netflix, Max, Prime Video, Hulu, Disney+",
    },
    {
        "id": "debate",
        "name": "Debate & Controversy",
        "icon": "🥊",
        "desc": "Split opinions: overrated masterpieces, underrated gems, worst sequels, Oscar snubs",
    },
    {
        "id": "evergreen",
        "name": "Evergreen Classics",
        "icon": "🏛️",
        "desc": "Proven high-retention formats: highest grossing, IMDb Top 250, Letterboxd top, director bests",
    },
]


def _gather_market_context() -> dict[str, Any]:
    """Pull live industry context from TMDB calendar and Cinemeta trending."""
    cal_data: dict[str, Any] = {}
    try:
        if tmdb.configured():
            cal_data = tmdb.calendar() or {}
    except Exception as e:
        logger.warning("Could not fetch TMDB calendar: %s", e)

    trending_movies = []
    try:
        trending_movies = cinemeta.get_top_movies()[:8]
    except Exception as e:
        logger.warning("Could not fetch Cinemeta top movies: %s", e)

    upcoming = cal_data.get("upcoming_movies", [])[:8]
    on_air_tv = cal_data.get("on_the_air_tv", [])[:6]
    trending_all = cal_data.get("trending", [])[:8]

    return {
        "upcoming": [f"{m.get('title')} ({m.get('date', 'soon')})" for m in upcoming if m.get("title")],
        "trending": [m.get("title") for m in (trending_all or trending_movies) if m.get("title")][:8],
        "tv_airing": [t.get("title") for t in on_air_tv if t.get("title")][:6],
        "today": datetime.date.today().isoformat(),
    }


def discover_topics(category_filter: str | None = None) -> list[dict[str, Any]]:
    """STEP 1 — TOPIC DISCOVERY

    Generates 15 candidate topics pulled from the 5 demand triggers,
    scored 1-10 on Demand, Momentum, Debate, Gap, and Visuals.
    """
    ctx = _gather_market_context()
    today_str = ctx["today"]

    prompt = f"""You are the senior content strategist and list engine for a top-tier film and TV countdown video channel (like WatchMojo, WhatCulture, Screen Rant, CineFix).
Today's date: {today_str}.

Current industry signals:
- Upcoming releases next 14-60 days: {', '.join(ctx['upcoming']) or 'Blockbusters and seasonal releases'}
- Currently trending & search spikes: {', '.join(ctx['trending']) or 'Top box office and streaming hits'}
- TV on the air now: {', '.join(ctx['tv_airing']) or 'Prestige series'}

STEP 1: Generate 15 distinct candidate countdown topics across the 5 "demand triggers":
  1. TRENDING (new release buzz, trailers, cast news, viral moments in last 7 days)
  2. CALENDAR (releases in next 14 days, anniversaries: 10/20/25/30 yrs, seasonal holidays)
  3. STREAMING (JustWatch catalog: new arrivals / leaving Netflix, Max, Prime, Hulu, Disney+)
  4. DEBATE (split opinions: overrated, underrated, worst sequels, plot twist failures, snubs)
  5. EVERGREEN (highest grossing inflation-adjusted, IMDb Top 250, Letterboxd favorites, director retrospectives)

Score every candidate 1-10 on each factor:
  - demand (search + social volume) -> weight 0.30
  - momentum (growth over last 7 days) -> weight 0.25
  - debate (comment bait & discussion) -> weight 0.20
  - gap (few strong existing high-production videos) -> weight 0.15
  - visuals (clip appeal & cinematic action) -> weight 0.10

Overall score = (demand * 0.30) + (momentum * 0.25) + (debate * 0.20) + (gap * 0.15) + (visuals * 0.10), rounded to 1 decimal place.

Choose format for each topic:
  - "top5" for trending / fast topics
  - "top10" for standard / default topics
  - "top15" for deep evergreen topics with abundant strong candidates

Return JSON array of 15 candidate objects:
[
  {{
    "id": "slug-id",
    "topic": "Topic Title (e.g. Top 10 Mind-Bending Sci-Fi Movies on Netflix Right Now)",
    "trigger": "trending|calendar|streaming|debate|evergreen",
    "trigger_name": "Trending & Viral|Upcoming & Anniversaries|JustWatch Streaming|Debate & Controversy|Evergreen Classics",
    "format": "top5|top10|top15",
    "why_now": "One concise sentence explaining why this topic will blow up right now",
    "demand": 8.5,
    "momentum": 9.0,
    "debate": 7.5,
    "gap": 8.0,
    "visuals": 9.5,
    "total_score": 8.6,
    "suggested_entries": ["Entry 1", "Entry 2", "Entry 3"]
  }}
]
"""
    try:
        candidates = gemini.ask_json(prompt, temperature=0.7)
        if isinstance(candidates, list) and len(candidates) > 0:
            candidates.sort(key=lambda x: x.get("total_score", 0), reverse=True)
            if category_filter and category_filter != "all":
                candidates = [c for c in candidates if c.get("trigger") == category_filter] or candidates
            return candidates
    except Exception as e:
        logger.error("Gemini failed in discover_topics: %s", e)

    # High-quality fallback candidates if Gemini fails or rate-limits
    return _default_candidate_topics()


def get_or_discover_topics(category_filter: str | None = None, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return cached candidate topics if available; otherwise discover and cache.
    Ensures Countdown Studio loads instantly without re-scoring every page visit."""
    if not force_refresh:
        with SessionLocal() as s:
            row = s.get(AppSetting, "countdown_candidates")
            cached = (row.value or {}) if row else {}
            items = cached.get("items", [])
            updated_at = cached.get("updated_at")
            if items:
                if updated_at:
                    try:
                        dt = datetime.datetime.fromisoformat(updated_at)
                        if (datetime.datetime.now(UTC) - dt).total_seconds() < 72 * 3600:
                            out = items
                            if category_filter and category_filter != "all":
                                out = [c for c in items if c.get("trigger") == category_filter] or items
                            return out
                    except Exception:
                        pass
                else:
                    out = items
                    if category_filter and category_filter != "all":
                        out = [c for c in items if c.get("trigger") == category_filter] or items
                    return out

    candidates = discover_topics(category_filter=None)
    if candidates:
        with SessionLocal() as s:
            row = s.get(AppSetting, "countdown_candidates")
            payload = {
                "items": candidates,
                "updated_at": datetime.datetime.now(UTC).isoformat(),
            }
            if row:
                row.value = payload
            else:
                s.add(AppSetting(key="countdown_candidates", value=payload))
            s.commit()

    out = candidates
    if category_filter and category_filter != "all":
        out = [c for c in candidates if c.get("trigger") == category_filter] or candidates
    return out


def _default_candidate_topics() -> list[dict[str, Any]]:
    """High-performing fallback topics matching the 5 triggers."""
    return [
        {
            "id": "top-10-sci-fi-netflix",
            "topic": "Top 10 Mind-Bending Sci-Fi Movies on Netflix Right Now",
            "trigger": "streaming",
            "trigger_name": "JustWatch Streaming",
            "format": "top10",
            "why_now": "Huge search demand for streaming recommendations with certified high audience scores.",
            "demand": 9.2, "momentum": 8.5, "debate": 8.0, "gap": 7.5, "visuals": 9.0, "total_score": 8.6,
            "suggested_entries": ["Annihilation", "Arrival", "Ex Machina", "Interstellar"],
        },
        {
            "id": "top-10-psychological-thrillers",
            "topic": "Top 10 Psychological Thrillers That Will Break Your Brain",
            "trigger": "evergreen",
            "trigger_name": "Evergreen Classics",
            "format": "top10",
            "why_now": "Letterboxd and Reddit discussions around twist endings have perennial 80%+ retention.",
            "demand": 9.0, "momentum": 7.8, "debate": 9.2, "gap": 8.0, "visuals": 8.5, "total_score": 8.6,
            "suggested_entries": ["Shutter Island", "Memento", "Prisoners", "Gone Girl"],
        },
        {
            "id": "top-5-upcoming-blockbusters",
            "topic": "Top 5 Upcoming Blockbusters That Will Dominate the Box Office",
            "trigger": "calendar",
            "trigger_name": "Upcoming & Anniversaries",
            "format": "top5",
            "why_now": "Capitalizes on imminent pre-release trailer drops and premiere momentum.",
            "demand": 9.5, "momentum": 9.4, "debate": 7.0, "gap": 7.0, "visuals": 9.5, "total_score": 8.7,
            "suggested_entries": ["Avatar: Fire and Ash", "Spider-Man 4", "The Batman Part II"],
        },
        {
            "id": "top-10-overrated-oscars",
            "topic": "Top 10 Most Overrated Best Picture Winners in History",
            "trigger": "debate",
            "trigger_name": "Debate & Controversy",
            "format": "top10",
            "why_now": "Maximum debate potential guaranteed to trigger fierce comment section activity.",
            "demand": 8.8, "momentum": 8.0, "debate": 9.8, "gap": 8.2, "visuals": 7.5, "total_score": 8.6,
            "suggested_entries": ["Crash", "Shakespeare in Love", "Green Book", "The Artist"],
        },
        {
            "id": "top-10-highest-grossing-inflation",
            "topic": "Top 10 Highest-Grossing Movies of All Time (Adjusted for Inflation)",
            "trigger": "evergreen",
            "trigger_name": "Evergreen Classics",
            "format": "top10",
            "why_now": "Debunks modern box office myths with real historical ticket-sale data.",
            "demand": 9.1, "momentum": 7.5, "debate": 8.5, "gap": 8.5, "visuals": 8.0, "total_score": 8.4,
            "suggested_entries": ["Gone with the Wind", "Avatar", "Titanic", "Star Wars: A New Hope"],
        },
    ]


def _lookup_tmdb_metadata(title: str, year: int | None = None) -> dict[str, Any]:
    """Fetch verified TMDB metadata, poster, backdrop, and JustWatch streaming info."""
    if not tmdb.configured():
        return {}
    try:
        results = tmdb.search(title)
        if not results:
            return {}
        from ..identity import select
        match = select(title, results, year=year)
        if not match:
            return {}

        media_type = match.get("media_type") or "movie"
        tmdb_id = match.get("tmdb_id") or match.get("id")
        if not tmdb_id:
            return {}

        # Fetch enriched data including watch/providers (JustWatch)
        d = tmdb.get(f"/{media_type}/{tmdb_id}", append_to_response="watch/providers,external_ids")
        us_providers = ((d.get("watch/providers") or {}).get("results") or {}).get("US") or {}
        flatrate = [p.get("provider_name") for p in (us_providers.get("flatrate") or [])][:3]
        rent = [p.get("provider_name") for p in (us_providers.get("rent") or [])][:2]

        streaming_summary = " · ".join(flatrate) if flatrate else ("Rent on " + ", ".join(rent) if rent else "Availability unconfirmed")

        return {
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "title": d.get("title") or d.get("name") or title,
            "year": int((d.get("release_date") or d.get("first_air_date") or "0000")[:4] or (year or 0)),
            "poster": tmdb.image_url(d.get("poster_path"), "w500"),
            "backdrop": tmdb.image_url(d.get("backdrop_path"), "original"),
            "rating": round(float(d.get("vote_average") or 0.0), 1),
            "vote_count": int(d.get("vote_count") or 0),
            "overview": (d.get("overview") or "")[:240],
            "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
            "where_to_watch": streaming_summary,
            "availability_status": "available" if flatrate or rent else "unavailable",
            "justwatch_url": (us_providers.get("link") or ""),
        }
    except Exception as e:
        logger.warning("Error fetching TMDB metadata for '%s': %s", title, e)
        return {}


def _find_matching_studio_project(tmdb_id: int | None, title: str) -> dict[str, Any] | None:
    """Check if our system already has a rendered video or footage for this entry."""
    with SessionLocal() as s:
        p = None
        if tmdb_id:
            p = s.query(StudioProject).filter(StudioProject.tmdb_id == tmdb_id).order_by(StudioProject.id.desc()).first()
        if not p and title:
            p = s.query(StudioProject).filter(StudioProject.title.ilike(f"%{title}%")).order_by(StudioProject.id.desc()).first()
        if p:
            return {
                "project_id": p.id,
                "has_render": bool(p.render),
                "seconds": round((p.render or {}).get("seconds", 0.0), 1) if p.render else 0.0,
                "thumbnail": (p.render or {}).get("thumbnail"),
                "stage": p.stage,
            }
    return None


def generate_countdown(
    topic: str,
    format_type: str = "top10",
    custom_instructions: str = "",
    market: str = "",
) -> dict[str, Any]:
    """STEPS 2–5: Researched Countdown Generation & Scripting.

    Builds defensible ranking from real data (ratings, cultural impact, buzz, sentiment)
    and scripts for maximum retention with verified JustWatch/IMDb stats.
    """
    count = 10
    if format_type == "top5":
        count = 5
    elif format_type == "top15":
        count = 15

    prompt = f"""You are the senior film/TV content strategist and list engine for a major countdown video channel.
Your task is to build a high-retention, fully researched countdown for:
TOPIC: "{topic}"
FORMAT: {format_type.upper()} ({count} ranked entries + 2-3 honorable mentions)
ADDITIONAL NOTES: {custom_instructions or "None"}
{("SEARCH & COMPETITION DATA (vidIQ, real YouTube numbers) — use it: favour titles people search for, answer what they ask, "
  "and make our list clearly different from the top existing videos (don't copy their order or picks wholesale):" + chr(10) + market) if market else ""}

Follow the 5-Step Methodology strictly:

STEP 2 — RANKING LOGIC (Defensible 0-100 Scoring):
- Rank entries based on real, defensible metrics:
    * Critic & audience ratings (IMDb, Letterboxd, TMDB composite): 35%
    * Cultural impact & box office (adjusted for inflation, awards, longevity): 25%
    * Current cultural buzz & search trends: 20%
    * Fan community sentiment: 20%
- Minimum vote count: ignore obscure outliers.
- Only include legitimate, universally recognized contenders. Do not duplicate franchise entries unless required by the topic.

STEP 3 — ENTERTAINMENT ORDERING & RETENTION SCRIPTING:
- Entries must be ranked from #{count} down to #1.
- #1 MUST be deeply satisfying, but DO NOT make it overly predictable. Tease #1 in the opening 10-second hook without spoiling it.
- Place a surprise or controversial pick at #{3 if count <= 5 else 4} to provoke healthy disagreement and boost watch time.
- The lowest-ranked opener (#{count}) must still have a killer hook—NO weak openers.
- Provide 2–3 Honorable Mentions to address fan favorites that just missed the cut before revealing #1.
- Conclude with a punchy closing debate question specifically designed to ignite the comment section.

STEP 4 — FACT RULES:
- State verified box office numbers, years, Oscars/awards, and US streaming availability (JustWatch).
- Never fabricate quotes or statistics. If a stat cannot be 100% verified, add an explanation to "unverified_flags".
- Specify streaming availability for US platforms (Netflix, Max, Prime Video, Hulu, Disney+, Apple TV+, etc.).

STEP 5 — OUTPUT JSON FORMAT ONLY (Do not wrap in markdown or explanation):
{{
  "topic": "{topic}",
  "why_now": "One sharp sentence on why this countdown is trending right now",
  "format": "{format_type}",
  "title_options": [
    "Catchy YouTube Title 1 (Under 60 chars)",
    "Catchy YouTube Title 2 (Under 60 chars)",
    "Catchy YouTube Title 3 (Under 60 chars)"
  ],
  "thumbnail_text": "3-4 word explosive thumbnail text (e.g. #1 WILL SHOCK YOU)",
  "hook_script": "The exact spoken cold open: ONE or TWO punchy sentences, at most 20 words (about 6-8 seconds), that hook the viewer and tease the #1 pick.",
  "entries": [
    {{
      "rank": {count},
      "title": "Movie or Show Title",
      "year": 2020,
      "score": 88.5,
      "key_stat": "Box Office: $1.1B | IMDb: 8.4 | 4 Academy Awards",
      "why_it_ranks": "Why it earns this exact spot on the countdown",
      "fun_fact": "A fascinating behind-the-scenes trivia fact",
      "where_to_watch": "Netflix (US) or Max (US)",
      "sources": ["IMDb", "Letterboxd", "Box Office Mojo", "JustWatch"]
    }}
  ],
  "honorable_mentions": [
    "Title (Year) — Quick one-line reason it barely missed",
    "Title (Year) — Quick one-line reason it barely missed"
  ],
  "closing_question": "Provocative debate question ending the video to spark comments",
  "unverified_flags": []
}}
"""

    # No made-up fallback list (audit 2026-10-13): a failed or cap-paused call is reported
    # as such — it used to return a fixed sci-fi list with invented stats for any topic.
    data = gemini.ask_json(prompt, temperature=0.5, max_tokens=16384)
    if not isinstance(data, dict) or not data.get("entries"):
        raise ValueError("The AI didn't return a ranked list — try Re-run research")

    # Enrich each entry with TMDB posters, backdrops, real streaming data, and local footage links
    entries = data.get("entries", [])
    for entry in entries:
        title = entry.get("title", "")
        year = entry.get("year")
        meta = _lookup_tmdb_metadata(title, year)
        if meta:
            entry["tmdb_id"] = meta.get("tmdb_id")
            entry["media_type"] = meta.get("media_type")
            entry["poster"] = meta.get("poster")
            entry["backdrop"] = meta.get("backdrop")
            entry["tmdb_rating"] = meta.get("rating")
            entry["overview"] = meta.get("overview")
            suggested = entry.get("where_to_watch")
            entry["where_to_watch"] = meta.get("where_to_watch") or "Availability unconfirmed"
            entry["availability_scope"] = {"territory": "US", "retrieved_at": datetime.datetime.now(UTC).isoformat(),
                                           "source": meta.get("justwatch_url"), "provider": "TMDB/JustWatch",
                                           "status": meta.get("availability_status", "unavailable")}
            if suggested and suggested != entry["where_to_watch"]:
                entry["availability_conflict"] = {"suggested": suggested, "provider_snapshot": entry["where_to_watch"]}
            entry["justwatch_url"] = meta.get("justwatch_url")

        # Check for matching project in our database
        proj = _find_matching_studio_project(entry.get("tmdb_id"), title)
        if proj:
            entry["linked_project"] = proj
            entry["has_footage"] = proj.get("has_render", False)
        else:
            entry["linked_project"] = None
            entry["has_footage"] = False

    data["generated_at"] = datetime.datetime.now(UTC).isoformat()
    return data


def list_saved_countdowns() -> list[dict[str, Any]]:
    with SessionLocal() as s:
        row = s.get(AppSetting, "saved_countdowns")
        return list((row.value or {}).get("items", [])) if row else []


def save_countdown_item(countdown: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as s:
        row = s.get(AppSetting, "saved_countdowns")
        items = list((row.value or {}).get("items", [])) if row else []
        # Keep a countdown's id and update it in place (audit 2026-10-11: every save
        # minted a new id and a duplicate, which broke the link to its queue row).
        if not countdown.get("id"):
            countdown["id"] = f"cd-{int(datetime.datetime.now(UTC).timestamp() * 1000)}"
        items = [c for c in items if c.get("id") != countdown["id"]]
        items.insert(0, countdown)
        items = items[:50]  # keep up to 50 saved lists
        if row:
            row.value = {"items": items}
        else:
            s.add(AppSetting(key="saved_countdowns", value={"items": items}))
        s.commit()
    return countdown


def delete_saved_countdown(countdown_id: str) -> bool:
    with SessionLocal() as s:
        row = s.get(AppSetting, "saved_countdowns")
        if not row:
            return False
        items = [x for x in (row.value or {}).get("items", []) if x.get("id") != countdown_id]
        row.value = {"items": items}
        s.commit()
    return True


def _resolve_title(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The entry's real TMDB title, or None. A countdown is made from trailers, so an
    entry that isn't a movie or show (e.g. "Ned Leeds Becomes Hobgoblin…") can't be used."""
    title = (entry.get("title") or "").strip()
    if not title or not tmdb.configured():
        return None
    try:
        results = tmdb.search(title)
    except Exception:  # noqa: BLE001 — treated as "not found"
        return None
    from ..identity import select
    return select(title, results, year=entry.get("year"), medium=entry.get("media_type"), tmdb_id=entry.get("tmdb_id"))


def countdown_description(countdown: dict[str, Any], entries: list[dict[str, Any]],
                          chapter_lines: list[str]) -> str:
    """YouTube description for a countdown: hook, chapters, where to stream, question."""
    topic = countdown.get("topic", "Top Countdown")
    lines = []
    if countdown.get("hook_script"):
        lines.append(f'"{countdown["hook_script"]}"\n')
    lines.append(f"In this episode of Screen Central, we are counting down the {topic}!\n")
    lines.append("⏳ CHAPTERS:")
    lines.extend(chapter_lines)
    lines.append("")
    lines.append("🎬 RANKED ENTRIES & WHERE TO STREAM:")
    for e in entries:
        lines.append(f"• #{e['rank']} {e['title']} ({e.get('year', '')})")
        scope = e.get("availability_scope") or {}
        if (e.get("where_to_watch") and scope.get("status") == "available"
                and scope.get("provider") == "TMDB/JustWatch" and scope.get("territory") == "US"):
            try:
                retrieved = datetime.datetime.fromisoformat(scope["retrieved_at"])
                if datetime.timedelta(0) <= datetime.datetime.now(UTC) - retrieved < datetime.timedelta(days=1):
                    lines.append(f"  Streaming (US, checked {retrieved.date()}): {e['where_to_watch']}")
            except (KeyError, TypeError, ValueError):
                pass
        # Free-form model statistics are draft suggestions, never source-backed copy.
    if countdown.get("closing_question"):
        lines.append(f"\n💬 DISCUSSION:\n{countdown['closing_question']}\n")
    lines.append("\n🔔 Subscribe to Screen Central for daily film & TV countdowns, reviews, and rankings!")
    lines.append(tmdb.ATTRIBUTION)
    return "\n".join(lines)
