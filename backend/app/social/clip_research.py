"""TMDB research for Posting Queue clips -> grounded, SEO-friendly metadata.

1. Identify: Gemini reads the clip's title / filename / hashtags / channel and
   names the movie or show it most likely comes from, with a confidence.
2. Look up: TMDB search -> title, year, genres, overview, cast with character
   names, keywords, US streaming providers.
3. Write: title / caption / hashtags built from those facts (names the title,
   actor and character; hashtags from TMDB keywords). Below MIN_CONFIDENCE the
   clip is NOT tied to a title — a wrong movie name is worse for SEO than none.

The match is stored on the queue row (`research`) so the owner can see it and
a re-run reuses it instead of paying for the lookup again.
"""

from __future__ import annotations

from typing import Any

from ..studio import gemini, tmdb

MIN_CONFIDENCE = 0.6


def identify_prompt(item_text: str) -> str:
    return f"""This is a short vertical clip from a movie or TV show, posted on a clips channel.
Clip details:
{item_text}

Which movie or TV series is this clip most likely from? Use the character names, actor names,
hashtags and situation. If it is not from a movie or show, or you cannot tell, say so.
Return JSON: {{"title": "official title or empty", "media_type": "movie" | "tv" | "", "year": 2021 or null,
"confidence": 0.0-1.0, "reason": "one short line"}}"""


def _item_text(item) -> str:
    parts = [f"Title: {item.title or ''}", f"Filename: {item.video_name or ''}", f"Hashtags: {item.tags or ''}",
             f"Channel/folder: {item.source or item.pipeline or ''}"]
    if item.description and item.description != item.title:
        parts.append(f"Description: {item.description}")
    return "\n".join(parts)


def _best_match(results: list[dict], media_type: str, year: int | None) -> dict | None:
    def score(r: dict) -> float:
        s = r.get("popularity", 0) / 100.0
        if media_type and r["media_type"] == media_type:
            s += 5
        if year and (r.get("date") or "")[:4] == str(year):
            s += 3
        return s
    results = [r for r in results if r.get("tmdb_id")]
    return max(results, key=score) if results else None


def lookup(media_type: str, tmdb_id: int) -> dict[str, Any]:
    d = tmdb.get(f"/{media_type}/{tmdb_id}", append_to_response="credits,keywords,watch/providers")
    kw = d.get("keywords") or {}
    keywords = [k["name"] for k in (kw.get("keywords") or kw.get("results") or [])][:15]
    us = ((d.get("watch/providers") or {}).get("results") or {}).get("US") or {}
    providers = [p["provider_name"] for p in (us.get("flatrate") or [])][:3]
    cast = [{"actor": c.get("name"), "character": c.get("character") or ""}
            for c in (d.get("credits") or {}).get("cast", [])[:8]]
    date = d.get("release_date") or d.get("first_air_date") or ""
    return {
        "tmdb_id": tmdb_id, "media_type": media_type, "title": d.get("title") or d.get("name") or "",
        "year": date[:4] or None, "genres": [g["name"] for g in d.get("genres", [])],
        "overview": d.get("overview") or "", "cast": cast, "keywords": keywords, "watch_on": providers,
        "source": f"https://www.themoviedb.org/{media_type}/{tmdb_id}",
    }


def research(item, cache: dict | None = None) -> dict[str, Any]:
    """Identify the clip's title and fetch its TMDB facts (sync; run in a thread)."""
    if item.research and item.research.get("matched") is not None:
        return item.research
    guess = gemini.ask_json(identify_prompt(_item_text(item)), temperature=0.1)
    if not isinstance(guess, dict):
        guess = {}
    conf = float(guess.get("confidence") or 0)
    title = (guess.get("title") or "").strip()
    out: dict[str, Any] = {"matched": False, "guess": title, "confidence": round(conf, 2),
                           "reason": str(guess.get("reason") or "")[:200]}
    if not title or conf < MIN_CONFIDENCE or not tmdb.configured():
        if not tmdb.configured():
            out["reason"] = "TMDB is not configured on the server"
        return out
    key = f"{guess.get('media_type')}|{title.lower()}|{guess.get('year')}"
    if cache is not None and key in cache:
        facts = cache[key]
    else:
        match = _best_match(tmdb.search(title), guess.get("media_type") or "", guess.get("year"))
        facts = lookup(match["media_type"], match["tmdb_id"]) if match else None
        if cache is not None:
            cache[key] = facts
    if not facts:
        out["reason"] = f"TMDB has no match for '{title}'"
        return out
    return {**out, "matched": True, **facts}


def metadata_prompt(item, r: dict[str, Any]) -> str:
    clip = _item_text(item)
    if r.get("matched"):
        cast = "; ".join(f"{c['actor']} as {c['character']}" for c in r["cast"] if c.get("character"))
        facts = (f"From TMDB: {r['title']} ({r.get('year') or 'year unknown'}), "
                 f"{'TV series' if r['media_type'] == 'tv' else 'movie'}; genres: {', '.join(r['genres'])}; "
                 f"cast: {cast}; keywords: {', '.join(r['keywords'])}; "
                 f"streaming (US): {', '.join(r['watch_on']) or 'unknown'}; synopsis: {r['overview']}")
    else:
        facts = "No confident match to a movie or show: do NOT name any title, actor or character not already in the clip details."
    return f"""Write YouTube Shorts / Reels / TikTok metadata for this clip, optimised for search.

Clip details:
{clip}

{facts}

Rules:
- Describe what happens ONLY from the clip details; never invent scenes or quotes.
- Use TMDB facts for names: the show/movie title, the actor and the character when the clip involves them.
- "title": max 90 characters, a curiosity hook that includes the show/movie name when known.
- "caption": 2-3 short sentences: what happens + who plays the character + where to watch if known,
  ending with a question that invites comments.
- "hashtags": 8-12, mixing #shorts, the title as a hashtag, the main actor, the genre and 3-5 TMDB keywords;
  no spaces inside a hashtag.
Return JSON: {{"title": "...", "caption": "...", "hashtags": ["#..."]}}"""


def generate(item, cache: dict | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(metadata, research) for one queue item (sync; run in a thread)."""
    r = research(item, cache)
    meta = gemini.ask_json(metadata_prompt(item, r), temperature=0.6)
    if not isinstance(meta, dict) or not meta.get("title"):
        raise gemini.GeminiError("AI returned no title")
    tags = [("#" + t.lstrip("#").replace(" ", "")) for t in (meta.get("hashtags") or []) if t.strip()][:12]
    return {"title": str(meta["title"])[:100], "caption": str(meta.get("caption") or ""), "hashtags": tags}, r
