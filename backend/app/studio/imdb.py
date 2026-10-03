"""IMDb video source — official trailers, clips and promos as direct MP4s.

Uses IMDb's public GraphQL endpoint (the one imdb.com's own pages call). No
YouTube involved, so it works where YouTube blocks the server's IP. Playback
URLs are signed and expire: always fetch them right before downloading.
Verified 2026-10-01 from a home connection: Send Help -> 1080p 141 s trailer.
"""

from __future__ import annotations

from typing import Any

import httpx

GRAPHQL = "https://caching.graphql.imdb.com/"
HEADERS = {"Content-Type": "application/json", "x-imdb-client-name": "imdb-web-next"}
TIMEOUT = 30.0


class IMDbError(Exception):
    pass


def _query(q: str) -> dict[str, Any]:
    from .. import costs
    costs.record_free("imdb")
    r = httpx.post(GRAPHQL, json={"query": q}, headers=HEADERS, timeout=TIMEOUT)
    if not r.is_success:
        raise IMDbError(f"IMDb returned HTTP {r.status_code}")
    data = r.json()
    if data.get("errors"):
        raise IMDbError(f"IMDb error: {str(data['errors'])[:200]}")
    return data.get("data") or {}


def _safe_id(i: str, prefix: str) -> str:
    if not (i.startswith(prefix) and i[2:].isdigit()):
        raise IMDbError(f"Not an IMDb id: {i!r}")
    return i


def list_videos(imdb_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Official videos for a title: id, name, type (Trailer/Clip/Teaser/…), seconds."""
    tid = _safe_id(imdb_id, "tt")
    d = _query(f'query {{ title(id: "{tid}") {{ primaryVideos(first: {int(limit)}) {{ edges {{ node {{ '
               f'id name {{ value }} contentType {{ displayName {{ value }} }} runtime {{ value }} }} }} }} }} }}')
    edges = (((d.get("title") or {}).get("primaryVideos") or {}).get("edges")) or []
    return [{
        "id": e["node"]["id"],
        "name": (e["node"].get("name") or {}).get("value", ""),
        "type": ((e["node"].get("contentType") or {}).get("displayName") or {}).get("value", ""),
        "seconds": (e["node"].get("runtime") or {}).get("value") or 0,
        "origin": "imdb",
        "page": f"https://www.imdb.com/video/{e['node']['id']}/",
    } for e in edges]


def audience(imdb_id: str, reviews: int = 10) -> dict[str, Any]:
    """Audience reaction on IMDb: rating + votes, Metacritic, MOVIEmeter /
    TVmeter popularity rank and its direction, and the average star rating of
    the newest user reviews (+ their one-line summaries) — plus whether a
    trailer exists, in the same call."""
    tid = _safe_id(imdb_id, "tt")
    d = _query(f'query {{ title(id: "{tid}") {{ '
               f'ratingsSummary {{ aggregateRating voteCount }} '
               f'meterRanking {{ currentRank rankChange {{ changeDirection difference }} }} '
               f'metacritic {{ metascore {{ score }} }} '
               f'reviews(first: {int(reviews)}, sort: {{ by: SUBMISSION_DATE, order: DESC }}) {{ total edges {{ node {{ '
               f'authorRating summary {{ originalText }} }} }} }} '
               f'primaryVideos(first: 10) {{ edges {{ node {{ contentType {{ displayName {{ value }} }} }} }} }} '
               f'}} }}')
    t = d.get("title") or {}
    rs, mr = t.get("ratingsSummary") or {}, t.get("meterRanking") or {}
    revs = [e["node"] for e in ((t.get("reviews") or {}).get("edges") or [])]
    stars = [r["authorRating"] for r in revs if r.get("authorRating")]
    kinds = [(((e["node"].get("contentType") or {}).get("displayName") or {}).get("value") or "")
             for e in ((t.get("primaryVideos") or {}).get("edges") or [])]
    return {
        "rating": rs.get("aggregateRating"),
        "votes": rs.get("voteCount") or 0,
        "metascore": (((t.get("metacritic") or {}).get("metascore") or {}).get("score")),
        "meter_rank": mr.get("currentRank"),
        "meter_change": ((mr.get("rankChange") or {}).get("changeDirection") or "").upper() or None,
        "meter_change_by": (mr.get("rankChange") or {}).get("difference"),
        "reviews_total": (t.get("reviews") or {}).get("total") or 0,
        "review_avg": round(sum(stars) / len(stars), 1) if stars else None,
        "review_lines": [((r.get("summary") or {}).get("originalText") or "")[:120] for r in revs[:3]],
        "has_trailer": "Trailer" in kinds,
        "page": f"https://www.imdb.com/title/{tid}/",
    }


def mp4_url(video_id: str) -> str:
    """Best direct MP4 (1080p, then 720p, then any non-HLS)."""
    vid = _safe_id(video_id, "vi")
    d = _query(f'query {{ video(id: "{vid}") {{ playbackURLs {{ displayName {{ value }} url }} }} }}')
    urls = {(u.get("displayName") or {}).get("value", ""): u.get("url") for u in
            ((d.get("video") or {}).get("playbackURLs") or [])}
    for want in ("1080p", "720p", "SD", "480p"):
        if urls.get(want):
            return urls[want]
    for name, u in urls.items():
        if name != "AUTO" and u:
            return u
    raise IMDbError(f"No downloadable MP4 for {vid}")


def download(video_id: str, dest, max_bytes: int = 400_000_000) -> int:
    """Stream the MP4 to dest; returns bytes written. Refuses oversized files."""
    import pathlib
    url = mp4_url(video_id)
    n = 0
    dest = pathlib.Path(dest)
    part = dest.with_name(dest.name + ".part")  # only a finished file gets the real name
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        if not r.is_success:
            raise IMDbError(f"Download of {video_id} failed: HTTP {r.status_code}")
        with open(part, "wb") as fh:
            from .. import control
            for chunk in r.iter_bytes(1024 * 1024):
                control.check()  # Stop mid-download
                n += len(chunk)
                if n > max_bytes:
                    raise IMDbError(f"{video_id} is larger than {max_bytes // 1_000_000} MB; skipped")
                fh.write(chunk)
    part.replace(dest)
    return n
