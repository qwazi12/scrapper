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
        for a, b in media.shots_from_cuts(media.scene_cuts(src["file"]), total):
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
    tagged = 0
    for i in range(0, len(shots), BATCH):
        control.check()
        batch = shots[i:i + BATCH]
        control.progress(f"tagging shots {i + 1}–{i + len(batch)} of {len(shots)}")
        try:
            tags = _tag(sheet, [root / sh["thumb"] for sh in batch])
        except gemini.GeminiError as exc:
            for sh in batch:
                sh["tag_error"] = str(exc)[:200]
            continue
        for sh, t in zip(batch, tags):
            known = {c["actor"] for c in facts.get("cast", [])}
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
            tagged += 1
    for sh in shots:
        sh["usable"] = bool(sh.get("description")) and not sh.get("card") and not sh.get("text") \
            and sh.get("quality") == "good"

    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.shots = shots
        p.plan = None  # shot ids changed
        s.commit()
    usable = sum(1 for sh in shots if sh["usable"])
    with_people = sum(1 for sh in shots if sh.get("people"))
    return (f"{len(shots)} shots from {len(sources)} video(s); {tagged} tagged, {usable} usable, "
            f"{with_people} with a named actor; {dropped_dark} black frames dropped")
