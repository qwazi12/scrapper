"""Countdown transition bumpers (#10 down to #1) for countdown compilations.

Generates 1920x1080 30fps transition bumpers with bold Screen Central typography,
entry numbers, movie/show title, release/platform badge, and matching timescale
for seamless ffmpeg concatenation without re-encoding.
"""

from __future__ import annotations

import pathlib
from PIL import Image, ImageDraw, ImageFilter

from .. import media

W, H, FPS = 1920, 1080, 30

ENC = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
    "-video_track_timescale", "30000",
    "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"
]
NORM = "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p"


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    l, _, r, _ = draw.textbbox((0, 0), text, font=font)
    return r - l


def create_bumper_frame(
    rank: int,
    title: str,
    badge: str = "",
    backdrop: pathlib.Path | None = None,
    dest: pathlib.Path | None = None,
) -> pathlib.Path:
    """Generate a single 1920x1080 high-contrast countdown graphic frame."""
    canvas = Image.new("RGB", (W, H), (10, 10, 14))

    if backdrop and backdrop.exists():
        try:
            with Image.open(backdrop) as im:
                bg = im.convert("RGB").resize((W, H))
                bg = bg.filter(ImageFilter.GaussianBlur(32)).point(lambda v: int(v * 0.35))
                canvas.paste(bg, (0, 0))
        except Exception:
            pass

    draw = ImageDraw.Draw(canvas)

    # 1. Countdown Badge (#05 or #1)
    rank_str = f"#{rank}" if rank >= 10 else f"#0{rank}"
    badge_f = media.font(58)
    bw = _text_w(draw, rank_str, badge_f) + 64
    bh = 84
    bx = (W - bw) // 2
    by = H // 2 - 160

    # Red badge background with subtle glow/border
    draw.rounded_rectangle((bx - 2, by - 2, bx + bw + 2, by + bh + 2), radius=22, fill=(255, 60, 60, 120))
    draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=20, fill=(229, 9, 20))
    draw.text((bx + 32, by + 12), rank_str, font=badge_f, fill="white")

    # 2. Main Title (uppercase, bold)
    title_upper = title.upper()
    title_f = media.font(64)
    tw = _text_w(draw, title_upper, title_f)
    if tw > W - 160:
        # Scale down if too long
        title_f = media.font(48)
        tw = _text_w(draw, title_upper, title_f)
    if tw > W - 160:
        title_f = media.font(38)
        tw = _text_w(draw, title_upper, title_f)

    tx = (W - tw) // 2
    ty = by + bh + 40
    # Shadow
    draw.text((tx + 3, ty + 3), title_upper, font=title_f, fill=(0, 0, 0))
    draw.text((tx, ty), title_upper, font=title_f, fill="white")

    # 3. Subtitle / Platform / Release Date Badge
    if badge:
        badge_clean = badge.upper()
        sub_f = media.font(28)
        sw = _text_w(draw, badge_clean, sub_f) + 48
        sh = 50
        sx = (W - sw) // 2
        sy = ty + 90

        draw.rounded_rectangle((sx, sy, sx + sw, sy + sh), radius=12, fill=(25, 25, 30, 230), outline=(229, 9, 20), width=2)
        draw.text((sx + 24, sy + 10), badge_clean, font=sub_f, fill=(235, 235, 235))

    out_file = dest or pathlib.Path("/tmp/bumper_frame.jpg")
    canvas.save(out_file, quality=95)
    return out_file


def render_bumper_clip(
    rank: int,
    title: str,
    dest_mp4: pathlib.Path,
    badge: str = "",
    backdrop: pathlib.Path | None = None,
    duration: float = 2.5,
) -> pathlib.Path:
    """Render a standalone 1920x1080 30fps MP4 bumper video with silent audio track."""
    frame_jpg = dest_mp4.with_suffix(".jpg")
    create_bumper_frame(rank, title, badge, backdrop=backdrop, dest=frame_jpg)

    n_frames = max(1, round(duration * FPS))
    # Slow subtle push zoom
    vf = f"zoompan=z='1+0.04*on/{n_frames}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS},{NORM},setsar=1"

    # Generate video with silent stereo audio to keep concat streams 100% uniform
    media.run([
        "ffmpeg", "-v", "error", "-y",
        "-loop", "1", "-i", str(frame_jpg),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        *ENC,
        str(dest_mp4)
    ])

    try:
        frame_jpg.unlink(missing_ok=True)
    except Exception:
        pass

    return dest_mp4
