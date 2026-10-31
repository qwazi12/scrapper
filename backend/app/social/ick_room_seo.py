"""AI SEO for The ICK Room (owner, 2026-10-31): relationship / dating talk videos, not movie clips.

Styled on youtube.com/@TonightsConversation (style only — never their name or brand):
a plain-words story hook, a "…" cliffhanger, one 😬/😳-type emoji, often a closing question to the
viewer; the description sums up the dilemma, how opinions split, and asks the comments to weigh in.
Written from the video's own title/filename; no TMDB lookup. The result is marked research.kind="ick_room".
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..studio import gemini

KIND = "ick_room"
RETRY_AFTER = 24 * 3600
_failed: dict[int, float] = {}   # row id -> when its SEO last failed (the scheduler waits before retrying)

EXAMPLES = """- She found out her fiancé is already married… 😳 and now she AND his wife are both pregnant.
- He told her “I’m not ready for a relationship”… then 3 months later got into one with somebody else.
- 20 years married… 😔 but they’ve spent the last 4 years living like roommates. Marriage over?
- She’s 32 with 3 kids by 3 different fathers… 😬 Should a man give her grace?
- He Served Her Divorce Papers on Her Birthday… Then Brought the Side Chick to Court"""


def source_title(item) -> str:
    """The video's own title: the filename without the date prefix, TikTok ID and extension."""
    raw = item.video_name or item.title or ""
    raw = re.sub(r"\.(mp4|mov|webm)$", "", raw, flags=re.I)
    raw = re.sub(r"^\d{8}[_ ]", "", raw)
    raw = re.sub(r"[_ ]\d{15,}$", "", raw)
    raw = raw.replace("_", " ")
    return re.sub(r"\s+", " ", raw).strip(" .") or (item.title or "")


def prompt(item) -> str:
    return f"""Write Facebook video metadata for a relationship / dating talk video (a real person tells their
situation and a host or panel reacts). Optimise it for search and for comments.

The video's own title (from the creator; underscores replaced apostrophes or punctuation, fix those):
{source_title(item)}

Style to follow (titles from a top channel in this niche; copy the STYLE, never the words or any show name):
{EXAMPLES}

Rules:
- Describe ONLY what the video's own title says; never invent ages, numbers, names, quotes or outcomes.
- "title": max 95 characters. A plain-words story hook, a "…" cliffhanger, at most one emoji (😬 😳 💔 👀),
  and where it fits a short question to the viewer. Keep "P1"/"P2" as "(Part 1)"/"(Part 2)" at the end.
- "caption": 2-3 short paragraphs: the situation, the question it raises / how people would split on it,
  then ask viewers to comment their side. No hashtags in the caption. Never name a show, channel or brand.
- "hashtags": 5-8 relationship / dating hashtags (e.g. #relationships #datingadvice #cheating), no spaces,
  never #shorts or movie hashtags.
Return JSON: {{"title": "...", "caption": "...", "hashtags": ["#..."]}}"""


def generate(item, cache: dict | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(metadata, research marker) for one Ick Room row (sync; run in a thread)."""
    try:
        meta = gemini.ask_json(prompt(item), temperature=0.7)
        if not isinstance(meta, dict) or not meta.get("title"):
            raise gemini.GeminiError("AI returned no title")
    except Exception:
        _failed[item.id] = time.time()
        raise
    _failed.pop(item.id, None)
    tags = [("#" + t.lstrip("#").replace(" ", "")) for t in (meta.get("hashtags") or []) if str(t).strip()][:8]
    return ({"title": str(meta["title"])[:100], "caption": str(meta.get("caption") or ""), "hashtags": tags},
            {"kind": KIND, "from": source_title(item)})


def needs_seo(item, now: float | None = None) -> bool:
    if (item.research or {}).get("kind") == KIND:
        return False
    failed = _failed.get(item.id)
    return failed is None or (now or time.time()) - failed >= RETRY_AFTER
