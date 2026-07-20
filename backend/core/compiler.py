"""ffmpeg wrapper — normalize mixed-size clips and concat into one video.

Refactored from the original compile.py. Emits progress (0..1) via a callback so
the worker can stream encode progress to the UI. Uses libx264 by default for
portability (Railway has no VideoToolbox); the encoder is configurable.
"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Callable

PORTRAIT = (1080, 1920)
LANDSCAPE = (1920, 1080)


def _probe_duration(path: pathlib.Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        ).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def compile_videos(
    files: list[pathlib.Path],
    output: pathlib.Path,
    *,
    orientation: str = "portrait",
    encoder: str = "libx264",
    bitrate: str = "6000k",
    fps: int = 30,
    on_progress: Callable[[float], None] | None = None,
) -> pathlib.Path:
    """Stitch `files` into a single normalized MP4 at `output`. Returns the path."""
    files = [pathlib.Path(f) for f in files]
    missing = [f for f in files if not f.exists()]
    if missing:
        raise FileNotFoundError(f"missing: {', '.join(map(str, missing))}")
    if len(files) < 1:
        raise ValueError("need at least one video to compile")

    width, height = LANDSCAPE if orientation == "landscape" else PORTRAIT
    total = sum(_probe_duration(f) for f in files) or 1.0

    cmd = ["ffmpeg", "-hide_banner", "-y"]
    for f in files:
        cmd += ["-i", str(f)]

    filters, pairs = [], []
    for i in range(len(files)):
        filters.append(
            f"[{i}:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]"
        )
        filters.append(
            f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
        )
        pairs.append(f"[v{i}][a{i}]")
    filters.append(f"{''.join(pairs)}concat=n={len(files)}:v=1:a=1[vout][aout]")

    encoder_args = ["-c:v", encoder]
    if encoder == "libx264":
        encoder_args += ["-preset", "veryfast", "-crf", "23"]
    else:
        encoder_args += ["-b:v", bitrate]

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        *encoder_args,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(output),
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_ms=") and on_progress:
            try:
                secs = int(line.split("=", 1)[1]) / 1_000_000
                on_progress(min(secs / total, 0.99))
            except (ValueError, ZeroDivisionError):
                pass
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed")
    if on_progress:
        on_progress(1.0)
    return output
