"""Stage: plan — voice every sentence, then pick a visual for every ~4 s.

Timeline rules (from the Screen Central example: a new picture every ~4 s):
- Each sentence is voiced on its own; its real audio length + a breath
  (0.35 s, 0.6 s at a paragraph break) is its slot on the timeline.
- A slot is split into ceil(len / 4.2 s) visuals.
- The first visual is the title card / studio logo from the trailer if one
  exists, else the poster; the release-date sentence shows the poster on blur.
- Every other visual is a trailer shot, picked by Gemini so the people named
  in the sentence are on screen, with no repeats until the pool runs out and
  roughly one moving clip for every two stills. If Gemini fails, shots are
  laid in trailer order (the narration follows the trailer, so it still fits).
"""

from __future__ import annotations

import math
import re

from ..db import SessionLocal
from ..models import StudioProject
from .. import control
from . import gemini, media, tts
from .runner import project_dir, stage

GAP, PARA_GAP = 0.35, 0.6
TARGET_VISUAL = 4.2
CLIP_MAX = 5.0

_DATE_WORDS = re.compile(r"\b(release|releases|premiere|premieres|arrives|hits|in theaters|streaming|"
                         r"january|february|march|april|may|june|july|august|september|october|"
                         r"november|december)\b", re.I)


def assign_prompt(slots: list[dict], catalog: list[dict]) -> str:
    lines = "\n".join(f"{s['slot']}: [sentence {s['sentence']}] {s['text']}" for s in slots)
    shots = "\n".join(f"{c['id']}: {c['description']} | people: {', '.join(c['people']) or '-'} | "
                      f"{c['setting']} | {c['mood']} | {c['size']}" for c in catalog)
    return f"""You are a video editor placing trailer shots under narration.
Each numbered slot below is one on-screen visual (about 4 seconds) under the given sentence.

Pick ONE shot id per slot:
- If the sentence names a character or actor, prefer shots showing that person.
- Match setting and mood to what the sentence describes.
- Do not reuse a shot until every shot has been used; avoid using the same shot twice in a row.
- Vary close / medium / wide.
- Mark about one slot in three as "clip" (plays as motion) — choose shots with action for clips;
  the rest are "still" (slow zoom on the frame).

Slots:
{lines}

Shots:
{shots}

Return a JSON array: [{{"slot": 1, "shot": "s012", "mode": "still"}}, ...] covering every slot."""


def _fallback(slots: list[dict], catalog: list[dict]) -> list[dict]:
    """Trailer order, cycling, every third a clip."""
    return [{"slot": s["slot"], "shot": catalog[i % len(catalog)]["id"],
             "mode": "clip" if i % 3 == 1 else "still"} for i, s in enumerate(slots)]


def _validate(picks: list, slots: list[dict], catalog: list[dict]) -> tuple[list[dict], int]:
    """Keep valid picks; repair missing/unknown/back-to-back ones in trailer order."""
    ids = [c["id"] for c in catalog]
    by_slot = {int(p.get("slot", 0)): p for p in picks if isinstance(p, dict)}
    out, used, repaired, prev = [], set(), 0, None
    for s in slots:
        p = by_slot.get(s["slot"], {})
        sid = p.get("shot")
        if sid not in ids or sid == prev:
            sid = next((i for i in ids if i not in used and i != prev), None) or \
                next(i for i in ids if i != prev) if len(ids) > 1 else ids[0]
            repaired += 1
        used.add(sid)
        out.append({"slot": s["slot"], "shot": sid, "mode": "clip" if p.get("mode") == "clip" else "still"})
        prev = sid
    return out, repaired


@stage("plan")
def plan(project_id: int) -> str:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        script, shots, facts = p.script, p.shots, p.facts
    if not script or not script.get("sentences"):
        raise RuntimeError("Run 'script' first")
    if not shots:
        raise RuntimeError("Run 'shots' first")
    catalog = [sh for sh in shots if sh.get("usable")]
    if len(catalog) < 5:
        raise RuntimeError(f"Only {len(catalog)} usable shots — add footage (upload or more trailers)")
    cards = [sh for sh in shots if sh.get("card")]
    root = project_dir(project_id)
    tts_dir = root / "tts"
    tts_dir.mkdir(exist_ok=True)

    # 1. Voice every sentence; its real length drives the timeline.
    t, timeline, prev_para = 0.0, [], None
    sentences = script["sentences"]
    for k, sen in enumerate(sentences):
        control.progress(f"voicing sentence {k + 1} of {len(sentences)}")
        mp3 = tts.synth(sen["text"], tts_dir / f"sent_{k + 1:03d}.mp3")
        d = media.duration(mp3)
        gap = PARA_GAP if (k + 1 < len(sentences) and sentences[k + 1]["paragraph"] != sen["paragraph"]) else GAP
        timeline.append({"sentence": k + 1, "text": sen["text"], "paragraph": sen["paragraph"],
                         "audio": str(mp3.relative_to(root)), "start": round(t, 3), "speech": round(d, 3),
                         "end": round(t + d + gap, 3)})
        t += d + gap
        prev_para = sen["paragraph"]

    # 2. Split each sentence slot into ~4 s visuals.
    slots: list[dict] = []
    date_sentence = next((x["sentence"] for x in timeline if x["paragraph"] == 2 and _DATE_WORDS.search(x["text"])),
                         None)
    for x in timeline:
        span = x["end"] - x["start"]
        n = max(1, math.ceil(span / TARGET_VISUAL))
        for j in range(n):
            slots.append({"slot": len(slots) + 1, "sentence": x["sentence"], "text": x["text"],
                          "start": round(x["start"] + span * j / n, 3),
                          "end": round(x["start"] + span * (j + 1) / n, 3)})

    special = {}
    special[1] = {"kind": "card", "shot": cards[0]["id"]} if cards else {"kind": "poster"}
    if date_sentence:
        first_date_slot = next(s["slot"] for s in slots if s["sentence"] == date_sentence)
        special[first_date_slot] = {"kind": "poster"}
    open_slots = [s for s in slots if s["slot"] not in special]

    # 3. Gemini picks shots; validate and repair.
    try:
        picks = gemini.ask_json(assign_prompt(open_slots, catalog), temperature=0.3)
        assigned, repaired = _validate(picks if isinstance(picks, list) else [], open_slots, catalog)
        how = f"Gemini picks ({repaired} repaired)"
    except gemini.GeminiError as exc:
        assigned, repaired = _validate(_fallback(open_slots, catalog), open_slots, catalog)
        how = f"trailer order (Gemini failed: {str(exc)[:80]})"
    by_slot = {a["slot"]: a for a in assigned}
    shots_by_id = {sh["id"]: sh for sh in shots}

    plan_items = []
    for s in slots:
        dur = round(s["end"] - s["start"], 3)
        if s["slot"] in special:
            sp = special[s["slot"]]
            item = {**s, "kind": sp["kind"], "shot": sp.get("shot"), "duration": dur}
        else:
            a = by_slot[s["slot"]]
            sh = shots_by_id[a["shot"]]
            item = {**s, "kind": a["mode"], "shot": a["shot"], "duration": dur,
                    "clip_start": sh["start"], "clip_len": round(min(dur, CLIP_MAX), 3)}
        plan_items.append(item)

    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.plan = plan_items
        sc = dict(p.script)
        sc["timeline"] = timeline
        sc["narration_seconds"] = round(t, 2)
        p.script = sc
        p.render = None
        s.commit()
    clips = sum(1 for i in plan_items if i["kind"] == "clip")
    distinct = len({i["shot"] for i in plan_items if i.get("shot")})
    return (f"{len(timeline)} sentences voiced ({t:.0f}s); {len(plan_items)} visuals, {clips} clips, "
            f"{distinct} distinct shots — {how}")
