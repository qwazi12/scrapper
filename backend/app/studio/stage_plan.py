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

QA (added 2026-10-02 after "By Any Means" showed two different shots of the
same interview framing back to back): every still gets a perceptual
fingerprint; shots that look alike (Hamming distance <= LOOK_ALIKE) form one
"look" group. Gemini sees one shot per group, and _validate refuses a pick
whose look was used in the last NEAR_WINDOW slots, or used at all while
unused looks remain. Groups are saved on the shots (`look`) so the Studio can
flag repeats after manual swaps too.
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
LOOK_ALIKE = 10       # max differing bits (of 64) for two stills to count as the same picture
SAME_SETUP = 24       # looser match for the same set-up: same video, same people, within SETUP_SECONDS
SETUP_SECONDS = 12.0  # (By Any Means: one interview, 8 s apart, 20 bits apart — read as "the same shot")
NEAR_WINDOW = 6       # the same look never returns within this many slots
TARGET_VISUAL = 4.2
MAX_VISUAL = 7.0      # with few distinct shots, hold each visual longer (up to this) instead of repeating
CLIP_MAX = 5.0
# Rhythm (owner, 2026-10-04): a clip at 0:00; after every clip at least MIN_STILLS
# stills before the next clip (never two clips in a row); never more than
# MAX_STILLS stills in a row — the next visual is a clip.
MIN_STILLS, MAX_STILLS = 2, 3

_DATE_WORDS = re.compile(r"\b(release|releases|premiere|premieres|arrives|hits|in theaters|streaming|"
                         r"january|february|march|april|may|june|july|august|september|october|"
                         r"november|december)\b", re.I)


def dhash(path) -> int | None:
    """64-bit difference hash of an image: near-identical frames differ by a few bits."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            g = im.convert("L").resize((9, 8))
            px = list(g.getdata())
    except Exception:  # noqa: BLE001 — a missing/broken still just gets no fingerprint
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def look_groups(shots: list[dict], root) -> dict[str, str]:
    """shot id -> look group id (the first shot of that look, in trailer order).

    Two shots are the same look if their pictures are near-identical (any
    trailer: trailers reuse footage), or if they're the same set-up: same
    source video, within SETUP_SECONDS, the same people on screen, and a
    moderately similar picture."""
    ids = [sh["id"] for sh in shots]
    parent = {i: i for i in ids}

    def find(i: str) -> str:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:   # the earlier shot (trailer order) names the group
            first, second = (ra, rb) if ids.index(ra) < ids.index(rb) else (rb, ra)
            parent[second] = first

    hashes = {sh["id"]: (dhash(root / sh["still"]) if sh.get("still") else None) for sh in shots}
    for i, a in enumerate(shots):
        ha = hashes[a["id"]]
        if ha is None:
            continue
        for b in shots[i + 1:]:
            hb = hashes[b["id"]]
            if hb is None:
                continue
            bits = bin(ha ^ hb).count("1")
            same_setup = (a.get("source") and a.get("source") == b.get("source")
                          and abs(float(a.get("start", 0)) - float(b.get("start", 0))) <= SETUP_SECONDS
                          and a.get("people") and set(a["people"]) == set(b.get("people") or []))
            if bits <= LOOK_ALIKE or (same_setup and bits <= SAME_SETUP):
                union(a["id"], b["id"])
    return {i: find(i) for i in ids}


def plan_issues(items: list[dict], groups: dict[str, str]) -> list[dict]:
    """Repeats a viewer would notice: the same picture (or a look-alike) within
    NEAR_WINDOW slots, or again later while it's a repeat at all."""
    issues, seen = [], {}
    for idx, it in enumerate(items):
        sid = it.get("shot")
        if not sid or it.get("kind") == "poster":
            continue
        g = groups.get(sid, sid)
        if g in seen:
            gap = idx - seen[g]
            issues.append({"slot": it.get("slot"), "with_slot": items[seen[g]].get("slot"),
                           "kind": "back_to_back" if gap == 1 else ("near" if gap <= NEAR_WINDOW else "repeat")})
        seen[g] = idx
    return issues


def assign_prompt(slots: list[dict], catalog: list[dict]) -> str:
    lines = "\n".join(f"{s['slot']}: [sentence {s['sentence']}] {s['text']}" for s in slots)
    shots = "\n".join(f"{c['id']}: {c.get('description') or '-'} | people: {', '.join(c.get('people') or []) or '-'} | "
                      f"{c.get('setting') or '-'} | {c.get('mood') or '-'} | {c.get('size') or '-'}" for c in catalog)
    return f"""You are a video editor placing trailer shots under narration.
Each numbered slot below is one on-screen visual (about 4 seconds) under the given sentence.

Pick ONE shot id per slot:
- If the sentence names a character or actor, prefer shots showing that person.
- Match setting and mood to what the sentence describes.
- Do not reuse a shot until every shot has been used; avoid using the same shot twice in a row.
- Vary close / medium / wide.
- Never reuse a shot while unused shots remain.
- Set "mode" to "clip" when the shot has strong action that should play as motion, else "still".
  (The editor decides the final clip/still rhythm; your "clip" marks are preferences.)

Slots:
{lines}

Shots:
{shots}

Return a JSON array: [{{"slot": 1, "shot": "s012", "mode": "still"}}, ...] covering every slot."""


def apply_rhythm(slots: list[dict], fixed_still: set[int], prefer_clip: dict[int, bool]) -> dict[int, str]:
    """Slot number -> "clip" | "still", following the owner's rhythm:
    clip first; then MIN_STILLS..MAX_STILLS stills between clips. Inside that
    window a slot becomes a clip if Gemini preferred one there (action shot);
    after MAX_STILLS stills it is a clip regardless. Fixed stills (the release
    poster) count as stills; a clip is placed just before one when the poster
    would otherwise make the run too long."""
    modes: dict[int, str] = {}
    since = None                      # stills since the last clip (None = no clip yet)
    nums = [s["slot"] for s in slots]
    for k, n in enumerate(nums):
        if n in fixed_still:
            modes[n] = "still"
            since = (since or 0) + 1
            continue
        nxt_fixed = k + 1 < len(nums) and nums[k + 1] in fixed_still
        if since is None:
            clip = True                                   # the opening visual moves
        elif since < MIN_STILLS:
            clip = False
        elif since >= MAX_STILLS:
            clip = True
        else:                                             # MIN_STILLS <= since < MAX_STILLS
            clip = prefer_clip.get(n, False) or (nxt_fixed and since + 1 >= MAX_STILLS)
        modes[n] = "clip" if clip else "still"
        since = 0 if clip else since + 1
    return modes


def _fallback(slots: list[dict], catalog: list[dict]) -> list[dict]:
    """Trailer order, cycling, every third a clip."""
    return [{"slot": s["slot"], "shot": catalog[i % len(catalog)]["id"],
             "mode": "clip" if i % 3 == 1 else "still"} for i, s in enumerate(slots)]


def _validate(picks: list, slots: list[dict], catalog: list[dict],
              groups: dict[str, str] | None = None) -> tuple[list[dict], int]:
    """Keep good picks; repair the rest. A pick is refused if it's unknown, if its
    look was used in the last NEAR_WINDOW slots, or if its look was used at all
    while unused looks remain. The replacement prefers a shot of the same people
    as the refused pick (so the sentence still matches), then trailer order."""
    groups = groups or {c["id"]: c["id"] for c in catalog}
    ids = [c["id"] for c in catalog]
    people = {c["id"]: set(c.get("people") or []) for c in catalog}
    all_looks = {groups.get(i, i) for i in ids}
    by_slot = {int(p.get("slot", 0)): p for p in picks if isinstance(p, dict)}
    out, used, recent, repaired = [], set(), [], 0

    def ok(i: str, strict: bool) -> bool:
        g = groups.get(i, i)
        if g in recent[-NEAR_WINDOW:]:
            return False
        return not (strict and g in used)

    for s in slots:
        p = by_slot.get(s["slot"], {})
        sid = p.get("shot")
        fresh_left = len(all_looks - used) > 0
        if sid not in ids or not ok(sid, strict=fresh_left):
            want = people.get(sid, set())
            order = sorted(ids, key=lambda i: (not (want & people[i]), ids.index(i)))
            sid = (next((i for i in order if ok(i, strict=True)), None)
                   or next((i for i in order if ok(i, strict=False)), None)
                   or next((i for i in order if groups.get(i, i) != (recent[-1] if recent else None)), ids[0]))
            repaired += 1
        g = groups.get(sid, sid)
        used.add(g)
        recent.append(g)
        out.append({"slot": s["slot"], "shot": sid, "mode": "clip" if p.get("mode") == "clip" else "still"})
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
    root = project_dir(project_id)
    tts_dir = root / "tts"
    tts_dir.mkdir(exist_ok=True)

    # 1. Voice every sentence; its real length drives the timeline.
    t, timeline, prev_para = 0.0, [], None
    sentences = script["sentences"]
    for k, sen in enumerate(sentences):
        control.check()
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
    groups = look_groups(shots, root)
    looks = len({groups[c["id"]] for c in catalog})
    planned = sum(max(1, math.ceil((x["end"] - x["start"]) / TARGET_VISUAL)) for x in timeline)
    # Fewer distinct pictures than visuals: hold each one longer instead of repeating.
    scarce = looks < planned
    target = min(MAX_VISUAL, t / max(1, looks)) if scarce else TARGET_VISUAL
    for x in timeline:
        span = x["end"] - x["start"]
        # Scarce pictures: round (not ceil) so the visual count lands near the number of pictures.
        n = max(1, round(span / target)) if scarce else max(1, math.ceil(span / target))
        for j in range(n):
            slots.append({"slot": len(slots) + 1, "sentence": x["sentence"], "text": x["text"],
                          "start": round(x["start"] + span * j / n, 3),
                          "end": round(x["start"] + span * (j + 1) / n, 3)})

    special = {}          # slot 1 is a clip now (owner, 2026-10-04), no longer the title card / poster
    if date_sentence:
        first_date_slot = next(s["slot"] for s in slots if s["sentence"] == date_sentence)
        special[first_date_slot] = {"kind": "poster"}
    open_slots = [s for s in slots if s["slot"] not in special]

    # 3. Gemini picks shots (one per look-alike group); validate and repair.
    control.check()
    control.progress("checking shots for look-alikes")
    seen_looks: set[str] = set()
    distinct_catalog = []
    for c in catalog:
        if groups[c["id"]] not in seen_looks:
            seen_looks.add(groups[c["id"]])
            distinct_catalog.append(c)
    try:
        control.check()
        picks = gemini.ask_json(assign_prompt(open_slots, distinct_catalog), temperature=0.3)
        control.check()
        assigned, repaired = _validate(picks if isinstance(picks, list) else [], open_slots, catalog, groups)
        how = f"Gemini picks ({repaired} repaired)"
    except gemini.GeminiError as exc:
        assigned, repaired = _validate(_fallback(open_slots, distinct_catalog), open_slots, catalog, groups)
        how = f"trailer order (Gemini failed: {str(exc)[:80]})"
    by_slot = {a["slot"]: a for a in assigned}
    shots_by_id = {sh["id"]: sh for sh in shots}
    modes = apply_rhythm(slots, {n for n, sp in special.items() if sp["kind"] == "poster"},
                         {a["slot"]: a["mode"] == "clip" for a in assigned})

    plan_items = []
    for s in slots:
        dur = round(s["end"] - s["start"], 3)
        if s["slot"] in special:
            sp = special[s["slot"]]
            item = {**s, "kind": sp["kind"], "shot": sp.get("shot"), "duration": dur}
        else:
            a = by_slot[s["slot"]]
            sh = shots_by_id[a["shot"]]
            item = {**s, "kind": modes[s["slot"]], "shot": a["shot"], "duration": dur,
                    "clip_start": sh["start"], "clip_len": round(min(dur, CLIP_MAX), 3)}
        plan_items.append(item)

    issues = plan_issues(plan_items, groups)
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.shots = [{**sh, "look": groups.get(sh["id"], sh["id"])} for sh in (p.shots or [])]
        p.plan = plan_items
        sc = dict(p.script)
        sc["timeline"] = timeline
        sc["narration_seconds"] = round(t, 2)
        p.script = sc
        p.render = None
        s.commit()
    clips = sum(1 for i in plan_items if i["kind"] == "clip")
    distinct = len({i["shot"] for i in plan_items if i.get("shot")})
    visuals = sum(1 for i in plan_items if i.get("shot"))
    why = (f" — only {looks} distinct pictures for {visuals} visuals even at {target:.1f}s each; "
           "add footage for fewer" if issues else "")
    return (f"{len(timeline)} sentences voiced ({t:.0f}s); {len(plan_items)} visuals ({target:.1f}s each), "
            f"{clips} clips, {distinct} distinct shots — {how}; QA: {len(issues)} repeat(s) left "
            f"({sum(1 for i in issues if i['kind'] != 'repeat')} within {NEAR_WINDOW} slots){why}")
