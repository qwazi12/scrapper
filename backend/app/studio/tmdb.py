"""TMDB client (The Movie Database) — facts, calendar, cast, videos, images.

Auth (developer.themoviedb.org/docs/authentication-application): the Read
Access Token as `Authorization: Bearer` is TMDB's default; the v3 key as the
`api_key` query param gives the same access. We send the token first and fall
back to the key on a 401 (logged), so one bad credential never fails silently.

Hobby / non-commercial use (owner decision 2026-10-01). Every video
description credits TMDB, as its terms require:
  "This product uses the TMDB API but is not endorsed or certified by TMDB."
Accepts a v3 API key (query param) or a v4 read-access token (Bearer).
"""

from __future__ import annotations

from typing import Any

import logging

import httpx

from ..config import settings

logger = logging.getLogger("scrapper.studio.tmdb")

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
ATTRIBUTION = "This product uses the TMDB API but is not endorsed or certified by TMDB."
TIMEOUT = 20.0


class TMDBError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def configured() -> bool:
    return bool(settings.tmdb_read_token or settings.tmdb_api_key)


def _auths() -> list[tuple[str, dict[str, str], dict[str, str]]]:
    """Credentials to try, in order: (label, headers, query params)."""
    out = []
    accept = {"Accept": "application/json"}
    if settings.tmdb_read_token:
        out.append(("API_Read_Access_Token", {**accept, "Authorization": f"Bearer {settings.tmdb_read_token}"}, {}))
    key = settings.tmdb_api_key
    if key:
        if key.startswith("eyJ"):  # someone pasted the token into the key slot
            out.append(("TMDB_API_KEY", {**accept, "Authorization": f"Bearer {key}"}, {}))
        else:
            out.append(("TMDB_API_KEY", accept, {"api_key": key}))
    if not out:
        raise TMDBError("TMDB is not configured: set API_Read_Access_Token or TMDB_API_KEY on the server", 400)
    return out


def _auth() -> tuple[dict[str, str], dict[str, str]]:
    _, headers, params = _auths()[0]
    return headers, params


def get(path: str, **params: Any) -> dict[str, Any]:
    last = None
    for i, (label, headers, auth_params) in enumerate(_auths()):
        res = httpx.get(f"{API}{path}", headers=headers, params={**auth_params, **params}, timeout=TIMEOUT)
        if res.is_success:
            from .. import costs
            costs.record_free("tmdb")
            if i > 0:
                logger.warning("TMDB: first credential rejected; %s worked — check the other one", label)
            return res.json()
        last = res
        if res.status_code != 401:
            break  # only an auth failure is worth retrying with the other credential
    # Body only: the key may sit in the URL, never log the request itself.
    raise TMDBError(f"TMDB {path} failed ({last.status_code}): {last.text[:300]}", last.status_code)


def image_url(path: str | None, size: str = "original") -> str | None:
    return f"{IMG}/{size}{path}" if path else None


def _summary(r: dict[str, Any], media_type: str | None = None) -> dict[str, Any]:
    mt = media_type or r.get("media_type") or ("tv" if "first_air_date" in r else "movie")
    return {
        "tmdb_id": r.get("id"),
        "media_type": mt,
        "title": r.get("title") or r.get("name") or "",
        "date": r.get("release_date") or r.get("first_air_date") or "",
        "overview": r.get("overview") or "",
        "poster": image_url(r.get("poster_path"), "w342"),
        "popularity": r.get("popularity") or 0,
    }


def search(query: str) -> list[dict[str, Any]]:
    data = get("/search/multi", query=query, include_adult="false")
    return [_summary(r) for r in data.get("results", []) if r.get("media_type") in ("movie", "tv")]


def calendar(region: str | None = None) -> dict[str, list[dict[str, Any]]]:
    """Our own release calendar: upcoming films, airing TV, and what's trending."""
    region = region or settings.studio_region
    upcoming = get("/movie/upcoming", region=region).get("results", [])
    airing = get("/tv/on_the_air").get("results", [])
    trending = get("/trending/all/day").get("results", [])
    return {
        "upcoming_movies": sorted((_summary(r, "movie") for r in upcoming), key=lambda x: x["date"] or "9"),
        "on_the_air_tv": [_summary(r, "tv") for r in airing],
        "trending": [_summary(r) for r in trending if r.get("media_type") in ("movie", "tv")],
    }


_RELEASE_TYPES = {1: "Premiere", 2: "Theatrical (limited)", 3: "Theatrical", 4: "Digital", 5: "Physical", 6: "TV"}


def fact_sheet(media_type: str, tmdb_id: int, region: str | None = None) -> dict[str, Any]:
    """Everything TMDB knows that a breakdown needs, each fact tagged with its source."""
    region = region or settings.studio_region
    if media_type not in ("movie", "tv"):
        raise TMDBError(f"media_type must be movie or tv, not {media_type}", 400)
    append = "credits,videos,images,external_ids,keywords," + ("release_dates" if media_type == "movie" else "content_ratings")
    d = get(f"/{media_type}/{tmdb_id}", append_to_response=append, include_image_language="en,null")
    src = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"

    releases: list[dict[str, Any]] = []
    if media_type == "movie":
        for country in (d.get("release_dates") or {}).get("results", []):
            if country.get("iso_3166_1") not in (region, "US"):
                continue
            for r in country.get("release_dates", []):
                releases.append({
                    "country": country["iso_3166_1"],
                    "date": (r.get("release_date") or "")[:10],
                    "type": _RELEASE_TYPES.get(r.get("type"), str(r.get("type"))),
                    "note": r.get("note") or "",
                })
    else:
        if d.get("next_episode_to_air"):
            e = d["next_episode_to_air"]
            releases.append({"country": region, "date": e.get("air_date") or "", "type": "Next episode",
                             "note": f"S{e.get('season_number')}E{e.get('episode_number')} {e.get('name') or ''}".strip()})
        if d.get("first_air_date"):
            releases.append({"country": region, "date": d["first_air_date"], "type": "First aired", "note": ""})

    credits = d.get("credits") or {}
    cast = [{
        "actor": c.get("name"),
        "character": c.get("character") or "",
        "profile": image_url(c.get("profile_path"), "w185"),
        "order": c.get("order", i),
    } for i, c in enumerate(credits.get("cast", [])[:12])]
    crew = credits.get("crew", [])
    directors = [c["name"] for c in crew if c.get("job") == "Director"] or \
                [c.get("name") for c in d.get("created_by", [])]
    writers = [c["name"] for c in crew if c.get("department") == "Writing"][:4]

    videos = [{
        "name": v.get("name"),
        "type": v.get("type"),          # Trailer | Teaser | Clip | Featurette | ...
        "site": v.get("site"),          # YouTube | Vimeo
        "key": v.get("key"),
        "official": bool(v.get("official")),
        "published_at": v.get("published_at"),
        "url": (f"https://www.youtube.com/watch?v={v.get('key')}" if v.get("site") == "YouTube"
                else f"https://vimeo.com/{v.get('key')}" if v.get("site") == "Vimeo" else None),
    } for v in (d.get("videos") or {}).get("results", [])]
    videos.sort(key=lambda v: (v["type"] != "Trailer", not v["official"], v["published_at"] or ""), reverse=False)

    images = d.get("images") or {}
    pick = lambda arr, n, size: [image_url(i.get("file_path"), size) for i in arr[:n]]

    return {
        "tmdb_id": tmdb_id,
        "media_type": media_type,
        "title": d.get("title") or d.get("name") or "",
        "tagline": d.get("tagline") or "",
        "overview": d.get("overview") or "",
        "genres": [g["name"] for g in d.get("genres", [])],
        "runtime": d.get("runtime") or (d.get("episode_run_time") or [None])[0],
        "status": d.get("status") or "",
        "primary_date": d.get("release_date") or d.get("first_air_date") or "",
        "releases": releases,
        "studios": [c["name"] for c in d.get("production_companies", [])][:4],
        "networks": [n["name"] for n in d.get("networks", [])][:3],
        "directors": directors,
        "writers": writers,
        "cast": cast,
        "videos": videos,
        "poster": image_url(d.get("poster_path"), "original"),
        "posters": pick(images.get("posters", []), 4, "original"),
        "backdrops": pick(images.get("backdrops", []), 12, "original"),
        "logos": pick(images.get("logos", []), 2, "original"),
        "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
        "keywords": [k["name"] for k in ((d.get("keywords") or {}).get("keywords")
                                          or (d.get("keywords") or {}).get("results") or [])][:12],
        "source": src,
        "attribution": ATTRIBUTION,
    }
