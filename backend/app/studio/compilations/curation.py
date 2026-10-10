"""Curation & discovery engine for Top 5 / Top 10 Countdown Lists.

Provides high-performing thematic list presets (Sci-Fi, Thrillers, Horror, Action,
Streaming exclusives) and queries TMDB Discover + IMDb to assemble candidates with
verified trailers, cast details, and streaming providers.
"""

from __future__ import annotations

import datetime
from typing import Any

from .. import imdb
from .. import tmdb
from ...db import SessionLocal
from ...models import AppSetting

PRESET_THEMES = [
    {
        "id": "upcoming_scifi",
        "title": "Top 10 Upcoming Sci-Fi Movies You Need To Watch",
        "description": "Mind-bending futuristic concepts, space operas, and dystopian thrillers hitting theaters and streaming.",
        "media_type": "movie",
        "genre_ids": [878],  # Science Fiction
        "timeframe": "upcoming",
        "default_count": 10,
    },
    {
        "id": "mindbending_thrillers",
        "title": "Top 5 Mind-Bending Psychological Thrillers",
        "description": "Twisting mysteries, dark paranoia, and high-tension plot twists with shocking climaxes.",
        "media_type": "movie",
        "genre_ids": [53, 9648],  # Thriller, Mystery
        "timeframe": "trending",
        "default_count": 5,
    },
    {
        "id": "horror_anticipation",
        "title": "Top 10 Terrifying Horror Releases To Watch With The Lights On",
        "description": "Supernatural hauntings, psychological dread, and visceral creature features.",
        "media_type": "movie",
        "genre_ids": [27],  # Horror
        "timeframe": "upcoming_or_recent",
        "default_count": 10,
    },
    {
        "id": "action_blockbusters",
        "title": "Top 5 High-Octane Action Blockbusters",
        "description": "Relentless stunts, explosive spectacle, and adrenaline-fueled thrill rides.",
        "media_type": "movie",
        "genre_ids": [28, 12],  # Action, Adventure
        "timeframe": "upcoming",
        "default_count": 5,
    },
    {
        "id": "streaming_netflix",
        "title": "Top 10 Movies & TV Shows On Netflix Right Now",
        "description": "Trending binge-worthy hits and critically acclaimed originals available to stream today.",
        "media_type": "all",
        "provider_id": 8,  # Netflix
        "timeframe": "popular",
        "default_count": 10,
    },
    {
        "id": "bingeworthy_tv",
        "title": "Top 5 Must-Watch TV Series With 100% Rotten Tomatoes Energy",
        "description": "Prestige drama, compelling mini-series, and gripping season arcs.",
        "media_type": "tv",
        "timeframe": "trending",
        "default_count": 5,
    },
]


def list_themes() -> list[dict[str, Any]]:
    """Return all curated countdown themes."""
    return PRESET_THEMES


def get_made_compilations() -> list[str]:
    """Retrieve permanent deduplication ledger of already-created compilation titles/IDs."""
    with SessionLocal() as s:
        row = s.get(AppSetting, "compilations_made")
        if not row or not row.value:
            return []
        return row.value.get("titles", [])


def record_compilation_made(title: str, item_ids: list[int]) -> None:
    """Record a compilation into the deduplication ledger."""
    with SessionLocal() as s:
        row = s.get(AppSetting, "compilations_made")
        if not row:
            row = AppSetting(key="compilations_made", value={"titles": [], "items": []})
            s.add(row)
        val = dict(row.value or {})
        titles = list(val.get("titles", []))
        items = list(val.get("items", []))
        if title not in titles:
            titles.append(title)
        items.append({"title": title, "item_ids": item_ids, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
        row.value = {"titles": titles, "items": items}
        s.commit()


def discover_theme_candidates(
    theme_id: str | None = None,
    custom_query: str | None = None,
    media_type: str = "movie",
    genre_id: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Discover candidate titles for a countdown list from TMDB with Cinemeta fallback."""
    if not tmdb.configured():
        from . import cinemeta
        items = cinemeta.get_top_series() if media_type in ("tv", "series") else cinemeta.get_top_movies()
        return [
            {
                "tmdb_id": 0,
                "media_type": media_type,
                "imdb_id": m.get("imdb_id"),
                "title": m.get("title", ""),
                "date": m.get("year", ""),
                "overview": m.get("synopsis", ""),
                "poster": m.get("poster"),
                "backdrop": m.get("background"),
                "vote_average": m.get("rating", 0.0),
                "popularity": 80.0,
            }
            for m in items[:limit]
        ]


    theme = next((t for t in PRESET_THEMES if t["id"] == theme_id), None)
    today = datetime.date.today()
    params: dict[str, Any] = {
        "language": "en-US",
        "page": 1,
        "include_adult": "false",
    }

    m_type = media_type
    if theme:
        m_type = theme.get("media_type", "movie")
        if m_type == "all":
            m_type = "movie"
        if theme.get("genre_ids"):
            params["with_genres"] = ",".join(str(g) for g in theme["genre_ids"])
        if theme.get("provider_id"):
            params["watch_region"] = "US"
            params["with_watch_providers"] = str(theme["provider_id"])

        timeframe = theme.get("timeframe", "trending")
        if timeframe == "upcoming":
            params["primary_release_date.gte"] = today.isoformat()
            params["primary_release_date.lte"] = (today + datetime.timedelta(days=180)).isoformat()
            params["sort_by"] = "popularity.desc"
        elif timeframe == "upcoming_or_recent":
            params["primary_release_date.gte"] = (today - datetime.timedelta(days=60)).isoformat()
            params["primary_release_date.lte"] = (today + datetime.timedelta(days=120)).isoformat()
            params["sort_by"] = "popularity.desc"
        else:
            params["sort_by"] = "popularity.desc"
    elif genre_id:
        params["with_genres"] = str(genre_id)
        params["sort_by"] = "popularity.desc"

    if custom_query:
        # User searched directly
        path = f"/search/{m_type}"
        params["query"] = custom_query
    else:
        path = f"/discover/{m_type}"

    try:
        data = tmdb.get(path, **params)
        raw_items = data.get("results", [])
    except Exception:
        return []

    candidates = []
    for r in raw_items:
        if not r.get("id") or not (r.get("title") or r.get("name")):
            continue
        item_id = r["id"]
        title = r.get("title") or r.get("name") or ""
        date_str = r.get("release_date") or r.get("first_air_date") or ""
        overview = r.get("overview") or ""
        poster = tmdb.image_url(r.get("poster_path"), "w500")
        backdrop = tmdb.image_url(r.get("backdrop_path"), "w1280")

        # Quick fetch external IDs to verify IMDb ID
        imdb_id = None
        try:
            ext = tmdb.get(f"/{m_type}/{item_id}/external_ids")
            imdb_id = ext.get("imdb_id")
        except Exception:
            pass

        candidates.append({
            "tmdb_id": item_id,
            "media_type": m_type,
            "imdb_id": imdb_id,
            "title": title,
            "date": date_str,
            "overview": overview,
            "poster": poster,
            "backdrop": backdrop,
            "vote_average": r.get("vote_average", 0.0),
            "popularity": r.get("popularity", 0.0),
        })

        if len(candidates) >= limit:
            break

    return candidates
