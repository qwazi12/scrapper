"""Stage: render — build the final 1080p video in the Screen Central style.

Look: full-frame 16:9 1920x1080. Footage is never cropped to fit: a 16:9
source fills the frame; a wider "scope" source keeps its own black bars
(owner's call 2026-10-02 — the old forced 2.4:1 band cut ~26% off the top
and bottom of 16:9 trailers). Hard cuts every ~4 s, slow Ken Burns on stills, short motion clips, the
poster on a blurred copy of itself when the release date is spoken, a
"subscribe" lower-third a few times, and an 8 s end card for YouTube's
end-screen elements. Narration sentences are placed at their planned start
times and loudness-normalised to YouTube's -14 LUFS.
"""

from __future__ import annotations

import datetime
import pathlib
import shutil

from PIL import Image, ImageDraw, ImageFilter

from ..config import settings
from ..db import SessionLocal
from ..models import StudioProject
from .. import control
from . import media
from . import motion
from .runner import project_dir, stage

W, H, FPS = 1920, 1080, 30
# Fit the whole picture inside 16:9, centred, black where it doesn't reach (never crop).
FIT = (f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
       f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")
END_CARD = 8.0
# One timescale for every segment: the concat demuxer joins them without
# re-encoding, and mismatched timescales (zoompan vs trimmed clips) scramble
# the joined timestamps (seen 2026-10-01: a 33 s plan came out as 13 s).
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-video_track_timescale", "30000",
       "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
# Every segment ends in the same pixel format and colour tags. Stills made from
# JPEGs (poster, end card) otherwise come out full-range yuvj420p/bt470bg; that
# mid-stream change makes ffmpeg rebuild its filters and drop an overlay that
# starts there — the release card vanished from Send Help on 2026-10-02.
NORM = "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"

_MOVES = [
    "z='1+0.10*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",              # slow push in
    "z='1.10-0.10*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",           # slow pull out
    "z='1.08':x='(iw-iw/zoom)*on/{n}':y='ih/2-(ih/zoom/2)'",                    # pan left -> right
    "z='1.08':x='(iw-iw/zoom)*(1-on/{n})':y='ih/2-(ih/zoom/2)'",                # pan right -> left
]


def _human_date(iso: str) -> str:
    try:
        return datetime.date.fromisoformat(iso[:10]).strftime("%B %-d, %Y").upper()
    except ValueError:
        return iso


def release_line(facts: dict) -> str:
    rel = next((r for r in facts.get("releases", []) if r.get("type") in ("Theatrical", "Digital", "Next episode")),
               None) or next(iter(facts.get("releases", [])), None)
    date = (rel or {}).get("date") or facts.get("primary_date") or ""
    if not date:
        return ""
    where = {"Theatrical": "IN THEATERS", "Digital": "STREAMING", "Next episode": "NEW EPISODE"}.get(
        (rel or {}).get("type", ""), "RELEASE DATE")
    if facts.get("networks"):
        where = f"ON {facts['networks'][0].upper()}"
    return f"{where} · {_human_date(date)}"


# --- overlay images (Pillow) -------------------------------------------------
def _text_w(draw: ImageDraw.ImageDraw, text: str, f) -> int:
    l, _, r, _ = draw.textbbox((0, 0), text, font=f)
    return r - l


def subscribe_png(dest: pathlib.Path, channel: str) -> pathlib.Path:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x, y, h = 60, H - 260, 84
    name_f, small_f, btn_f = media.font(34), media.font(22, False), media.font(26)
    tagline = "Trailer breakdowns & release dates"
    btn_w = _text_w(d, "SUBSCRIBE", btn_f) + 44
    w = max(_text_w(d, channel, name_f), _text_w(d, tagline, small_f)) + 86 + 30 + btn_w + 20
    d.rounded_rectangle((x, y, x + w, y + h), radius=18, fill=(15, 15, 15, 225))
    d.ellipse((x + 14, y + 14, x + 70, y + 70), fill=(229, 9, 20, 255))
    d.text((x + 31, y + 22), channel[:1].upper(), font=media.font(30), fill="white")
    d.text((x + 86, y + 12), channel, font=name_f, fill="white")
    d.text((x + 86, y + 52), tagline, font=small_f, fill=(200, 200, 200))
    bx = x + w - btn_w - 20
    d.rounded_rectangle((bx, y + 20, bx + btn_w, y + 64), radius=22, fill=(229, 9, 20, 255))
    d.text((bx + 22, y + 28), "SUBSCRIBE", font=btn_f, fill="white")
    img.save(dest)
    return dest


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    r = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1))
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def poster_frame(poster: pathlib.Path | None, backdrop: pathlib.Path | None, line: str,
                 dest: pathlib.Path) -> pathlib.Path:
    """Poster centred over a blurred, darkened copy of itself + release banner."""
    src = poster or backdrop
    canvas = Image.new("RGB", (W, H), "black")
    if src:
        with Image.open(src) as im:
            im = im.convert("RGB")
            bg = _cover(im, W, H).filter(ImageFilter.GaussianBlur(28)).point(lambda v: int(v * 0.55))
            canvas.paste(bg, (0, 0))
            ph = 900 if poster else 700
            fg = im.resize((int(im.width * ph / im.height), ph))
            canvas.paste(fg, ((W - fg.width) // 2, (H - ph) // 2 - (20 if line else 0)))
    if line:
        d = ImageDraw.Draw(canvas, "RGBA")
        f = media.font(40)
        tw = _text_w(d, line, f)
        bx, by = (W - tw) // 2 - 30, H - 120
        d.rounded_rectangle((bx, by, bx + tw + 60, by + 70), radius=12, fill=(229, 9, 20, 235))
        d.text((bx + 30, by + 12), line, font=f, fill="white")
    canvas.save(dest, quality=92)
    return dest


def banner_png(line: str, dest: pathlib.Path) -> pathlib.Path:
    """poster_frame's red release banner alone, on a transparent canvas."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = media.font(40)
    tw = _text_w(d, line, f)
    bx, by = (W - tw) // 2 - 30, H - 120
    d.rounded_rectangle((bx, by, bx + tw + 60, by + 70), radius=12, fill=(229, 9, 20, 235))
    d.text((bx + 30, by + 12), line, font=f, fill="white")
    img.save(dest)
    return dest


def end_card(backdrop: pathlib.Path | None, channel: str, dest: pathlib.Path) -> pathlib.Path:
    canvas = Image.new("RGB", (W, H), (10, 10, 10))
    if backdrop:
        with Image.open(backdrop) as im:
            canvas.paste(_cover(im.convert("RGB"), W, H).filter(ImageFilter.GaussianBlur(24))
                         .point(lambda v: int(v * 0.4)), (0, 0))
    d = ImageDraw.Draw(canvas)
    for text, f, y in (("THANKS FOR WATCHING", media.font(64), 360),
                       (f"Subscribe to {channel} for more trailer breakdowns", media.font(36, False), 460)):
        d.text(((W - _text_w(d, text, f)) // 2, y), text, font=f, fill="white")
    canvas.save(dest, quality=92)
    return dest


def thumbnail(still: pathlib.Path | None, poster: pathlib.Path | None, title: str, dest: pathlib.Path) -> pathlib.Path:
    src = still or poster
    canvas = Image.new("RGB", (1280, 720), "black")
    if src:
        with Image.open(src) as im:
            canvas.paste(_cover(im.convert("RGB"), 1280, 720), (0, 0))
    d = ImageDraw.Draw(canvas, "RGBA")
    d.rectangle((0, 470, 1280, 720), fill=(0, 0, 0, 170))
    f = media.font(86)
    words, lines, cur = title.upper().split(), [], ""
    for w in words:
        if _text_w(d, (cur + " " + w).strip(), f) > 1180 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    y = 490
    for ln in lines[:2]:
        d.text((50, y), ln, font=f, fill="white")
        y += 96
    d.rounded_rectangle((50, 410, 470, 466), radius=10, fill=(229, 9, 20, 240))
    d.text((66, 418), "TRAILER BREAKDOWN", font=media.font(34), fill="white")
    canvas.save(dest, quality=92)
    return dest


# --- segments (ffmpeg) -------------------------------------------------------
def seg_still(img: pathlib.Path, dur: float, k: int, dest: pathlib.Path) -> None:
    """A still with a slow Ken Burns move. The whole still is fitted into 16:9
    first (2x size so the move stays sharp); only the move itself zooms (≤10%)."""
    n = max(1, round(dur * FPS))
    move = _MOVES[k % len(_MOVES)].format(n=n)
    vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
          f"pad={W * 2}:{H * 2}:(ow-iw)/2:(oh-ih)/2:black,"
          f"zoompan={move}:d={n}:s={W}x{H}:fps={FPS},{NORM},setsar=1")
    media.run(["ffmpeg", "-v", "error", "-y", "-i", str(img), "-vf", vf, "-frames:v", str(n), *ENC, str(dest)])


def seg_clip(src: str, start: float, dur: float, dest: pathlib.Path, crop: str | None = None) -> None:
    pre = f"crop={crop}," if crop else ""   # remove the source's own letterbox bars
    vf = f"{pre}{FIT},fps={FPS},{NORM},setsar=1,tpad=stop_mode=clone:stop_duration={dur:.3f}"
    media.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", src, "-vf", vf, "-an",
               "-t", f"{dur:.3f}", *ENC, str(dest)])


def seg_image(img: pathlib.Path, dur: float, dest: pathlib.Path) -> None:
    """A full-frame image (poster card, end card) with a very slow push-in."""
    seg_still(img, dur, 0, dest)


@stage("render")
def render(project_id: int) -> str:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        facts, script, plan, shots = p.facts or {}, p.script or {}, p.plan, p.shots or []
    if not plan:
        raise RuntimeError("Run 'plan' first")
    root = project_dir(project_id)
    out_dir = root / "render"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    segs_dir = out_dir / "segments"
    segs_dir.mkdir(parents=True)
    channel = settings.studio_channel_name
    shots_by_id = {sh["id"]: sh for sh in shots}
    local = facts.get("local") or {}
    poster = pathlib.Path(local["poster"]) if local.get("poster") else None
    backdrop = pathlib.Path(local["backdrops"][0]) if local.get("backdrops") else None

    rel_line = release_line(facts)
    poster_img = poster_frame(poster, backdrop, rel_line, out_dir / "poster_card.jpg")
    mode = motion_mode()
    want_motion = mode in ("on", "compare")
    clean_poster = poster_frame(poster, backdrop, "", out_dir / "poster_clean.jpg") if want_motion else None
    seg_files, motion_segs, poster_slots = [], [], []
    for k, item in enumerate(plan):
        control.check()
        control.progress(f"rendering segment {k + 1} of {len(plan)}")
        dest = segs_dir / f"seg_{k:03d}.mp4"
        dur = float(item["duration"])
        kind = item["kind"]
        sh = shots_by_id.get(item.get("shot") or "")
        if kind == "poster" or (kind in ("still", "card", "clip") and sh is None):
            seg_image(poster_img, dur, dest)
            if want_motion:   # the motion version animates the release card over a clean poster
                clean = segs_dir / f"seg_{k:03d}_clean.mp4"
                seg_image(clean_poster, dur, clean)
                motion_segs.append(clean)
            poster_slots.append((float(item["start"]), dur))
            seg_files.append(dest)
            continue
        elif kind == "clip":
            seg_clip(sh["file"], float(item.get("clip_start", sh["start"])), dur, dest, sh.get("crop"))
        else:
            seg_still(root / sh["still"], dur, k, dest)
        seg_files.append(dest)
        if want_motion:
            motion_segs.append(dest)
    end_img = end_card(backdrop or poster, channel, out_dir / "end_card.jpg")
    end_seg = segs_dir / "seg_end.mp4"
    seg_image(end_img, END_CARD, end_seg)
    seg_files.append(end_seg)

    video = _concat(seg_files, out_dir / "segments.txt", out_dir / "video.mp4")

    # Narration: every sentence at its planned start; pad through the end card.
    timeline = script.get("timeline") or []
    total = float(plan[-1]["end"]) + END_CARD
    narration = out_dir / "narration.m4a"
    if timeline:
        ins, chains = [], []
        for i, x in enumerate(timeline):
            ins += ["-i", str(root / x["audio"])]
            ms = int(float(x["start"]) * 1000)
            chains.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms}[a{i}]")
        mix = "".join(f"[a{i}]" for i in range(len(timeline)))
        fc = ";".join(chains) + f";{mix}amix=inputs={len(timeline)}:normalize=0,apad=whole_dur={total:.3f}," \
                                f"loudnorm=I=-14:TP=-1.5:LRA=11[out]"
        media.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", "[out]",
                   "-t", f"{total:.3f}", "-c:a", "aac", "-b:a", "192k", str(narration)])
    audio = narration if timeline else None

    # Subscribe bar: ~20 s in, ~60 % through, and over the closing call to action.
    speech_end = float(plan[-1]["end"])
    windows = []
    if speech_end > 40:
        windows.append((18.0, 23.0))
    if speech_end > 90:
        windows.append((round(speech_end * 0.6, 2), round(speech_end * 0.6 + 5, 2)))
    # Keep the bar off the intro and the release card: a mid-video window moves
    # to the next free moment (the closing call to action stays where it is).
    first = next((it for it in plan if it.get("kind") in ("clip", "still") and shots_by_id.get(it.get("shot") or "")), None)
    taken = [(s0, s0 + d) for s0, d in poster_slots] + ([(float(first["start"]), float(first["start"]) + 3.8)] if first else [])
    windows = [w for w in (_free_window(a, b - a, taken, speech_end) for a, b in windows) if w]
    if timeline:
        last = timeline[-1]
        windows.append((float(last["start"]), max(float(last["end"]), float(last["start"]) + 4.0)))

    static_final = out_dir / ("final.mp4" if mode != "on" else "final_static.mp4")
    sub = subscribe_png(out_dir / "subscribe.png", channel)
    sub_movs = [(_png_overlay(sub, b - a, out_dir / f"subscribe_{i}.mov"), a) for i, (a, b) in enumerate(windows)]
    if mode != "on":
        control.progress("compositing the video")
        media.run(motion.overlay_cmd(video, sub_movs, audio, total, ENC, static_final))

    motion_info = None
    if want_motion:
        control.progress("rendering motion graphics (HyperFrames)")
        motion_info, overlays, end_motion = _motion_pieces(facts, plan, shots_by_id, poster_slots, windows,
                                                            rel_line, backdrop or poster, channel, sub_movs, out_dir)
        if end_motion:
            end_seg_m = segs_dir / "seg_end_motion.mp4"
            media.run(["ffmpeg", "-v", "error", "-y", "-i", str(end_motion), "-t", f"{END_CARD:.3f}", "-an",
                       "-vf", f"{NORM},setsar=1", *ENC, str(end_seg_m)])
            motion_segs.append(end_seg_m)
        else:
            motion_segs.append(end_seg)
        base_m = _concat(motion_segs, out_dir / "segments_motion.txt", out_dir / "video_motion.mp4")
        motion_final = out_dir / ("final.mp4" if mode == "on" else "final_motion.mp4")
        control.progress("compositing the motion version")
        media.run(motion.overlay_cmd(base_m, sorted(overlays, key=lambda x: x[1]), audio, total, ENC, motion_final))
        motion_info["file"] = str(motion_final.relative_to(root))
        motion_info["overlays"] = [{"piece": f.stem.split("-")[0], "start": round(t, 2),
                                    "seconds": round(media.duration(f), 2)} for f, t in sorted(overlays, key=lambda x: x[1])]
        base_m.unlink(missing_ok=True)
    final = out_dir / "final.mp4"

    lead = (facts.get("cast") or [{}])[0].get("actor")
    best = next((sh for sh in shots if sh.get("usable") and sh.get("size") == "close" and lead in sh.get("people", [])),
                next((sh for sh in shots if sh.get("usable") and sh.get("people")), None))
    thumb = thumbnail(root / best["still"] if best else None, poster, facts.get("title", ""), out_dir / "thumbnail.jpg")

    secs = media.duration(final)
    info = {"file": str(final.relative_to(root)), "thumbnail": str(thumb.relative_to(root)),
            "seconds": round(secs, 2), "size": final.stat().st_size, "segments": len(seg_files),
            "rendered_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "motion_mode": mode, "motion": motion_info}
    if mode == "on":
        static_final.unlink(missing_ok=True)
    shutil.rmtree(segs_dir, ignore_errors=True)  # intermediates; final.mp4 is what we keep
    video.unlink(missing_ok=True)
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.render = info
        s.commit()
    note = ""
    if motion_info:
        note = f", motion graphics: {motion_info['pieces']} pieces" + (
            f" ({len(motion_info['failures'])} fell back to static)" if motion_info["failures"] else "")
    return f"{secs:.0f}s video, {round(info['size'] / 1e6)} MB, {len(seg_files)} segments{note}"


# --- helpers -------------------------------------------------------------------------
def motion_mode() -> str:
    """off | compare | on — app_settings "studio_motion". Default "compare" until
    the owner has watched both versions and picked one (plan step 6)."""
    from ..models import AppSetting
    with SessionLocal() as s:
        row = s.get(AppSetting, "studio_motion")
        v = (row.value or {}) if row else {}
    mode = v.get("mode", "compare")
    return mode if mode in ("off", "compare", "on") else "compare"


def motion_cast_cards() -> int:
    from ..models import AppSetting
    with SessionLocal() as s:
        row = s.get(AppSetting, "studio_motion")
        n = ((row.value or {}) if row else {}).get("cast_cards", 2)
    return max(0, min(4, int(n)))


def _free_window(start: float, dur: float, taken: list[tuple[float, float]], limit: float) -> tuple[float, float] | None:
    """The first window of `dur` seconds at or after `start` that overlaps none of
    `taken` and ends before `limit` (the closing line); None if there is none."""
    for _ in range(len(taken) + 1):
        clash = [b for a, b in taken if a < start + dur and start < b]
        if not clash:
            return (round(start, 2), round(start + dur, 2)) if start + dur <= limit else None
        start = max(clash) + 0.5
    return None


def _concat(files: list[pathlib.Path], listfile: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    listfile.write_text("".join(f"file '{f}'\n" for f in files))
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(dest)])
    return dest


def _png_overlay(png: pathlib.Path, dur: float, dest: pathlib.Path) -> pathlib.Path:
    """A still transparent PNG as a short alpha .mov, so static and motion
    overlays go through the same compositing path."""
    media.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{dur:.3f}", "-i", str(png), "-r", str(FPS),
               "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", str(dest)])
    return dest


def _motion_pieces(facts, plan, shots_by_id, poster_slots, windows, rel_line, backdrop, channel, sub_movs, out_dir):
    """Render every motion piece; each failure falls back to its static version
    (release card → the banner baked into the poster is gone, so the static
    banner PNG is used; subscribe → the static bar; end screen → static card;
    intro and cast cards have no static version and are simply left out)."""
    failures: list[str] = []
    overlays: list[tuple[pathlib.Path, float]] = []
    busy: list[tuple[float, float]] = []
    pieces = 0

    first = next((it for it in plan if it.get("kind") in ("clip", "still") and shots_by_id.get(it.get("shot") or "")), None)
    if first:
        d = 3.8
        f = motion.try_piece("intro", {"title": facts.get("title", ""), "meta": motion.intro_meta(facts), "duration": d},
                             None, failures)
        if f:
            start = float(first["start"])
            overlays.append((f, start)); busy.append((start, start + d)); pieces += 1

    where, _, date = rel_line.partition(" · ")
    for start, dur in poster_slots:
        if not rel_line:
            break
        d = round(min(dur, 6.0), 2)
        f = motion.try_piece("release", {"where": where, "date": date, "duration": d}, None, failures)
        if f:
            pieces += 1
        else:  # the motion version's poster has no baked banner: lay the static one on instead
            f = _png_overlay(banner_png(rel_line, out_dir / "banner.png"), dur, out_dir / f"banner_{len(overlays)}.mov")
        overlays.append((f, start))
        busy.append((start, start + d))

    for i, (a, b) in enumerate(windows):
        d = round(b - a, 2)
        f = motion.try_piece("subscribe", {"channel": channel, "duration": d}, None, failures)
        if f:
            pieces += 1
        overlays.append((f or sub_movs[i][0], a))
        busy.append((a, b))

    cast = motion.cast_cards(plan, shots_by_id, facts.get("cast", []), motion_cast_cards(), busy)
    for c in cast:
        f = motion.try_piece("cast", {"actor": c["actor"], "character": c["character"], "duration": c["duration"]},
                             None, failures)
        if f:
            overlays.append((f, c["start"])); pieces += 1

    end_motion = motion.try_piece("endscreen", {"channel": channel, "duration": END_CARD},
                                  {"backdrop.jpg": backdrop} if backdrop else None, failures)
    if end_motion:
        pieces += 1
    ok, why = motion.available()
    return ({"pieces": pieces, "failures": failures[:6], "cast_cards": [c["actor"] for c in cast],
             "available": ok, "reason": why or None}, overlays, end_motion)
