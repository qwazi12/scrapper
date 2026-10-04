"""Stage: shots — cut every footage file into shots and tag each one.

For each shot: a full-res still (for Ken Burns stills) + a small thumbnail
(for the UI and for Gemini), and tags from Gemini vision: what happens, WHO is
on screen (matched against a labelled sheet of the TMDB cast photos), setting,
mood, shot size, and whether it is a logo/title/rating card or has burned-in
text (those are kept out of the rotation, except one title card for the intro).
"""

from __future__ import annotations

import pathlib

from ..db import SessionLocal
from ..models import StudioProject
from .. import control
from . import gemini, media
from .runner import project_dir, stage

MAX_SHOTS = 220
BATCH = 12          # frames per Gemini call
DARK = 14           # mean luma below this = black frame, dropped without asking
DUP_BITS = 5        # dHash bits apart: neighbouring frames this close share one tag result


DUP_COLOR = 12      # …and mean R/G/B within this: dHash alone ignores colour (flat red = flat blue)


def _signature(path: pathlib.Path) -> tuple[int, tuple[float, float, float]] | None:
    """(dHash, mean RGB) of a frame — both must match for two frames to be look-alikes."""
    from PIL import Image, ImageStat
    from .stage_plan import dhash
    h = dhash(path)
    if h is None:
        return None
    try:
        with Image.open(path) as im:
            mean = tuple(ImageStat.Stat(im.convert("RGB")).mean)
    except Exception:  # noqa: BLE001
        return None
    return h, mean


def auto_usable(sh: dict) -> bool:
    """Owner rule (2026-10-04): only title/logo/rating CARDS are kept out.
    Dark, blurry and burned-in-text shots are usable — a network watermark in
    the corner marked every MobLand shot as "text" and left 2 usable shots."""
    return bool(sh.get("description")) and not sh.get("card")


def recompute_usable(project_id: int) -> tuple[int, int]:
    """Re-apply auto_usable to a project's saved tags (no AI); the owner's own
    Use / Leave out choices stay. Returns (usable before, usable after)."""
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        shots = [dict(sh) for sh in (p.shots or [])]
        before = sum(1 for sh in shots if sh.get("usable"))
        for sh in shots:
            if not sh.get("owner_set"):
                sh["usable"] = auto_usable(sh)
        p.shots = shots
        s.commit()
    return before, sum(1 for sh in shots if sh.get("usable"))


def use_batch(project_id: int) -> bool:
    """Batch Mode (half price, async) only for automation runs — nobody waits."""
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        return bool(p and (p.review or {}).get("auto"))

TAG_PROMPT = """You are logging shots from official trailer footage for a video editor.
Image 1 is a labelled sheet of the cast (number, actor, character). The next {n} images
are shots, in order, numbered 1..{n}.

For EACH shot return an object with:
- "i": its number (1..{n})
- "description": max 20 words, start with the action ("Linda sprints across wet sand toward the wreck")
- "people": actor names from the cast sheet who are clearly visible (exact names; [] if none or unsure)
- "setting": a few words (e.g. "beach", "office", "plane cabin")
- "mood": one word (tense, funny, sad, calm, action, romantic, scary, epic)
- "size": "close" | "medium" | "wide"
- "card": true if it is a studio logo, title card, rating card, release-date card or mostly text
- "text": true if there is any burned-in text/caption on the picture
- "quality": "good" | "blurry" | "dark"
Only name people you can actually see. Do not invent plot.
Return a JSON array of {n} objects."""


def _tag(cast_sheet: pathlib.Path | None, thumbs: list[pathlib.Path]) -> list[dict]:
    images = ([cast_sheet] if cast_sheet else []) + thumbs
    out = gemini.ask_json(TAG_PROMPT.format(n=len(thumbs)), images=images, temperature=0.2)
    if not isinstance(out, list):
        raise gemini.GeminiError("shot tagging did not return a list")
    by_i = {int(o.get("i", 0)): o for o in out if isinstance(o, dict)}
    return [by_i.get(k + 1, {}) for k in range(len(thumbs))]


@stage("shots")
def shots(project_id: int) -> str:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        facts, trailer = p.facts or {}, p.trailer or {}
    sources = trailer.get("sources") or ([{"id": "trailer", "file": trailer["file"], "type": "Trailer"}]
                                         if trailer.get("file") else [])
    sources = [v for v in sources if v.get("file") and pathlib.Path(v["file"]).exists()]
    if not sources:
        raise RuntimeError("No trailer file yet — run 'trailer' (or upload one) first")

    root = project_dir(project_id)
    stills, thumbs = root / "stills", root / "thumbs"
    stills.mkdir(exist_ok=True)
    thumbs.mkdir(exist_ok=True)

    shots: list[dict] = []
    dropped_dark = 0
    for src in sources:
        total = media.duration(src["file"])
        crop = media.active_area(src["file"])  # trailers often have bars baked in
        cuts = media.scene_cuts(src["file"])   # adaptive threshold; raises if ffmpeg fails
        for a, b in media.shots_from_cuts(cuts, total):
            if len(shots) >= MAX_SHOTS:
                break
            control.check()
            control.progress(f"cutting {src.get('id')}: shot {len(shots) + 1}")
            sid = f"s{len(shots) + 1:03d}"
            mid = a + (b - a) / 2
            thumb = media.frame(src["file"], mid, thumbs / f"{sid}.jpg", width=480, crop=crop)
            if media.brightness(thumb) < DARK:
                thumb.unlink(missing_ok=True)
                dropped_dark += 1
                continue
            still = media.frame(src["file"], mid, stills / f"{sid}.jpg", crop=crop)
            shots.append({
                "id": sid, "source": src.get("id"), "source_type": src.get("type"),
                "file": src["file"], "start": a, "end": b, "seconds": round(b - a, 2), "crop": crop,
                "still": str(still.relative_to(root)), "thumb": str(thumb.relative_to(root)),
            })

    sheet = media.cast_sheet(facts.get("cast", [])[:8], (facts.get("local") or {}).get("cast", {}),
                             root / "assets" / "cast_sheet.jpg")
    # Near-identical neighbouring frames (same source, a few dHash bits apart)
    # are tagged once and share the answer — fewer images sent to Gemini.
    reps, dup_of, prev = [], {}, None
    for sh in shots:
        h = _signature(root / sh["thumb"])
        if prev and h and prev[1] and prev[0]["source"] == sh["source"] \
                and bin(h[0] ^ prev[1][0]).count("1") <= DUP_BITS \
                and max(abs(a - b) for a, b in zip(h[1], prev[1][1])) <= DUP_COLOR:
            dup_of[sh["id"]] = prev[0]["id"]
        else:
            reps.append(sh)
            prev = (sh, h)
    batches = [reps[i:i + BATCH] for i in range(0, len(reps), BATCH)]
    known = {c["actor"] for c in facts.get("cast", [])}

    def apply(batch_shots, tags):
        for sh, t in zip(batch_shots, tags):
            sh.update({
                "description": str(t.get("description") or "")[:200],
                "people": [x for x in (t.get("people") or []) if x in known],
                "setting": str(t.get("setting") or ""),
                "mood": str(t.get("mood") or ""),
                "size": t.get("size") if t.get("size") in ("close", "medium", "wide") else "medium",
                "card": bool(t.get("card")),
                "text": bool(t.get("text")),
                "quality": t.get("quality") if t.get("quality") in ("good", "blurry", "dark") else "good",
            })

    # Automation runs (nobody waiting) use Gemini Batch Mode: half price.
    answers: dict[str, list] = {}
    how = "standard"
    if use_batch(project_id) and batches:
        control.progress(f"tagging {len(reps)} shots in one Gemini batch (half price)")
        items = [{"key": str(n), "prompt": TAG_PROMPT.format(n=len(bt)),
                  "images": ([sheet] if sheet else []) + [root / sh["thumb"] for sh in bt], "temperature": 0.2}
                 for n, bt in enumerate(batches)]
        try:
            got = gemini.ask_json_batch(items)
            answers = {k: v for k, v in got.items() if isinstance(v, list)}
            how = f"batch ({len(answers)}/{len(batches)} groups at half price)"
        except gemini.BatchUnavailable as exc:
            how = f"standard (batch unavailable: {str(exc)[:80]})"
    tagged = 0
    for n, bt in enumerate(batches):
        control.check()
        if str(n) in answers:
            by_i = {int(o.get("i", 0)): o for o in answers[str(n)] if isinstance(o, dict)}
            tags = [by_i.get(k + 1, {}) for k in range(len(bt))]
        else:
            control.progress(f"tagging shots group {n + 1} of {len(batches)}")
            try:
                tags = _tag(sheet, [root / sh["thumb"] for sh in bt])
            except gemini.GeminiError as exc:
                for sh in bt:
                    sh["tag_error"] = str(exc)[:200]
                continue
        apply(bt, tags)
        tagged += len(bt)
    by_id = {sh["id"]: sh for sh in shots}
    for sid, rep in dup_of.items():
        src = by_id[rep]
        if src.get("description"):
            by_id[sid].update({k: src[k] for k in ("description", "people", "setting", "mood", "size",
                                                   "card", "text", "quality") if k in src})
            by_id[sid]["tags_from"] = rep
            tagged += 1
    for sh in shots:
        sh["usable"] = auto_usable(sh)

    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.shots = shots
        p.plan = None  # shot ids changed
        s.commit()
    usable = sum(1 for sh in shots if sh["usable"])
    with_people = sum(1 for sh in shots if sh.get("people"))
    return (f"{len(shots)} shots from {len(sources)} video(s); {tagged} tagged ({len(reps)} sent to Gemini, "
            f"{len(dup_of)} look-alikes reused; {how}), {usable} usable, "
            f"{with_people} with a named actor; {dropped_dark} black frames dropped")
