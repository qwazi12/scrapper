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

from .. import control
from ..config import settings
from ..db import SessionLocal
from ..models import StudioProject
from . import gemini, research_gate, sources as srcq, tmdb
from .runner import stage

WPM = 180


def storyboard(facts: dict, research: dict | None, shots: list | None, project_id: int | None = None) -> dict:
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
    for credit in f.get("writing_credits", []):
        F.append(f"{credit['job']}: {credit['name']}")
    if f.get("studios") or f.get("networks"):
        F.append(f"Studio/network: {', '.join((f.get('studios') or []) + (f.get('networks') or []))}")
    for c in f.get("cast", [])[:10]:
        if c.get("character"):
            F.append(f"Cast: {c['actor']} plays {c['character']}")
    if f.get("runtime"):
        F.append(f"Runtime: {f['runtime']} minutes")

    # Only retained source records with fresh exact-context owner review enter R.
    # A source-domain label or the generating model's verdict is not evidence.
    R: list[dict] = []
    srcs = (research or {}).get("sources", [])
    skipped = 0
    for c in (research or {}).get("claims", []):
        text = (c.get("text") or "").strip()
        if not text:
            continue
        if not srcq.usable(c, srcs, facts=facts, project_id=project_id):
            skipped += 1
            continue
        R.append({"text": text, "sources": [srcs[i] for i in c.get("sources", [])
                                            if type(i) is int and 0 <= i < len(srcs)],
                  "evidence": c.get("evidence")})

    T = [s["description"] for s in (shots or [])
         if s.get("source_type") == "Trailer" and s.get("description") and not s.get("card")][:60]

    good = [x for x in srcs if x.get("tier") in ("official", "trusted") or "tier" not in x]
    ctx = f.get("countdown") if isinstance(f.get("countdown"), dict) else None
    C = [f"Ranked #{ctx.get('rank')} of {ctx.get('total')} in {ctx.get('topic')}"] if ctx else []
    return {"F": F, "R": R, "T": T, "C": C, "sources": good, "skipped_findings": skipped, "project_id": project_id, "identity": {k: f.get(k) for k in ("title", "primary_date", "media_type", "region")}}


def _board_text(b: dict) -> str:
    lines = ["FACTS (TMDB):"] + [f"[F{i + 1}] {x}" for i, x in enumerate(b["F"])]
    lines += ["", "WEB FINDINGS (grounded in the linked sources):"]
    lines += [f"[R{i + 1}] {x['text']}  (sources: {', '.join(s['title'] for s in x['sources']) or 'unlisted'})"
              for i, x in enumerate(b["R"])]
    lines += ["", "SHOT LOG OF THE OFFICIAL TRAILER, in order (evidence of what it shows — do not narrate shot by shot):"]
    lines += [f"[T{i + 1}] {x}" for i, x in enumerate(b["T"])]
    lines += ["", "EDITORIAL LIST POSITION (not a measured quality score):"]
    lines += [f"[C{i + 1}] {x}" for i, x in enumerate(b.get("C", []))]
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
- Then break the trailer down as a STORY, not a shot list:
  * Group the shot log into 4 to 6 story beats (for example: the setup, the inciting event,
    the turn, the escalation, the tease). Tell each beat in 2 to 4 sentences.
  * Never describe individual shots, camera framing, clothing or props unless they matter
    to the story. Do not say "the trailer opens with", "next", "then we see".
  * After each beat, add one line on what it suggests about the story or tone, but only
    when the synopsis or web findings support it (e.g. who has the upper hand).
  * Use character names and say who plays them the first time each appears.
  * Mention the director's track record or the genre only if the storyboard states it.
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


def countdown_notes(ctx: dict) -> str:
    """The countdown's research for this title, as citable storyboard lines [C1…]."""
    notes = [f"Ranked #{ctx.get('rank')} of {ctx.get('total')} in the countdown \"{ctx.get('topic')}\"."]
    # AI-generated ranking rationales/statistics are suggestions, never evidence.
    return "\n".join(["", "COUNTDOWN NOTES (researched for this list):"] +
                     [f"[C{i + 1}] {x}" for i, x in enumerate(notes)])


def countdown_prompt(board: str, title: str, minutes: float, ctx: dict) -> str:
    """One segment of a Top 5/10/15 countdown video (Countdowns Studio, 2026-10-12)."""
    words = int(minutes * WPM)
    return f"""You are writing ONE segment of a countdown video, "{ctx.get('topic')}", for text-to-speech
narration. This segment is entry #{ctx.get('rank')} of {ctx.get('total')}: "{title}".

Use ONLY the storyboard below (facts, web findings, trailer shot log and countdown notes). Never invent
plot, awards, box office, quotes or dates.

Rules:
- About {words} words ({minutes:g} minutes at {WPM} words per minute).
- The first sentence is exactly: "Number {ctx.get('rank')}: {title}." Then hook the viewer in one sentence.
- Say in a few sentences what it is about (from the synopsis), who stars, and why it earns this spot
  (from the countdown notes). Include one striking stat or fun fact if the notes have one.
- Say where to watch it if the notes say so.
- 7th-grade reading level, short sentences, enthusiastic. One continuous narrative, no lists.
- Do NOT add a subscribe line or a goodbye; the next entry follows straight after.

Storyboard:
{board}

Return JSON:
{{"sentences": [{{"paragraph": 1, "text": "...", "refs": ["F1","C1","T3"]}}, ...],
  "youtube_title": "{title}",
  "tags": ["5-10 search tags"]}}
"refs" lists the storyboard ids a sentence relies on; [] only for pure opinion/transition lines."""


def check_prompt(board: str, sentences: list[dict]) -> str:
    numbered = "\n".join(f"{i + 1}. {s['text']}" for i, s in enumerate(sentences))
    return f"""You are a strict fact-checker. Judge each numbered script sentence ONLY against the
storyboard. Opinion, enthusiasm, transitions and the subscribe line are fine ("ok").

For each sentence return {{"i": n, "verdict": "ok"|"unsupported", "kind": "fact"|"opinion"|"interpretation"|"transition"|"cta", "refs": ["F1"], "reason": "short", "fix": "..."}}
- Give exact valid storyboard refs for every fact or interpretation; [] only for nonfactual lines.
- Do not label a factual assertion as opinion to bypass checking.
- "unsupported" when it states a fact (plot, character, place, date, platform, cast, reception)
  that the storyboard does not support, or contradicts it.
- "fix": for unsupported sentences, a corrected sentence using only storyboard facts, or "" to drop it.

Storyboard:
{board}

Script:
{numbered}

Return a JSON array, one object per sentence."""


def seo_tags(model_tags: list, facts: dict) -> list[str]:
    """Model tags + the searchable basics (title, trailer/release terms, the
    leads, year) + TMDB keywords; de-duplicated, max 20."""
    year = (facts.get("primary_date") or "")[:4]
    title = facts.get("title", "")
    base = [title, f"{title} trailer", f"{title} trailer breakdown", f"{title} release date"]
    if year:
        base.append(f"{title} {year}")
    base += [c["actor"] for c in facts.get("cast", [])[:3]] + list(facts.get("directors", [])[:1])
    out, seen = [], set()
    for t in [*base, *[str(x) for x in model_tags], *facts.get("keywords", [])]:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out[:20]


@stage("script")
def script(project_id: int) -> str:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        facts, research, shots, minutes = p.facts, p.research, p.shots, p.target_minutes
    if not facts:
        raise RuntimeError("Run 'gather' first")
    board = storyboard(facts, research, shots, project_id)
    board_txt = _board_text(board)
    channel = settings.studio_channel_name

    ctx = facts.get("countdown") if isinstance(facts.get("countdown"), dict) else None
    control.check()
    prompt = countdown_prompt(board_txt, facts["title"], minutes, ctx) if ctx \
        else draft_prompt(board_txt, facts["title"], minutes, channel)
    draft = gemini.ask_json(prompt, temperature=0.7)
    control.check()
    sentences = [{"paragraph": int(x.get("paragraph") or 1), "text": str(x.get("text") or "").strip(),
                  "refs": [str(r) for r in (x.get("refs") or [])]}
                 for x in (draft.get("sentences") or []) if str(x.get("text") or "").strip()]
    if not sentences:
        raise gemini.GeminiError("draft came back empty")

    control.check()
    verdicts = gemini.ask_json(check_prompt(board_txt, sentences), temperature=0.1)
    control.check()
    checked, check_issues = research_gate.checked_sentences(sentences, verdicts, board)
    by_i = {v["i"]: v for v in verdicts if isinstance(v, dict) and type(v.get("i")) is int} if isinstance(verdicts, list) else {}
    final, changes = [], []
    for i, sen in enumerate(sentences):
        v = by_i.get(i + 1, {})
        if v.get("verdict") == "unsupported":
            fix = str(v.get("fix") or "").strip()
            changes.append({"original": sen["text"], "fix": fix, "reason": v.get("reason", "")})
            if not fix:
                continue
            sen = {**sen, "text": fix, "changed": True, "original": sen["text"], "reason": v.get("reason", "")}
        final.append({**sen, **checked[i], "text": sen["text"],
                      **({"changed": True, "original": sen["original"], "reason": sen["reason"]} if sen.get("changed") else {})})

    if changes and final:
        # Suggested repairs must themselves pass the checker. Never inherit the
        # verdict on the rejected original text.
        verdicts = gemini.ask_json(check_prompt(board_txt, final), temperature=0.1)
        final, check_issues = research_gate.checked_sentences(final, verdicts, board)
    check_issues += research_gate.structured_conflicts(final, facts)
    if not final:
        check_issues.append("No supported narration remains")
    # Owner (2026-10-19): a malformed checker reply gets one more try, then sentences that still fail
    # are dropped (kept in "dropped" for review) instead of stopping the whole breakdown.
    if final and any(x.startswith("Fact checker returned") for x in check_issues):
        control.check()
        verdicts = gemini.ask_json(check_prompt(board_txt, final), temperature=0.1)
        final, check_issues = research_gate.checked_sentences(final, verdicts, board)
        check_issues += research_gate.structured_conflicts(final, facts)
    final, dropped, check_issues = research_gate.drop_failing(final, check_issues)

    for i, sen in enumerate(final):
        sen["i"] = i + 1
    words = sum(len(x["text"].split()) for x in final)
    srcs = [x for x in board["sources"] if x.get("url") and "grounding-api-redirect" not in x["url"]]
    desc_facts = " ".join(x["text"] for x in final[:3])
    description = "\n".join([
        desc_facts, "",
        "Sources:", *[f"- {x.get('domain') or x['title']}: {x['url']}" for x in srcs[:8]],
        f"- TMDB: {facts.get('source')}", "",
        tmdb.ATTRIBUTION,
    ])
    result = {
        "sentences": final,
        "check_issues": check_issues,
        "check_scope": "storyboard_only",
        "changes": changes,
        "dropped": dropped,
        "word_count": words,
        "est_seconds": round(words / WPM * 60),
        "youtube_title": str(draft.get("youtube_title") or f"{facts['title']} Trailer Breakdown")[:100],
        "description": description,
        "tags": seo_tags(draft.get("tags") or [], facts),
        "storyboard": board,
        "model": settings.gemini_model,
    }
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.script = result
        p.plan = None  # sentences changed
        p.render = None
        s.commit()
    if check_issues:
        raise gemini.GeminiError("Research blocked: " + "; ".join(check_issues[:5]))
    return (f"{len(final)} sentences, {words} words (~{result['est_seconds'] // 60}m{result['est_seconds'] % 60:02d}s); "
            f"fact-check fixed {sum(1 for c in changes if c['fix'])}, dropped {sum(1 for c in changes if not c['fix'])}"
            + (f"; {len(dropped)} sentence(s) failed the source/credit checks and were left out — see the script" if dropped else ""))


@stage("verify")
def verify_saved(project_id: int) -> str:
    """Check owner edits in place; never redraft or replace their words."""
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        sc, facts = dict(p.script or {}), p.facts or {}
        board = storyboard(facts, p.research, p.shots, project_id)
    if not sc.get("sentences"):
        raise gemini.GeminiError("Write the script first")
    verdicts = gemini.ask_json(check_prompt(_board_text(board), sc["sentences"]), temperature=0.1)
    sc["sentences"], sc["check_issues"] = research_gate.checked_sentences(sc["sentences"], verdicts, board)
    sc["check_issues"] += research_gate.structured_conflicts(sc["sentences"], facts)
    sc["storyboard"], sc["check_scope"] = board, "storyboard_only"
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.script, p.plan, p.render = sc, None, None
        s.commit()
    research_gate.require_checked(sc,facts)
    return "Saved script checked against storyboard; independent source verification still required"
