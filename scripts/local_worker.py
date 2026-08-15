#!/usr/bin/env python3
"""Download the links the cloud server can't, from your home connection.

Why this exists: Railway runs on a datacenter IP, and YouTube (and often TikTok)
refuse those outright with "Sign in to confirm you're not a bot" — no account or
cookie fixes that reliably, and pushing account cookies through a datacenter IP
is what gets Google accounts flagged. Your home connection is not blocked, so
this claims the blocked URLs, downloads them here, and uploads them back.

    python3 scripts/local_worker.py              # drain the queue once and exit
    python3 scripts/local_worker.py --watch      # keep polling (Ctrl-C to stop)
    python3 scripts/local_worker.py --browser chrome   # also send browser cookies

Config comes from data/deploy.env (SERVER_URL / ACCESS_TOKEN) or the flags.
Install it as a background job with scripts/install_local_worker.sh.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

REPO = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = REPO / "data" / "deploy.env"


def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api_get(server: str, token: str, path: str):
    req = urllib.request.Request(f"{server.rstrip('/')}{path}",
                                 headers={"x-access-token": token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def download(url: str, workdir: pathlib.Path, browser: str | None) -> tuple[pathlib.Path, dict]:
    """Fetch one URL locally. Returns (video_path, info_dict)."""
    out_tpl = str(workdir / "%(uploader_id)s_%(id)s_%(title).60B.%(ext)s")
    cmd = [
        "yt-dlp",
        "-t", "sleep",                     # same polite pacing as the server
        "--output", out_tpl,
        "--format", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "--no-progress",
        "--retries", "5",
    ]
    if browser:
        cmd += ["--cookies-from-browser", browser]
    cmd.append(url)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    videos = sorted(workdir.glob("*.mp4"))
    if proc.returncode != 0 or not videos:
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        raise RuntimeError(tail[-1] if tail else "yt-dlp failed")

    video = videos[0]
    info_path = video.with_suffix(".info.json")
    info = json.loads(info_path.read_text()) if info_path.exists() else {}
    return video, info


def upload(server: str, token: str, clip_id: int, video: pathlib.Path, info: dict) -> dict:
    """Multipart-upload the video (+thumb, +metadata) back to the server."""
    boundary = f"----scrapperLocalWorker{uuid.uuid4().hex}"
    thumb = video.with_suffix(".jpg")
    parts: list[bytes] = []

    def add_file(field: str, path: pathlib.Path, ctype: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n".encode()
            + path.read_bytes() + b"\r\n"
        )

    add_file("file", video, "video/mp4")
    if thumb.exists():
        add_file("thumb", thumb, "image/jpeg")
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"meta\"\r\n\r\n".encode()
        + json.dumps(info).encode() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        f"{server.rstrip('/')}/api/clips/{clip_id}/upload",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "x-access-token": token,
            "Content-Length": str(len(body)),
        },
    )
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode())


def drain(server: str, token: str, browser: str | None) -> int:
    try:
        blocked = api_get(server, token, "/api/clips/blocked")
    except urllib.error.URLError as exc:
        print(f"  ! cannot reach server: {exc}")
        return 0
    if not blocked:
        return 0

    print(f"  {len(blocked)} blocked URL(s) to fetch locally")
    done = 0
    for item in blocked:
        url, cid = item["source_url"], item["id"]
        print(f"  → [{cid}] {url}")
        with tempfile.TemporaryDirectory() as tmp:
            try:
                video, info = download(url, pathlib.Path(tmp), browser)
                mb = video.stat().st_size / 1e6
                print(f"     downloaded {mb:.0f} MB — uploading…")
                upload(server, token, cid, video, info)
                print(f"     ✓ {info.get('title', url)[:60]}")
                done += 1
            except Exception as exc:
                print(f"     ✗ {exc}")
    return done


def main() -> int:
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=env.get("SERVER_URL", ""))
    ap.add_argument("--token", default=env.get("ACCESS_TOKEN", ""))
    ap.add_argument("--browser", default="", help="chrome | firefox | safari | edge | brave")
    ap.add_argument("--watch", action="store_true", help="keep polling instead of exiting")
    ap.add_argument("--interval", type=int, default=120, help="seconds between polls in --watch")
    args = ap.parse_args()

    if not args.server or not args.token:
        sys.exit("Need --server and --token (or SERVER_URL/ACCESS_TOKEN in data/deploy.env)")

    browser = args.browser or None
    if not args.watch:
        n = drain(args.server, args.token, browser)
        print(f"done — {n} clip(s) recovered")
        return 0

    print(f"watching {args.server} every {args.interval}s (Ctrl-C to stop)")
    while True:
        try:
            drain(args.server, args.token, browser)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"  ! {exc}")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
