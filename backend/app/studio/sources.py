"""Source quality for research findings.

Gemini's search grounding returns Google redirect links and any site it found,
including forums and fan pages. Here we resolve each link to the real page and
tier it, so only findings backed by an official or trusted outlet are used as
facts (owner's guide: official studios first, then IMDb, Screen Rant, IGN,
Rolling Stone, Vulture, AP News; avoid fan-made / unofficial content).
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

OFFICIAL = (
    "20thcenturystudios.com", "disney.com", "disneyplus.com", "hulu.com", "netflix.com", "about.netflix.com",
    "warnerbros.com", "hbo.com", "max.com", "a24films.com", "pixar.com", "paramount.com", "paramountplus.com",
    "sonypictures.com", "universalpictures.com", "focusfeatures.com", "lionsgate.com", "amazon.com",
    "amazonmgmstudios.com", "primevideo.com", "apple.com", "tv.apple.com", "peacocktv.com", "neonrated.com",
    "marvel.com", "dc.com", "searchlightpictures.com", "imdb.com", "themoviedb.org",
)
TRUSTED = (
    "screenrant.com", "ign.com", "rollingstone.com", "vulture.com", "apnews.com", "deadline.com", "variety.com",
    "hollywoodreporter.com", "wikipedia.org", "collider.com", "empireonline.com", "ew.com", "indiewire.com",
    "rottentomatoes.com", "nytimes.com", "theguardian.com", "bbc.com", "bbc.co.uk", "latimes.com", "polygon.com",
    "theverge.com", "slashfilm.com", "gamesradar.com", "comingsoon.net", "joblo.com", "boxofficemojo.com",
    "the-numbers.com", "metacritic.com", "cnn.com", "usatoday.com", "reuters.com", "denofgeek.com", "thewrap.com",
)
LOW = ("reddit.com", "quora.com", "fandom.com", "tiktok.com", "x.com", "twitter.com", "facebook.com",
       "instagram.com", "youtube.com", "pinterest.com", "medium.com", "blogspot.com", "wordpress.com")


def domain(url_or_title: str) -> str:
    host = urlparse(url_or_title).netloc if "://" in url_or_title else url_or_title
    host = host.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _matches(host: str, names: tuple[str, ...]) -> bool:
    return any(host == n or host.endswith("." + n) for n in names)


def tier(url_or_title: str) -> str:
    host = domain(url_or_title)
    if _matches(host, OFFICIAL):
        return "official"
    if _matches(host, TRUSTED):
        return "trusted"
    if _matches(host, LOW):
        return "low"
    return "other"


def resolve(url: str, timeout: float = 8.0) -> str:
    """Follow Google's grounding redirect to the real page (best effort)."""
    if "grounding-api-redirect" not in url:
        return url
    try:
        r = httpx.head(url, follow_redirects=False, timeout=timeout)
        loc = r.headers.get("location")
        if loc:
            return loc
        r = httpx.get(url, follow_redirects=True, timeout=timeout)
        return str(r.url)
    except Exception:
        return url


def annotate(research: dict) -> dict:
    """Resolve every source link and tag it with its tier (in place)."""
    for s in research.get("sources", []):
        s["url"] = resolve(s.get("url", ""))
        # Grounding titles are usually the domain; prefer the resolved host.
        s["domain"] = domain(s["url"]) if "://" in s.get("url", "") else domain(s.get("title", ""))
        s["tier"] = tier(s["domain"] or s.get("title", ""))
    return research


def usable(claim: dict, sources: list[dict]) -> bool:
    """A finding counts as a fact only if an official or trusted source backs it."""
    return any(i < len(sources) and sources[i].get("tier") in ("official", "trusted") for i in claim.get("sources", []))
