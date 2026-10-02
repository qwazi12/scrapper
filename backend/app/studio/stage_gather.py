"""Stage: gather — the fact sheet (TMDB) + web research (guide's sources)."""

from __future__ import annotations

import pathlib

import httpx

from ..db import SessionLocal
from ..models import StudioProject
from . import gemini, sources, tmdb
from .runner import project_dir, stage

# The guide's trusted sources, in its order of preference.
GUIDE_SOURCES = (
    "the official studio/network/streamer pages and press releases, IMDb, Screen Rant, IGN, "
    "Rolling Stone, Vulture, AP News, Deadline, Variety, The Hollywood Reporter"
)


def _download(url: str | None, dest: pathlib.Path) -> str | None:
    if not url:
        return None
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    try:
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return str(dest)
    except Exception:
        return None


def research_prompt(f: dict) -> str:
    kind = "TV series" if f["media_type"] == "tv" else "movie"
    cast = ", ".join(f"{c['actor']} as {c['character']}" for c in f["cast"][:8] if c.get("character"))
    return f"""Research the {kind} "{f['title']}" ({f.get('primary_date') or 'date unknown'}) for a
2–4 minute YouTube trailer breakdown and release-date news video.

Known from TMDB (verify, don't repeat blindly): directed/created by {', '.join(f['directors']) or 'unknown'};
cast {cast or 'unknown'}; synopsis: {f['overview'] or 'none'}.

Search {GUIDE_SOURCES}. Prefer official sources; ignore fan-made trailers and rumors.
Report, as short factual sentences:
1. The confirmed release date(s) and where to watch (theaters / which streamer / network), with any recent date changes.
2. The premise and genre exactly as official sources describe it.
3. What the latest official trailer shows: key moments in order, tone, any reveals.
4. Who plays whom, and notable cast/crew facts (previous work, reunions).
5. Production facts: studio, filming, source material (book/game/comic), budget if reported.
6. Reception of the trailer or early reviews, if any.
7. Anything sources disagree on — say which source says what.
Do not use Reddit, forums, fan wikis, social media or YouTube comments as sources.
Do not speculate. If something is unknown, say it is unknown."""


@stage("gather")
def gather(project_id: int) -> str:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        media_type, tmdb_id = p.media_type, p.tmdb_id

    facts = tmdb.fact_sheet(media_type, tmdb_id)
    assets = project_dir(project_id) / "assets"
    assets.mkdir(exist_ok=True)
    local = {
        "poster": _download(facts["poster"], assets / "poster.jpg"),
        "backdrops": [x for i, u in enumerate(facts["backdrops"])
                      if (x := _download(u, assets / f"backdrop_{i:02d}.jpg"))],
        "cast": {},
    }
    for i, c in enumerate(facts["cast"][:8]):
        path = _download(c.get("profile"), assets / f"cast_{i:02d}.jpg")
        if path:
            local["cast"][c["actor"]] = path
    facts["local"] = local

    research = sources.annotate(gemini.research(research_prompt(facts)))

    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.title = facts["title"]
        p.facts = facts
        p.research = research
        s.commit()
    good = sum(1 for x in research["sources"] if x.get("tier") in ("official", "trusted"))
    backed = sum(1 for c in research["claims"] if sources.usable(c, research["sources"]))
    return (f"{facts['title']}: {len(facts['cast'])} cast, {len(facts['videos'])} videos, "
            f"{len(local['backdrops'])} backdrops, {len(research['sources'])} web sources "
            f"({good} official/trusted); {backed}/{len(research['claims'])} findings backed by them")
