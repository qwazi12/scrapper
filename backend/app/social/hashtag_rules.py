"""Per-platform hashtags (owner, 2026-11-07). One reviewed tag pool, a different cut per platform.

YouTube: up to 10 — 1 series/film name, 2 the brand's niche tag, 3 alternate title / main character, then the rest;
Shorts keep #shorts inside the 10. Instagram 5, Facebook 3, X 2. ICK: Facebook = 1 topic + #tonightsconversation
#theickroom, X = #theickroom + 1 topic. Removed everywhere: #shorts outside YouTube, generic reach tags, other
channels'/brands' names (incl. another brand's niche tag). Reviewed words are never edited: hashtags inside the
text stay and count toward the cap; only the trailing hashtag line is rebuilt."""
from __future__ import annotations

import re
import unicodedata

CAP = {"youtube": 10, "instagram": 5, "facebook": 3, "x": 2, "twitter": 2}
NICHE = {"Cinama": "#MovieClips", "LongForm": "#TrailerBreakdown", "Dramarama": "#ShortDrama", "Manhwa": "#ManhwaRecap"}
OWN_BRAND = {"Cinama": {"#ScreenCentral"}, "Dramarama": {"#DramaRama"}, "TheIckRoom": {"#theickroom", "#tonightsconversation"}}
REACH = {"#viral", "#trending", "#fyp", "#foryou", "#explore",
         # beyond the owner's list (shown in the review): a #foryou variant and other platforms' names
         "#foryoupage", "#reels", "#tiktok", "#movietok"}
SHORTS = {"#shorts", "#short"}
ICK_BRAND = ("#tonightsconversation", "#theickroom")
HASHTAG = re.compile(r"#[\w]+", re.UNICODE)


def key(tag: str) -> str:
    return unicodedata.normalize("NFC", tag).casefold()


def _bare(tag: str) -> str:
    return re.sub(r"\W", "", key(tag))


def camel(text: str) -> str | None:
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", text).replace("'", "").replace("’", ""))
    fix = lambda w: w.capitalize() if w.isupper() and len(w) > 1 else w[:1].upper() + w[1:]
    return "#" + "".join(fix(w) for w in words) if words else None


def drama_lead(source: str | None, video_name: str | None) -> list[str]:
    """DramaRama: the series name (source folder) and its alternate title (file name `_EP01_<alt> EP01`)."""
    series = re.sub(r"\s*\[\d+\]\s*$", "", (source or "").partition(" / ")[2])
    alt = re.search(r"_EP\d+_(.+?)\s*-?\s*EP\d+", video_name or "")
    return [t for t in (camel(series) if series else None, camel(alt.group(1)) if alt else None) if t]


def platform_tags(pool, network, *, brand, profile=None, lead=(), shorts=False, other_names=()):
    """The hashtags `network` gets, in order. `brand` picks the niche tag (profile name or "LongForm")."""
    cap = CAP[network]
    others = {_bare(n) for n in other_names if _bare(n)}
    for b, tags in OWN_BRAND.items():
        if b != profile:
            others |= {_bare(t) for t in tags}
    niche = NICHE.get(brand)
    other_niches = {key(t) for t in NICHE.values()} - ({key(niche)} if niche else set())
    seen, rest = set(), []
    for t in list(lead) + list(pool):
        k = key(t)
        if (k in seen or k in REACH or k in SHORTS or k in other_niches or (niche and k == key(niche))
                or _bare(t) in others):
            continue
        seen.add(k)
        rest.append(t)
    if profile == "TheIckRoom":
        topic = [t for t in rest if key(t) not in ICK_BRAND][:1]
        if network in ("x", "twitter"):
            return ["#theickroom"] + topic
        if network == "facebook":
            return topic + list(ICK_BRAND)
    ordered = rest[:1] + ([niche] if niche else []) + rest[1:]
    if network == "youtube" and shorts:
        return ordered[:cap - 1] + ["#shorts"]
    return ordered[:cap]


def split_trailing(text: str) -> tuple[str, list[str]]:
    """(text without its trailing hashtag-only lines, the hashtags on them)."""
    lines = (text or "").rstrip().split("\n")
    tags: list[str] = []
    while lines and lines[-1].strip() and not HASHTAG.sub("", lines[-1]).strip():
        tags = HASHTAG.findall(lines.pop()) + tags
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines), tags


def apply(text: str, pool, network, **kw) -> str:
    """`text` with its trailing hashtag line rebuilt for `network`. Hashtags inside the words stay and count."""
    body, trailing = split_trailing(text)
    inline = {key(t) for t in HASHTAG.findall(body)}
    ordered = platform_tags(HASHTAG.findall(body) + list(trailing) + list(pool), network, **kw)
    chosen = [t for t in ordered if key(t) not in inline]
    limit = max(0, CAP[network] - len(inline))
    if len(chosen) > limit and chosen and key(chosen[-1]) == "#shorts":
        chosen = chosen[:limit - 1] + chosen[-1:] if limit else []      # a Short keeps #shorts inside the cap
    chosen = chosen[:limit]
    return "\n\n".join(x for x in (body, " ".join(chosen)) if x)
