#!/usr/bin/env python3
"""
compile.py — stitch downloaded videos into one compilation MP4.

Usage:
    python3 compile.py                     # compile every video in download order
    python3 compile.py file1.mp4 file2.mp4 # compile specific files in given order

Videos are normalized to 1080x1920 @ 30fps (scaled to fit, black-bar padded)
so mixed-size Twitter videos concat cleanly. Uses the macOS hardware encoder
(h264_videotoolbox) for speed. Output lands in downloads/ as
compilation_YYYY-MM-DD_HHMM.mp4 (gitignored, like all videos).
"""

import datetime
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
MANIFEST = ROOT / "logs" / "manifest.jsonl"

WIDTH, HEIGHT, FPS = 1080, 1920, 30


def files_in_download_order() -> list[pathlib.Path]:
    """Read logs/manifest.jsonl and return existing video files, oldest first."""
    seen, files = set(), []
    if not MANIFEST.exists():
        sys.exit("No manifest.jsonl found — pass files explicitly.")
    for line in MANIFEST.read_text().splitlines():
        rec = json.loads(line)
        for f in rec.get("files", []):
            p = pathlib.Path(f["path"])
            if p.exists() and p not in seen:
                seen.add(p)
                files.append(p)
    return files


def main() -> int:
    if len(sys.argv) > 1:
        files = [pathlib.Path(a).resolve() for a in sys.argv[1:]]
        missing = [f for f in files if not f.exists()]
        if missing:
            sys.exit(f"Not found: {', '.join(map(str, missing))}")
    else:
        files = files_in_download_order()
    if len(files) < 2:
        sys.exit("Need at least 2 videos to compile.")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    out = DOWNLOADS / f"compilation_{stamp}.mp4"

    cmd = ["ffmpeg", "-hide_banner", "-y"]
    for f in files:
        cmd += ["-i", str(f)]

    filters, pairs = [], []
    for i in range(len(files)):
        filters.append(
            f"[{i}:v]fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]"
        )
        filters.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        pairs.append(f"[v{i}][a{i}]")
    filters.append(f"{''.join(pairs)}concat=n={len(files)}:v=1:a=1[vout][aout]")

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "h264_videotoolbox", "-b:v", "6000k",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out),
    ]

    print(f"Compiling {len(files)} videos -> {out.name}")
    for i, f in enumerate(files, 1):
        print(f"  {i}. {f.name}")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        sys.exit("ffmpeg failed")
    size_mb = out.stat().st_size / 1e6
    print(f"Done: {out} ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
