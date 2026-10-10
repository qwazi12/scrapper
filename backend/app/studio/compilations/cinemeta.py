"""Stremio Cinemeta API client for keyless movie & series metadata.

Publicly accessible, zero-credential JSON endpoints that provide rich IMDb metadata,
ratings, cast, director, genres, and official YouTube trailer keys by IMDb ID.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("scrapper.studio.cinemeta")

BASE_URL = "https://v3-cinemeta.strem.io"
TIMEOUT = 15.0


def get_movie_meta(imdb_id: str) -> dict[str, Any] | None:
    """Fetch movie metadata by IMDb ID (e.g. 'tt8036976') without API keys."""
    if not imdb_id or not imdb_id.startswith("tt"):
        return None
    url = f"{BASE_URL}/meta/movie/{imdb_id}.json"
    try:
        res = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        if not res.is_success:
            return None
        data = res.json().get("meta", {})
        return _format_meta(data, "movie")
    except Exception as e:
        logger.warning("Cinemeta movie fetch failed for %s: %s", imdb_id, e)
        return None


def get_series_meta(imdb_id: str) -> dict[str, Any] | None:
    """Fetch series/TV metadata by IMDb ID without API keys."""
    if not imdb_id or not imdb_id.startswith("tt"):
        return None
    url = f"{BASE_URL}/meta/series/{imdb_id}.json"
    try:
        res = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        if not res.is_success:
            return None
        data = res.json().get("meta", {})
        return _format_meta(data, "tv")
    except Exception as e:
        logger.warning("Cinemeta series fetch failed for %s: %s", imdb_id, e)
        return None


def get_top_movies() -> list[dict[str, Any]]:
    """Fetch top/trending movies from Stremio Cinemeta catalog."""
    url = f"{BASE_URL}/catalog/movie/top.json"
    try:
        res = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        if not res.is_success:
            return []
        metas = res.json().get("metas", [])
        return [_format_meta(m, "movie") for m in metas]
    except Exception as e:
        logger.warning("Cinemeta top movies failed: %s", e)
        return []


def get_top_series() -> list[dict[str, Any]]:
    """Fetch top/trending series from Stremio Cinemeta catalog."""
    url = f"{BASE_URL}/catalog/series/top.json"
    try:
        res = httpx.get(url, timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        if not res.is_success:
            return []
        metas = res.json().get("metas", [])
        return [_format_meta(m, "tv") for m in metas]
    except Exception as e:
        logger.warning("Cinemeta top series failed: %s", e)
        return []


def _format_meta(raw: dict[str, Any], media_type: str) -> dict[str, Any]:
    trailers = []
    for t in raw.get("trailers", []):
        if isinstance(t, dict) and t.get("source"):
            trailers.append({
                "youtube_id": t["source"],
                "type": t.get("type", "Trailer"),
                "url": f"https://www.youtube.com/watch?v={t['source']}",
            })

    return {
        "imdb_id": raw.get("imdb_id") or raw.get("id"),
        "title": raw.get("name") or "",
        "year": str(raw.get("year", "")),
        "media_type": media_type,
        "genres": raw.get("genres", []),
        "director": raw.get("director", []),
        "cast": raw.get("cast", []),
        "rating": float(raw.get("imdbRating") or 0.0),
        "synopsis": raw.get("description", ""),
        "poster": raw.get("poster"),
        "background": raw.get("background"),
        "runtime": raw.get("runtime", ""),
        "trailers": trailers,
    }
