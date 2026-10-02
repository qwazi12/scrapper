"""Stage: render — build the final 1080p video in the Screen Central style.

Look (from the reference video): letterboxed footage (2.4:1 band on black),
hard cuts every ~4 s, slow Ken Burns on stills, short motion clips, the
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
from .runner import project_dir, stage

W, H, FPS = 1920, 1080, 30
BAND = 800                      # letterboxed picture height
BAND_Y = (H - BAND) // 2
END_CARD = 8.0
# One timescale for every segment: the concat demuxer joins them without
# re-encoding, and mismatched timescales (zoompan vs trimmed clips) scramble
# the joined timestamps (seen 2026-10-01: a 33 s plan came out as 13 s).
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-video_track_timescale", "30000"]

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
    x, y, h = 60, H - BAND_Y - 120, 84
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
def seg_still(img: pathlib.Path, dur: float, k: int, dest: pathlib.Path, letterbox: bool = True) -> None:
    n = max(1, round(dur * FPS))
    bh = BAND if letterbox else H
    move = _MOVES[k % len(_MOVES)].format(n=n)
    vf = (f"scale={W * 2}:{bh * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{bh * 2},"
          f"zoompan={move}:d={n}:s={W}x{bh}:fps={FPS}" +
          (f",pad={W}:{H}:0:{BAND_Y}:black" if letterbox else "") + ",setsar=1")
    media.run(["ffmpeg", "-v", "error", "-y", "-i", str(img), "-vf", vf, "-frames:v", str(n), *ENC, str(dest)])


def seg_clip(src: str, start: float, dur: float, dest: pathlib.Path, crop: str | None = None) -> None:
    pre = f"crop={crop}," if crop else ""   # remove the source's own letterbox bars
    vf = (f"{pre}scale={W}:{BAND}:force_original_aspect_ratio=increase,crop={W}:{BAND},fps={FPS},"
          f"pad={W}:{H}:0:{BAND_Y}:black,setsar=1,tpad=stop_mode=clone:stop_duration={dur:.3f}")
    media.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", src, "-vf", vf, "-an",
               "-t", f"{dur:.3f}", *ENC, str(dest)])


def seg_image(img: pathlib.Path, dur: float, dest: pathlib.Path) -> None:
    """A full-frame image (poster card, end card) with a very slow push-in."""
    seg_still(img, dur, 0, dest, letterbox=False)


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

    poster_img = poster_frame(poster, backdrop, release_line(facts), out_dir / "poster_card.jpg")
    seg_files = []
    for k, item in enumerate(plan):
        control.check()
        control.progress(f"rendering segment {k + 1} of {len(plan)}")
        dest = segs_dir / f"seg_{k:03d}.mp4"
        dur = float(item["duration"])
        kind = item["kind"]
        sh = shots_by_id.get(item.get("shot") or "")
        if kind == "poster" or (kind in ("still", "card", "clip") and sh is None):
            seg_image(poster_img, dur, dest)
        elif kind == "clip":
            seg_clip(sh["file"], float(item.get("clip_start", sh["start"])), dur, dest, sh.get("crop"))
        else:
            seg_still(root / sh["still"], dur, k, dest)
        seg_files.append(dest)
    end_img = end_card(backdrop or poster, channel, out_dir / "end_card.jpg")
    end_seg = segs_dir / "seg_end.mp4"
    seg_image(end_img, END_CARD, end_seg)
    seg_files.append(end_seg)

    concat = out_dir / "segments.txt"
    concat.write_text("".join(f"file '{f}'\n" for f in seg_files))
    video = out_dir / "video.mp4"
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(video)])

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

    # Subscribe bar: ~20 s in, ~60 % through, and over the closing call to action.
    sub = subscribe_png(out_dir / "subscribe.png", channel)
    speech_end = float(plan[-1]["end"])
    windows = []
    if speech_end > 40:
        windows.append((18.0, 23.0))
    if speech_end > 90:
        windows.append((round(speech_end * 0.6, 2), round(speech_end * 0.6 + 5, 2)))
    if timeline:
        last = timeline[-1]
        windows.append((float(last["start"]), float(last["end"])))
    enable = "+".join(f"between(t,{a},{b})" for a, b in windows) or "0"

    final = out_dir / "final.mp4"
    # Loop the PNG so the overlay input lasts as long as the video. Do NOT add
    # setpts=PTS-STARTPTS here: on the joined video it cut a 33 s render to
    # 13.1 s (verified 2026-10-01).
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-loop", "1", "-i", str(sub)]
    if timeline:
        cmd += ["-i", str(narration)]
    cmd += ["-filter_complex",
            f"[0:v][1:v]overlay=0:0:shortest=1:enable='{enable}'[v]",
            "-map", "[v]"]
    if timeline:
        cmd += ["-map", "2:a", "-c:a", "copy"]
    cmd += [*ENC, "-movflags", "+faststart", "-t", f"{total:.3f}", str(final)]
    media.run(cmd)

    lead = (facts.get("cast") or [{}])[0].get("actor")
    best = next((sh for sh in shots if sh.get("usable") and sh.get("size") == "close" and lead in sh.get("people", [])),
                next((sh for sh in shots if sh.get("usable") and sh.get("people")), None))
    thumb = thumbnail(root / best["still"] if best else None, poster, facts.get("title", ""), out_dir / "thumbnail.jpg")

    secs = media.duration(final)
    info = {"file": str(final.relative_to(root)), "thumbnail": str(thumb.relative_to(root)),
            "seconds": round(secs, 2), "size": final.stat().st_size, "segments": len(seg_files),
            "rendered_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    shutil.rmtree(segs_dir, ignore_errors=True)  # intermediates; final.mp4 is what we keep
    video.unlink(missing_ok=True)
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.render = info
        s.commit()
    return f"{secs:.0f}s video, {round(info['size'] / 1e6)} MB, {len(seg_files)} segments"
