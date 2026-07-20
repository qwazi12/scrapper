#!/usr/bin/env python3
"""
compile.py — stitch downloaded videos into one compilation MP4.

Usage:
    python3 compile.py                       # portrait (1080x1920), download order
    python3 compile.py --landscape           # landscape (1920x1080)
    python3 compile.py file1.mp4 file2.mp4   # specific files, given order
    python3 compile.py --landscape a.mp4 b.mp4

Orientation flag (--landscape / --portrait) may appear anywhere in the args.
Videos are normalized to the target canvas @ 30fps (scaled to fit, black-bar
padded) so mixed-size Twitter videos concat cleanly. Uses the macOS hardware
encoder (h264_videotoolbox) for speed. Output lands in downloads/ as
compilation_YYYY-MM-DD_HHMM[_landscape].mp4 (gitignored, like all videos).
"""

import datetime
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
MANIFEST = ROOT / "logs" / "manifest.jsonl"

FPS = 30
PORTRAIT = (1080, 1920)
LANDSCAPE = (1920, 1080)


def files_in_download_order() -> list[pathlib.Path]:
    """Read logs/manifest.jsonl and return existing video files, oldest first."""
    seen, files = set(), []
    if not MANIFEST.exists():
        sys.exit("No manifest.jsonl found — pass files explicitly.")
    for line in MANIFEST.read_text().splitlines():
        rec = json.loads(line)
        for f in rec.get("files", []):
            p = pathlib.Path(f["path"])
            # Manifest stores absolute paths; if the repo has moved, remap to
            # the current downloads/ dir by the path tail after "downloads/".
            if not p.exists():
                parts = p.parts
                if "downloads" in parts:
                    tail = parts[parts.index("downloads") + 1:]
                    p = DOWNLOADS.joinpath(*tail)
            if p.exists() and p not in seen:
                seen.add(p)
                files.append(p)
    return files


def main() -> int:
    args = sys.argv[1:]
    landscape = "--landscape" in args
    args = [a for a in args if a not in ("--landscape", "--portrait")]
    WIDTH, HEIGHT = LANDSCAPE if landscape else PORTRAIT

    if args:
        files = [pathlib.Path(a).resolve() for a in args]
        missing = [f for f in files if not f.exists()]
        if missing:
            sys.exit(f"Not found: {', '.join(map(str, missing))}")
    else:
        files = files_in_download_order()
    if len(files) < 2:
        sys.exit("Need at least 2 videos to compile.")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    suffix = "_landscape" if landscape else ""
    out = DOWNLOADS / f"compilation_{stamp}{suffix}.mp4"

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
