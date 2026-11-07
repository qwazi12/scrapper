"""Make DramaRama's YouTube dialogue-only copies ahead of time (owner, 2026-11-06). Runs on the owner's Mac.

For the next episodes of each series that YouTube hasn't had yet (its own sequence from EP01), this separates the
dialogue with Demucs (htdemucs vocals stem), re-encodes to H.264/AAC with the picture unchanged, checks the result,
and moves it into the shared-drive folder the backend reads (named <source Drive id>.mp4). Never overwrites.

Usage: <demucs venv>/bin/python -I scripts/drama_dialogue_prep.py [--series 3] [--per-series 7] [--dry-run]
Reads the queue through data/ops/ro_get.sh (GET only). Source files are read from Drive for Desktop.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import wave

REPO = pathlib.Path(__file__).resolve().parents[1]
DRIVE = pathlib.Path.home() / "Library/CloudStorage/GoogleDrive-kyeboah@kymediamgmt.com/Shared drives/Long Form Studio"
SOURCES = DRIVE / "DramaRama"
OUT = DRIVE / "DramaRama YouTube dialogue-only"          # Drive id 1yXoOy7CgmeI6E3a9n6IgLRTMT7LTyKnV (backend FOLDER_ID)
LOG = REPO / "data/ops/dialogue_prep.log"
XATTR = "com.google.drivefs.item-id#S"
SR = 44100


def log(msg: str) -> None:
    line = f"{datetime.datetime.now(datetime.timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, **kw)


def series_episode(row: dict) -> tuple[str, int | None]:
    series = (row.get("source") or "").partition(" / ")[2].strip()
    m = re.search(r"\bEP\s*0*(\d+)", row.get("video_name") or "", re.I) or re.search(r"\bEp\.\s*(\d+)", row.get("title") or "")
    return series, (int(m.group(1)) if m else None)


def source_id(row: dict) -> str | None:
    m = re.search(r"/d/([A-Za-z0-9_-]{10,100})", row.get("drive_link") or "")
    return m.group(1) if m else None


def done_on(row: dict, net: str) -> bool:
    return any(str(a).endswith(":" + net) for a, _ in ((row.get("catchup") or {}).get("done") or []))


def targets(rows: list[dict], n_series: int, per_series: int) -> list[dict]:
    """YouTube's next episodes: series already started on any platform first, then by arrival; EP order within."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        name, n = series_episode(r)
        if n is not None and source_id(r):
            by.setdefault(name, []).append(r)
    order = sorted(by, key=lambda s: (not any((r.get("catchup") or {}).get("done") for r in by[s]),
                                      min(r["id"] for r in by[s])))
    picked = []
    for name in order:
        todo = [r for r in sorted(by[name], key=lambda r: series_episode(r)[1]) if not done_on(r, "youtube")]
        if todo:
            picked += todo[:per_series]
            n_series -= 1
        if n_series == 0:
            break
    return picked


def make_copy(src: pathlib.Path, dest: pathlib.Path, model) -> str | None:
    """Write the checked dialogue-only copy to `dest`; returns why it failed, or None."""
    import numpy as np
    import torch
    from demucs.apply import apply_model
    raw = run(["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"], check=True).stdout
    mix = torch.from_numpy(np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T.copy())
    ref = mix.mean(0); m, s = ref.mean(), ref.std() + 1e-8
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    with torch.no_grad():
        stems = apply_model(model, ((mix - m) / s)[None], device=dev, split=True, overlap=0.25, progress=False)[0] * s + m
    vocals = stems[model.sources.index("vocals")]
    with tempfile.TemporaryDirectory() as tmp:
        wav = pathlib.Path(tmp) / "vocals.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes((vocals.clamp(-1, 1).T.numpy() * 32767).astype("<i2").tobytes())
        r = run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                 "-movflags", "+faststart", str(dest)])
        if r.returncode:
            return f"encode failed: {r.stderr.decode()[-200:]}"
    errors = run(["ffmpeg", "-v", "error", "-i", str(dest), "-f", "null", "-"]).stderr.decode().strip()
    if errors:
        return f"decode errors in the copy: {errors[:200]}"
    def video_end(p):   # picture end (start offset + length): sources may start their video late, e.g. 0.5 s
        out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=start_time,duration",
                   "-of", "csv=p=0", str(p)], text=True).stdout.strip().split(",")
        return sum(float(x) for x in out if x not in ("", "N/A"))
    if abs(video_end(dest) - video_end(src)) > 0.1:
        return f"picture ends at {video_end(dest):.2f}s vs source {video_end(src):.2f}s"
    vol = re.search(r"mean_volume: (-?[\d.]+) dB", run(["ffmpeg", "-i", str(dest), "-af", "volumedetect", "-f", "null", "-"]).stderr.decode())
    if not vol or float(vol.group(1)) < -45:
        return f"dialogue too quiet or missing (mean {vol.group(1) if vol else '?'} dB)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", type=int, default=3)
    ap.add_argument("--per-series", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not OUT.is_dir() or not SOURCES.is_dir():
        log(f"STOP: Drive folders not mounted ({OUT.is_dir()=}, {SOURCES.is_dir()=})")
        return 2
    q = run([str(REPO / "data/ops/ro_get.sh"), "/api/queue?limit=20000"], text=True)
    if q.returncode:
        log("STOP: queue read failed")
        return 2
    data = json.loads(q.stdout)
    rows = [r for r in (data["items"] if isinstance(data, dict) else data) if r.get("pipeline") == "DramaRama"]
    want = {source_id(r): r for r in targets(rows, a.series, a.per_series)}
    todo = {sid: r for sid, r in want.items() if not (OUT / f"{sid}.mp4").exists()}
    log(f"plan: {len(want)} upcoming YouTube episodes, {len(todo)} without a copy")
    if a.dry_run or not todo:
        for sid, r in todo.items():
            log(f"  would make #{r['id']} {'/'.join(map(str, series_episode(r)))} -> {sid}.mp4")
        return 0
    paths = {}
    for dp, _, files in os.walk(SOURCES):
        for f in files:
            p = pathlib.Path(dp) / f
            fid = run(["xattr", "-p", XATTR, str(p)], text=True).stdout.strip()
            if fid in todo:
                paths[fid] = p
    from demucs.pretrained import get_model
    model = get_model("htdemucs"); model.eval()
    made = failed = 0
    for sid, r in todo.items():
        name = f"#{r['id']} {series_episode(r)[0][:40]} EP{series_episode(r)[1]}"
        if sid not in paths:
            log(f"skip {name}: source not found on the mount"); failed += 1; continue
        with tempfile.TemporaryDirectory() as tmp:
            staged = pathlib.Path(tmp) / f"{sid}.mp4"
            try:
                why = make_copy(paths[sid], staged, model)
            except Exception as exc:  # noqa: BLE001
                why = f"{type(exc).__name__}: {str(exc)[:200]}"
            dest = OUT / f"{sid}.mp4"
            if why:
                log(f"FAIL {name}: {why}"); failed += 1; continue
            if dest.exists():
                log(f"skip {name}: {dest.name} appeared meanwhile, not overwritten"); continue
            r2 = run(["mv", "-n", str(staged), str(dest)])
            if r2.returncode or staged.exists():
                log(f"FAIL {name}: move into Drive failed"); failed += 1; continue
            log(f"made {name} -> {dest.name}"); made += 1
    log(f"done: {made} made, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
