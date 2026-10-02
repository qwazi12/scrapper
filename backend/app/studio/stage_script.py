"""Stage: script — a 2–4 min trailer breakdown written ONLY from the storyboard.

1. Storyboard: every TMDB fact [F#], web finding [R#] (with its source pages)
   and trailer moment [T#] gets an id.
2. Draft (owner's guide prompt): 7th-grade, one continuous narrative, name in
   paragraph 1, release date in paragraph 2, subscribe CTA at the end; every
   factual sentence cites the ids it relies on.
3. Check: a second pass judges each sentence against the storyboard and fixes
   or drops anything unsupported. Changes are kept for the owner to review.
4. Metadata: YouTube title, description (sources + TMDB credit), tags.
"""

from __future__ import annotations

from ..config import settings
from ..db import SessionLocal
from ..models import StudioProject
from . import gemini, tmdb
from .runner import stage

WPM = 180


def storyboard(facts: dict, research: dict | None, shots: list | None) -> dict:
    F: list[str] = []
    f = facts
    kind = "TV series" if f.get("media_type") == "tv" else "movie"
    F.append(f"Title: {f.get('title')} ({kind})")
    if f.get("tagline"):
        F.append(f"Tagline: {f['tagline']}")
    if f.get("overview"):
        F.append(f"Official synopsis: {f['overview']}")
    if f.get("genres"):
        F.append(f"Genres: {', '.join(f['genres'])}")
    for r in f.get("releases", []):
        F.append(f"Release ({r['country']}, {r['type']}): {r['date']}" + (f" — {r['note']}" if r.get("note") else ""))
    if f.get("primary_date"):
        F.append(f"Primary release date: {f['primary_date']}")
    if f.get("directors"):
        F.append(f"Directed/created by: {', '.join(f['directors'])}")
    if f.get("writers"):
        F.append(f"Written by: {', '.join(f['writers'])}")
    if f.get("studios") or f.get("networks"):
        F.append(f"Studio/network: {', '.join((f.get('studios') or []) + (f.get('networks') or []))}")
    for c in f.get("cast", [])[:10]:
        if c.get("character"):
            F.append(f"Cast: {c['actor']} plays {c['character']}")
    if f.get("runtime"):
        F.append(f"Runtime: {f['runtime']} minutes")

    R: list[dict] = []
    srcs = (research or {}).get("sources", [])
    for c in (research or {}).get("claims", []):
        text = (c.get("text") or "").strip()
        if text:
            R.append({"text": text, "sources": [srcs[i] for i in c.get("sources", []) if i < len(srcs)]})

    T = [s["description"] for s in (shots or [])
         if s.get("source_type") == "Trailer" and s.get("description") and not s.get("card")][:60]

    return {"F": F, "R": R, "T": T, "sources": srcs}


def _board_text(b: dict) -> str:
    lines = ["FACTS (TMDB):"] + [f"[F{i + 1}] {x}" for i, x in enumerate(b["F"])]
    lines += ["", "WEB FINDINGS (grounded in the linked sources):"]
    lines += [f"[R{i + 1}] {x['text']}  (sources: {', '.join(s['title'] for s in x['sources']) or 'unlisted'})"
              for i, x in enumerate(b["R"])]
    lines += ["", "WHAT THE OFFICIAL TRAILER SHOWS, in order:"] + [f"[T{i + 1}] {x}" for i, x in enumerate(b["T"])]
    return "\n".join(lines)


def draft_prompt(board: str, title: str, minutes: float, channel: str) -> str:
    words = int(minutes * WPM)
    return f"""You are a professional scriptwriter creating a chronological Trailer Breakdown and
release-date news script for text-to-speech narration on the YouTube channel "{channel}".

Use ONLY the storyboard below. Every fact you state must be in it. Never invent plot,
characters, places, dates or reception. If the storyboard does not cover something, leave it out.

Rules:
- About {words} words ({minutes:g} minutes at {WPM} words per minute).
- 7th-grade reading level, short sentences, simple words, friendly and enthusiastic.
- One continuous narrative: no headings, no scene labels, no lists.
- Paragraph 1 names "{title}" and hooks the viewer in the first two sentences.
- Paragraph 2 gives the release date and where to watch.
- Then walk through what the trailer shows, in order, with vivid but accurate description,
  and who plays whom.
- Do not quote dialogue from the trailer.
- End with a strong closing line that invites viewers to subscribe to {channel} for more
  trailer breakdowns and release-date updates.

Storyboard:
{board}

Return JSON:
{{"sentences": [{{"paragraph": 1, "text": "...", "refs": ["F1","R2","T3"]}}, ...],
  "youtube_title": "max 90 characters, includes the title and 'Trailer Breakdown' or 'Release Date'",
  "tags": ["10-15 search tags"]}}
"refs" lists the storyboard ids a sentence relies on; [] only for pure opinion/transition/CTA lines."""


def check_prompt(board: str, sentences: list[dict]) -> str:
    numbered = "\n".join(f"{i + 1}. {s['text']}" for i, s in enumerate(sentences))
    return f"""You are a strict fact-checker. Judge each numbered script sentence ONLY against the
storyboard. Opinion, enthusiasm, transitions and the subscribe line are fine ("ok").

For each sentence return {{"i": n, "verdict": "ok"|"unsupported", "reason": "short", "fix": "..."}}
- "unsupported" when it states a fact (plot, character, place, date, platform, cast, reception)
  that the storyboard does not support, or contradicts it.
- "fix": for unsupported sentences, a corrected sentence using only storyboard facts, or "" to drop it.

Storyboard:
{board}

Script:
{numbered}

Return a JSON array, one object per sentence."""


@stage("script")
def script(project_id: int) -> str:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        facts, research, shots, minutes = p.facts, p.research, p.shots, p.target_minutes
    if not facts:
        raise RuntimeError("Run 'gather' first")
    board = storyboard(facts, research, shots)
    board_txt = _board_text(board)
    channel = settings.studio_channel_name

    draft = gemini.ask_json(draft_prompt(board_txt, facts["title"], minutes, channel), temperature=0.7)
    sentences = [{"paragraph": int(x.get("paragraph") or 1), "text": str(x.get("text") or "").strip(),
                  "refs": [str(r) for r in (x.get("refs") or [])]}
                 for x in (draft.get("sentences") or []) if str(x.get("text") or "").strip()]
    if not sentences:
        raise gemini.GeminiError("draft came back empty")

    verdicts = gemini.ask_json(check_prompt(board_txt, sentences), temperature=0.1)
    by_i = {int(v.get("i", 0)): v for v in verdicts if isinstance(v, dict)} if isinstance(verdicts, list) else {}
    final, changes = [], []
    for i, sen in enumerate(sentences):
        v = by_i.get(i + 1, {})
        if v.get("verdict") == "unsupported":
            fix = str(v.get("fix") or "").strip()
            changes.append({"original": sen["text"], "fix": fix, "reason": v.get("reason", "")})
            if not fix:
                continue
            sen = {**sen, "text": fix, "changed": True, "original": sen["text"], "reason": v.get("reason", "")}
        final.append({**sen, "check": "ok" if v else "unchecked"})

    for i, sen in enumerate(final):
        sen["i"] = i + 1
    words = sum(len(x["text"].split()) for x in final)
    srcs = [x for x in board["sources"] if x.get("url")]
    desc_facts = " ".join(x["text"] for x in final[:3])
    description = "\n".join([
        desc_facts, "",
        "Sources:", *[f"- {x['title']}: {x['url']}" for x in srcs[:8]],
        f"- TMDB: {facts.get('source')}", "",
        tmdb.ATTRIBUTION,
    ])
    result = {
        "sentences": final,
        "changes": changes,
        "word_count": words,
        "est_seconds": round(words / WPM * 60),
        "youtube_title": str(draft.get("youtube_title") or f"{facts['title']} Trailer Breakdown")[:100],
        "description": description,
        "tags": [str(t) for t in (draft.get("tags") or [])][:15],
        "storyboard": board,
        "model": settings.gemini_model,
    }
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.script = result
        p.plan = None  # sentences changed
        s.commit()
    return (f"{len(final)} sentences, {words} words (~{result['est_seconds'] // 60}m{result['est_seconds'] % 60:02d}s); "
            f"fact-check fixed {sum(1 for c in changes if c['fix'])}, dropped {sum(1 for c in changes if not c['fix'])}")
