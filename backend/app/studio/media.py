"""ffmpeg / Pillow helpers for the Studio: probing, scene cuts, frames, fonts."""

from __future__ import annotations

import pathlib
import re
import subprocess
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageStat

FONT_CANDIDATES = {
    True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",            # Railway (fonts-dejavu-core)
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],              # macOS dev
    False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf"],
}


class MediaError(Exception):
    pass


def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-6:]
        raise MediaError(f"{cmd[0]} failed: {' | '.join(tail)[:600]}")
    return proc


def duration(path: str | pathlib.Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.stdout.strip())


def scene_cuts(path: str | pathlib.Path, threshold: float = 0.3) -> list[float]:
    """Times (seconds) where the picture changes hard, i.e. editor's cuts."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-vf", f"select='gt(scene,{threshold})',showinfo",
         "-an", "-f", "null", "-"], capture_output=True, text=True, timeout=1800)
    return sorted(float(t) for t in re.findall(r"pts_time:([0-9.]+)", proc.stderr))


def shots_from_cuts(cuts: list[float], total: float, min_len: float = 0.7) -> list[tuple[float, float]]:
    """Turn cut times into [start, end) shots, merging slivers into neighbours."""
    edges = [0.0] + [c for c in cuts if 0 < c < total] + [total]
    shots: list[list[float]] = []
    for a, b in zip(edges, edges[1:]):
        if shots and b - a < min_len:
            shots[-1][1] = b
        else:
            shots.append([a, b])
    if len(shots) > 1 and shots[0][1] - shots[0][0] < min_len:
        shots[1][0] = shots[0][0]
        shots.pop(0)
    return [(round(a, 3), round(b, 3)) for a, b in shots]


def active_area(path: str | pathlib.Path) -> str | None:
    """Detect letterbox/pillarbox bars baked into a video: 'w:h:x:y' of the real
    picture, or None when the frame is already full. Samples the middle of the file."""
    total = duration(path)
    start = max(0.0, total / 2 - 15)
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-ss", f"{start:.2f}", "-t", "30", "-i", str(path),
                           "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-f", "null", "-"],
                          capture_output=True, text=True, timeout=600)
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", proc.stderr)
    if not found:
        return None
    w, h, x, y = map(int, found[-1])
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", str(path)]).stdout.strip().split(",")
    fw, fh = int(probe[0]), int(probe[1])
    if w >= fw - 8 and h >= fh - 8:
        return None  # no meaningful bars
    if w < fw * 0.5 or h < fh * 0.4:
        return None  # a dark scene, not bars — don't trust it
    return f"{w}:{h}:{x}:{y}"


def frame(path: str | pathlib.Path, t: float, dest: pathlib.Path, width: int | None = None,
          crop: str | None = None) -> pathlib.Path:
    filters = ([f"crop={crop}"] if crop else []) + ([f"scale={width}:-2"] if width else [])
    vf = ["-vf", ",".join(filters)] if filters else []
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", *vf,
         "-q:v", "2", str(dest)], timeout=120)
    if not dest.exists():
        raise MediaError(f"no frame at {t:.2f}s")
    return dest


def brightness(img_path: pathlib.Path) -> float:
    with Image.open(img_path) as im:
        return ImageStat.Stat(im.convert("L")).mean[0]


@lru_cache(maxsize=None)
def _font_path(bold: bool) -> str | None:
    for p in FONT_CANDIDATES[bold]:
        if pathlib.Path(p).exists():
            return p
    return None


def font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    p = _font_path(bold)
    return ImageFont.truetype(p, size) if p else ImageFont.load_default()


def cast_sheet(cast: list[dict], photos: dict[str, str], dest: pathlib.Path) -> pathlib.Path | None:
    """A labelled grid of cast headshots, used to tell Gemini who is who."""
    people = [(c, photos.get(c["actor"])) for c in cast if photos.get(c["actor"])]
    if not people:
        return None
    cell_w, cell_h, label = 185, 278, 40
    cols = min(4, len(people))
    rows = (len(people) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * (cell_h + label)), "white")
    draw = ImageDraw.Draw(sheet)
    f = font(14)
    for i, (c, ph) in enumerate(people):
        x, y = (i % cols) * cell_w, (i // cols) * (cell_h + label)
        with Image.open(ph) as im:
            sheet.paste(im.convert("RGB").resize((cell_w, cell_h)), (x, y))
        draw.text((x + 4, y + cell_h + 2), f"{i + 1}. {c['actor']}"[:24], fill="black", font=f)
        draw.text((x + 4, y + cell_h + 20), f"as {c['character']}"[:24], fill="#444", font=font(12, False))
    sheet.save(dest, quality=88)
    return dest
