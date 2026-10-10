"""FreeMediaHeckYeah (FMHY) integration module.

Indexes curated video scrapers, trailer extractors, movie discovery databases,
and royalty-free sound effects/music libraries from the community FMHY knowledge base.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("scrapper.studio.fmhy")

FMHY_DOCS_BASE = "https://raw.githubusercontent.com/fmhy/edit/main/docs"
TIMEOUT = 12.0

# Curated high-value tools & repositories from FMHY for video automation
FMHY_CURATED_TOOLS = [
    {
        "category": "Movie & TV Discovery",
        "name": "Trakt.tv",
        "url": "https://trakt.tv",
        "description": "Community tracking of trending movies, anticipated lists, and TV episode schedules.",
    },
    {
        "category": "Movie & TV Discovery",
        "name": "Letterboxd",
        "url": "https://letterboxd.com",
        "description": "Film rankings, reviews, and high-engagement user countdown lists.",
    },
    {
        "category": "Movie & TV Discovery",
        "name": "JustWatch",
        "url": "https://www.justwatch.com",
        "description": "Streaming availability database across all US and international platforms.",
    },
    {
        "category": "Royalty-Free Audio & SFX",
        "name": "Free Music Archive (FMA)",
        "url": "https://freemusicarchive.org",
        "description": "Curated library of high-quality background music beds under Creative Commons.",
    },
    {
        "category": "Royalty-Free Audio & SFX",
        "name": "Incompetech",
        "url": "https://incompetech.com/music/royalty-free/music.html",
        "description": "Cinematic ambient, suspense, and dramatic background scores by Kevin MacLeod.",
    },
    {
        "category": "Royalty-Free Audio & SFX",
        "name": "Freesound",
        "url": "https://freesound.org",
        "description": "Massive collaborative sound effect library (whooshes, risers, cinematic impacts).",
    },
    {
        "category": "Video Scraping & Downloaders",
        "name": "Cobalt",
        "url": "https://cobalt.tools",
        "description": "Fast, privacy-focused media downloader for YouTube, TikTok, and X without ads or trackers.",
    },
    {
        "category": "Video Scraping & Downloaders",
        "name": "yt-dlp",
        "url": "https://github.com/yt-dlp/yt-dlp",
        "description": "The command-line media extraction powerhouse supporting over 1,800 platforms.",
    },
    {
        "category": "Video Scraping & Downloaders",
        "name": "NewPipe Extractor",
        "url": "https://github.com/TeamNewPipe/NewPipeExtractor",
        "description": "Keyless streaming extraction engine bypassing Google YouTube APIs.",
    },
]


def get_curated_resources() -> list[dict[str, Any]]:
    """Return verified FMHY resources organized by category."""
    return FMHY_CURATED_TOOLS


def fetch_fmhy_markdown_section(doc_name: str = "video-tools.md") -> list[dict[str, str]]:
    """Fetch live tools from FMHY GitHub repo docs."""
    url = f"{FMHY_DOCS_BASE}/{doc_name}"
    try:
        res = httpx.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if not res.is_success:
            return []
        text = res.text
        # Parse markdown links: [Name](URL) - Description
        matches = re.findall(r"\[([^\]]+)\]\((https?://[^\)]+)\)(?:\s*-\s*([^\n]+))?", text)
        results = []
        for name, link, desc in matches[:30]:
            if any(skip in link for skip in ("reddit.com/r/FREEMEDIAHECKYEAH", "github.com/fmhy")):
                continue
            results.append({
                "name": name.strip(),
                "url": link.strip(),
                "description": (desc or "").strip(),
            })
        return results
    except Exception as e:
        logger.warning("FMHY fetch failed for %s: %s", doc_name, e)
        return []
