"""YouTube Data API v3 — read-only trailer stats (views, likes, comments).

Used to rank breakdown candidates, never to download (YouTube blocks the
server's IP for downloads; stats calls are a normal keyed API). Quota: the
default is 10,000 units/day and videos.list costs 1 unit per call of up to
50 ids, so a full candidate refresh is ~2 units. search.list (100 units) is
deliberately not used: TMDB already gives each title's trailer video id.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from ..config import settings

logger = logging.getLogger("scrapper.youtube")
URL = "https://www.googleapis.com/youtube/v3/videos"
TIMEOUT = 20.0


class YouTubeError(Exception):
    pass


def configured() -> bool:
    return bool(settings.youtube_api_key)


def video_stats(ids: list[str], retries: int = 3) -> dict[str, dict[str, Any]]:
    """{video_id: {views, likes, comments, published_at, channel}} for the ids
    that exist. Missing/private videos are simply absent."""
    if not configured():
        raise YouTubeError("YOUTUBE_API_KEY is not set on the server")
    from .. import costs
    out: dict[str, dict[str, Any]] = {}
    ids = [i for i in dict.fromkeys(ids) if i]
    for n in range(0, len(ids), 50):
        chunk = ids[n:n + 50]
        last = ""
        for attempt in range(retries):
            res = httpx.get(URL, params={"part": "statistics,snippet", "id": ",".join(chunk),
                                         "key": settings.youtube_api_key}, timeout=TIMEOUT)
            if res.is_success:
                costs.record_free("youtube")
                for it in res.json().get("items", []):
                    st, sn = it.get("statistics") or {}, it.get("snippet") or {}
                    out[it["id"]] = {
                        "views": int(st.get("viewCount") or 0),
                        "likes": int(st.get("likeCount") or 0) if "likeCount" in st else None,
                        "comments": int(st.get("commentCount") or 0) if "commentCount" in st else None,
                        "published_at": sn.get("publishedAt"),
                        "channel": sn.get("channelTitle"),
                    }
                break
            # Body only — the key is in the URL, never log the request.
            last = f"HTTP {res.status_code}: {res.text[:200]}"
            if res.status_code not in (429, 500, 502, 503, 504):
                raise YouTubeError(f"YouTube stats failed: {last}")
            time.sleep((2 ** attempt) + random.random())  # bounded backoff with jitter
        else:
            raise YouTubeError(f"YouTube stats failed: {last}")
    return out
