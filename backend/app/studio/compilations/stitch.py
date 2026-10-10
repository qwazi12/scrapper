"""Master stitching engine for Top 5 & Top 10 Countdown Compilations.

Assembles 8–12 minute master YouTube videos from Studio project breakdowns,
generating countdown transition bumpers (#10 down to #1), cinematic Intro/Outro bookends,
YouTube chapter timestamps, -14 LUFS loudness normalization, and 3 thumbnail options.
"""

from __future__ import annotations

import datetime
import math
import os
import pathlib
import shutil
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from ...config import settings
from ...db import SessionLocal
from ...models import QueueItem, StudioProject
from .. import media
from .. import runner
from .. import tmdb
from .. import tts
from . import bumper

W, H, FPS = 1920, 1080, 30


def resolve_project_render(project_id: int, render_dict: dict | None) -> pathlib.Path | None:
    if not render_dict:
        return None
    raw = render_dict.get("file")
    if not raw:
        return None
    p_cand = pathlib.Path(raw)
    if p_cand.is_absolute() and p_cand.exists():
        return p_cand
    p_dir = runner.project_dir(project_id)
    cand2 = p_dir / raw
    if cand2.exists():
        return cand2
    cand3 = p_dir / "render" / "final.mp4"
    if cand3.exists():
        return cand3
    return None


ENC = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-video_track_timescale", "30000",
    "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"
]
NORM = "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"


def _format_time(seconds: float) -> str:
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"


def compilations_dir() -> pathlib.Path:
    d = settings.data_path / "studio" / "compilations"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_intro_clip(
    projects: list[StudioProject],
    title: str,
    dest_mp4: pathlib.Path,
    custom_text: str = "",
) -> tuple[pathlib.Path, float]:
    """Generate an energetic 15–22s Intro teaser clip with voiceover."""
    count = len(projects)
    script_text = custom_text or (
        f"Welcome back to Screen Central! Today, we're breaking down the top {count} "
        f"must-watch movies and shows. Every single entry on this list delivers an unforgettable experience. "
        f"Make sure to stay tuned for our number one pick, because it completely steals the show. "
        f"Let's dive right in!"
    )

    work_dir = dest_mp4.parent / "_intro_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    voice_mp3 = work_dir / "intro_voice.mp3"

    try:
        tts.synth(script_text, voice_mp3)
        voice_dur = media.duration(voice_mp3)
    except Exception:
        # Fallback silent track if TTS unavailable
        voice_dur = 14.0
        media.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={voice_dur:.2f}",
            "-c:a", "libmp3lame", str(voice_mp3)
        ])

    intro_dur = max(voice_dur + 1.2, 12.0)

    # Pick top backdrops or posters for the intro montage
    slides = []
    for p in reversed(projects):
        facts = p.facts or {}
        local = facts.get("local") or {}
        if local.get("backdrops"):
            slides.append(pathlib.Path(local["backdrops"][0]))
        elif local.get("poster"):
            slides.append(pathlib.Path(local["poster"]))

    if not slides:
        # Emergency slate
        slate = work_dir / "slate.jpg"
        Image.new("RGB", (W, H), (15, 15, 20)).save(slate)
        slides = [slate]

    slide_dur = intro_dur / len(slides)
    slide_clips = []
    for idx, slide_path in enumerate(slides):
        sub_dest = work_dir / f"slide_{idx:02d}.mp4"
        n_frames = max(1, round(slide_dur * FPS))
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},zoompan=z='1+0.05*on/{n_frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS},{NORM},setsar=1"
        media.run([
            "ffmpeg", "-v", "error", "-y",
            "-loop", "1", "-i", str(slide_path),
            "-vf", vf,
            "-t", f"{slide_dur:.3f}",
            "-an",
            *ENC,
            str(sub_dest)
        ])
        slide_clips.append(sub_dest)

    # Concat slides
    concat_list = work_dir / "intro_slides.txt"
    concat_list.write_text("".join(f"file '{f}'\n" for f in slide_clips))
    video_track = work_dir / "intro_video_raw.mp4"
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(video_track)])

    # Add text overlay and voiceover
    title_upper = title.upper()
    media.run([
        "ffmpeg", "-v", "error", "-y",
        "-i", str(video_track),
        "-i", str(voice_mp3),
        "-filter_complex",
        f"[0:v]drawtext=text='COUNTDOWN':fontcolor=white:fontsize=44:box=1:boxcolor=red@0.85:boxborderw=14:x=(w-text_w)/2:y=180,"
        f"drawtext=text='{title_upper}':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=270:shadowcolor=black:shadowx=3:shadowy=3[v];"
        f"[1:a]apad=pad_dur=1.0[a]",
        "-map", "[v]", "-map", "[a]",
        "-t", f"{intro_dur:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        *ENC,
        str(dest_mp4)
    ])

    shutil.rmtree(work_dir, ignore_errors=True)
    return dest_mp4, intro_dur


def create_outro_clip(
    channel_name: str,
    dest_mp4: pathlib.Path,
    custom_text: str = "",
    backdrop: pathlib.Path | None = None,
) -> tuple[pathlib.Path, float]:
    """Generate a clean 16–20s Outro discussion prompt with subscribe CTA."""
    script_text = custom_text or (
        f"That wraps up our top countdown! Which of these titles are you watching first? "
        f"Let us know down in the comments below. And if you enjoyed this list, make sure to hit "
        f"that subscribe button and ring the bell so you never miss our next breakdown. "
        f"Thanks for watching Screen Central!"
    )

    work_dir = dest_mp4.parent / "_outro_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    voice_mp3 = work_dir / "outro_voice.mp3"

    try:
        tts.synth(script_text, voice_mp3)
        voice_dur = media.duration(voice_mp3)
    except Exception:
        voice_dur = 14.0
        media.run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={voice_dur:.2f}",
            "-c:a", "libmp3lame", str(voice_mp3)
        ])

    outro_dur = max(voice_dur + 1.5, 14.0)
    card_jpg = work_dir / "outro_card.jpg"

    # Pillow end card
    canvas = Image.new("RGB", (W, H), (12, 12, 16))
    if backdrop and backdrop.exists():
        try:
            with Image.open(backdrop) as im:
                bg = im.convert("RGB").resize((W, H))
                bg = bg.filter(ImageFilter.GaussianBlur(28)).point(lambda v: int(v * 0.35))
                canvas.paste(bg, (0, 0))
        except Exception:
            pass

    draw = ImageDraw.Draw(canvas)
    f_big = media.font(64)
    f_sub = media.font(36, False)
    f_cta = media.font(32)

    t1 = "THANKS FOR WATCHING"
    t2 = "Which movie or show was your favorite?"
    t3 = f"SUBSCRIBE TO {channel_name.upper()} FOR MORE"

    w1 = _text_w(draw, t1, f_big)
    w2 = _text_w(draw, t2, f_sub)
    w3 = _text_w(draw, t3, f_cta)

    draw.text(((W - w1) // 2, 340), t1, font=f_big, fill="white")
    draw.text(((W - w2) // 2, 440), t2, font=f_sub, fill=(220, 220, 220))
    # Red CTA Box
    bx = (W - w3) // 2 - 24
    by = 540
    draw.rounded_rectangle((bx, by, bx + w3 + 48, by + 68), radius=16, fill=(229, 9, 20))
    draw.text(((W - w3) // 2, by + 14), t3, font=f_cta, fill="white")

    canvas.save(card_jpg, quality=95)

    n_frames = max(1, round(outro_dur * FPS))
    vf = f"zoompan=z='1+0.03*on/{n_frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS},{NORM},setsar=1"

    media.run([
        "ffmpeg", "-v", "error", "-y",
        "-loop", "1", "-i", str(card_jpg),
        "-i", str(voice_mp3),
        "-vf", vf,
        "-t", f"{outro_dur:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        *ENC,
        str(dest_mp4)
    ])

    shutil.rmtree(work_dir, ignore_errors=True)
    return dest_mp4, outro_dur


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    l, _, r, _ = draw.textbbox((0, 0), text, font=font)
    return r - l


def generate_compilation_thumbnails(
    projects: list[StudioProject],
    title: str,
    out_dir: pathlib.Path,
) -> list[pathlib.Path]:
    """Generate 3 clickable 1280x720 JPEG thumbnail options."""
    out_dir.mkdir(parents=True, exist_ok=True)
    thumbs = []

    # Option 1: Poster Collage
    thumb1 = out_dir / "thumb_option_1_poster.jpg"
    canvas1 = Image.new("RGB", (1280, 720), (10, 10, 15))
    draw1 = ImageDraw.Draw(canvas1)

    # Paste up to 3 posters side-by-side with rotation/depth
    valid_posters = []
    for p in reversed(projects):
        local = (p.facts or {}).get("local") or {}
        if local.get("poster") and pathlib.Path(local["poster"]).exists():
            valid_posters.append(pathlib.Path(local["poster"]))

    if valid_posters:
        # Ambient blurred backdrop from #1
        try:
            with Image.open(valid_posters[0]) as im:
                bg = im.convert("RGB").resize((1280, 720)).filter(ImageFilter.GaussianBlur(32)).point(lambda v: int(v * 0.4))
                canvas1.paste(bg, (0, 0))
        except Exception:
            pass

        # Draw posters
        ph = 520
        pw = 350
        start_x = (1280 - (len(valid_posters[:3]) * 360)) // 2
        for i, pos_path in enumerate(valid_posters[:3]):
            try:
                with Image.open(pos_path) as im:
                    fg = im.convert("RGB").resize((pw, ph))
                    canvas1.paste(fg, (start_x + i * 360, 140))
            except Exception:
                pass

    # Bold Top Badge
    draw1.rectangle((0, 0, 1280, 110), fill=(10, 10, 15, 230))
    t1_f = media.font(58)
    t1_text = title.upper()
    if _text_w(draw1, t1_text, t1_f) > 1200:
        t1_f = media.font(42)
    tw1 = _text_w(draw1, t1_text, t1_f)
    draw1.text(((1280 - tw1) // 2, 25), t1_text, font=t1_f, fill="white")
    canvas1.save(thumb1, quality=95)
    thumbs.append(thumb1)

    # Option 2: High-Tension Action Still with Red #1 Badge
    thumb2 = out_dir / "thumb_option_2_action.jpg"
    canvas2 = Image.new("RGB", (1280, 720), (5, 5, 10))
    draw2 = ImageDraw.Draw(canvas2)

    top_project = projects[-1] if projects else None
    if top_project:
        local = (top_project.facts or {}).get("local") or {}
        backdrop_path = pathlib.Path(local["backdrops"][0]) if local.get("backdrops") else None
        if backdrop_path and backdrop_path.exists():
            try:
                with Image.open(backdrop_path) as im:
                    bg = im.convert("RGB").resize((1280, 720))
                    canvas2.paste(bg, (0, 0))
            except Exception:
                pass

    # Huge Red #1 Badge in corner
    draw2.rounded_rectangle((60, 60, 320, 200), radius=24, fill=(229, 9, 20))
    b_f = media.font(92)
    draw2.text((100, 78), "#1 PICK", font=b_f, fill="white")

    # Banner across bottom
    draw2.rectangle((0, 560, 1280, 720), fill=(10, 10, 15, 235))
    bot_f = media.font(52)
    bot_text = "MUST-WATCH COUNTDOWN"
    draw2.text(((1280 - _text_w(draw2, bot_text, bot_f)) // 2, 610), bot_text, font=bot_f, fill=(255, 220, 0))
    canvas2.save(thumb2, quality=95)
    thumbs.append(thumb2)

    # Option 3: Split Hero Contrast
    thumb3 = out_dir / "thumb_option_3_split.jpg"
    canvas3 = Image.new("RGB", (1280, 720), (0, 0, 0))
    draw3 = ImageDraw.Draw(canvas3)

    if len(valid_posters) >= 2:
        try:
            with Image.open(valid_posters[0]) as im1, Image.open(valid_posters[1]) as im2:
                w_half = 640
                canvas3.paste(im1.convert("RGB").resize((w_half, 720)), (0, 0))
                canvas3.paste(im2.convert("RGB").resize((w_half, 720)), (w_half, 0))
        except Exception:
            pass

    # Center VS/Split Divider
    draw3.line([(640, 0), (640, 720)], fill=(229, 9, 20), width=10)
    # Center Badge
    draw3.rounded_rectangle((480, 290, 800, 430), radius=24, fill=(15, 15, 20, 240), outline=(229, 9, 20), width=4)
    cnt_f = media.font(64)
    cnt_text = f"TOP {len(projects)}"
    draw3.text(((1280 - _text_w(draw3, cnt_text, cnt_f)) // 2, 325), cnt_text, font=cnt_f, fill="white")
    canvas3.save(thumb3, quality=95)
    thumbs.append(thumb3)

    # Primary thumb defaults to Option 1
    shutil.copyfile(thumb1, out_dir / "thumbnail.jpg")
    return thumbs


def stitch_countdown_compilation(
    project_ids: list[int],
    title: str,
    custom_intro: str = "",
    custom_outro: str = "",
    accounts: list[str] | None = None,
    channel_name: str = "Screen Central",
    send_to_queue: bool = True,
) -> dict[str, Any]:
    """Assemble a master 8–12 min YouTube countdown compilation from Studio projects."""
    from ... import control
    control.check()

    with SessionLocal() as s:
        projects = []
        for pid in project_ids:
            p = s.get(StudioProject, pid)
            if not p:
                raise ValueError(f"Studio project #{pid} not found")
            p_video = resolve_project_render(p.id, p.render)
            if not p_video:
                raise ValueError(f"Project #{pid} ('{p.title}') is not rendered or missing on disk.")
            projects.append(p)

    if len(projects) < 2:
        raise ValueError("A countdown compilation requires at least 2 projects (typically 5 or 10).")

    comp_id = int(datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    comp_dir = compilations_dir() / str(comp_id)
    comp_dir.mkdir(parents=True, exist_ok=True)
    segs_dir = comp_dir / "segments"
    segs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate Intro Clip
    intro_mp4 = segs_dir / "00_intro.mp4"
    intro_path, intro_dur = create_intro_clip(projects, title, intro_mp4, custom_text=custom_intro)

    ordered_segments: list[pathlib.Path] = [intro_path]
    chapters: list[dict[str, Any]] = [
        {"time": 0.0, "time_formatted": "0:00", "title": "Intro & Preview"}
    ]
    cur_time = intro_dur

    # 2. Generate Bumpers & Collect Video Segments
    # projects list is ordered lowest rank (#5 / #10) to #1!
    total_count = len(projects)
    for idx, p in enumerate(projects):
        control.check()
        rank = total_count - idx  # #5, #4, #3, #2, #1

        facts = p.facts or {}
        local = facts.get("local") or {}
        backdrop_path = pathlib.Path(local["backdrops"][0]) if local.get("backdrops") else None

        # Determine badge text (release date or streaming)
        badge = ""
        if facts.get("networks"):
            badge = f"ON {facts['networks'][0].upper()}"
        elif facts.get("releases"):
            rel = next((r for r in facts["releases"] if r.get("type") in ("Theatrical", "Digital")), None)
            if rel and rel.get("date"):
                badge = f"RELEASING {rel['date'][:4]}"
        if not badge and p.facts.get("primary_date"):
            badge = f"RELEASE: {p.facts['primary_date'][:4]}"

        # Render countdown bumper clip
        bumper_mp4 = segs_dir / f"{idx + 1:02d}_bumper_{rank}.mp4"
        bumper.render_bumper_clip(rank, p.title, bumper_mp4, badge=badge, backdrop=backdrop_path, duration=2.5)
        ordered_segments.append(bumper_mp4)

        # Record chapter start at the bumper
        chapters.append({
            "time": round(cur_time, 2),
            "time_formatted": _format_time(cur_time),
            "title": f"#{rank}: {p.title}",
            "project_id": p.id,
        })
        cur_time += 2.5

        # Item video segment
        p_video = resolve_project_render(p.id, p.render)
        if not p_video or not p_video.exists():
            raise ValueError(f"Project #{p.id} render video not found on disk.")
        p_dur = float(p.render.get("seconds") or media.duration(p_video))

        # Direct copy into segments dir
        seg_item_mp4 = segs_dir / f"{idx + 1:02d}_content_{p.id}.mp4"
        shutil.copyfile(p_video, seg_item_mp4)
        ordered_segments.append(seg_item_mp4)

        cur_time += p_dur

    # 3. Generate Outro Clip
    control.check()
    top_p = projects[-1]
    top_local = (top_p.facts or {}).get("local") or {}
    top_backdrop = pathlib.Path(top_local["backdrops"][0]) if top_local.get("backdrops") else None

    outro_mp4 = segs_dir / "99_outro.mp4"
    outro_path, outro_dur = create_outro_clip(channel_name, outro_mp4, custom_text=custom_outro, backdrop=top_backdrop)
    ordered_segments.append(outro_path)

    chapters.append({
        "time": round(cur_time, 2),
        "time_formatted": _format_time(cur_time),
        "title": "Outro & Final Thoughts",
    })
    cur_time += outro_dur

    # 4. Master Concat & Audio Loudness Normalization
    control.check()
    concat_list = comp_dir / "concat_segments.txt"
    concat_list.write_text("".join(f"file '{f}'\n" for f in ordered_segments))

    raw_joined_mp4 = comp_dir / "master_raw.mp4"
    media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(raw_joined_mp4)])

    final_mp4 = comp_dir / "final_compilation.mp4"
    # Normalize master audio to YouTube broadcast standard -14 LUFS
    media.run([
        "ffmpeg", "-v", "error", "-y",
        "-i", str(raw_joined_mp4),
        "-c:v", "copy",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k",
        str(final_mp4)
    ])

    raw_joined_mp4.unlink(missing_ok=True)

    # 5. Generate 3-Tier Thumbnails
    thumbs = generate_compilation_thumbnails(projects, title, comp_dir / "thumbnails")
    primary_thumb = comp_dir / "thumbnails" / "thumbnail.jpg"

    # 6. Format Chapter Text & SEO Description
    chapters_txt = "\n".join(f"{ch['time_formatted']} - {ch['title']}" for ch in chapters)
    description = (
        f"Counting down the top {len(projects)} movie and television releases you cannot afford to miss! "
        f"From upcoming blockbusters to sleeper hits, here is our definitive breakdown.\n\n"
        f"TIMESTAMPS:\n{chapters_txt}\n\n"
        f"Subscribe to {channel_name} for daily movie news, trailers, and breakdowns.\n\n"
        f"{tmdb.ATTRIBUTION}"
    )

    tags = f"top {len(projects)}, movie countdown, best movies, upcoming movies 2026, trailer breakdown, screen central"
    for p in projects:
        tags += f", {p.title}"

    # 7. Push to Posting Queue
    queue_item_id = None
    if send_to_queue:
        with SessionLocal() as s:
            q = QueueItem(
                pipeline="LongForm",
                video_name=f"Top {len(projects)} - {title[:60]}",
                video_path=str(final_mp4),
                thumb_path=str(primary_thumb),
                title=title,
                description=description,
                tags=tags[:500],
                accounts=accounts or ["default:*"],
                status="ready",
                notes=f"Top {len(projects)} countdown compilation (#{comp_id})",
            )
            s.add(q)
            s.commit()
            queue_item_id = q.id

    return {
        "compilation_id": comp_id,
        "title": title,
        "total_seconds": round(cur_time, 2),
        "total_minutes": round(cur_time / 60, 2),
        "video_path": str(final_mp4),
        "primary_thumb": str(primary_thumb),
        "thumbnails": [str(t) for t in thumbs],
        "chapters": chapters,
        "chapters_text": chapters_txt,
        "description": description,
        "queue_item_id": queue_item_id,
    }
