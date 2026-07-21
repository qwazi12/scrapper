"""yt-dlp wrapper — downloads one URL and returns rich metadata.

Refactored from the original scrape.py into an importable function used by both
the web worker and the CLI. Supports cookies (Layer 2) and a proxy (Layer 4).
Retries/backoff live in the worker so they can update job state between tries.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class ScrapeResult:
    url: str
    ok: bool = False
    already: bool = False           # was in the archive already
    clips: list[dict] = field(default_factory=list)
    error: str | None = None
    log_lines: list[str] = field(default_factory=list)


def platform_of(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for key in ("x.com", "twitter.com", "youtube.com", "youtu.be", "tiktok.com", "instagram.com"):
        if key in host:
            return {"x.com": "x", "twitter.com": "x", "youtu.be": "youtube"}.get(key, key.split(".")[0])
    return host or "unknown"


def scrape(
    url: str,
    *,
    downloads_dir: pathlib.Path,
    archive_path: pathlib.Path,
    cookies_path: pathlib.Path | None = None,
    proxy: str | None = None,
    sleep_preset: bool = True,
    cookies_from_browser: str | None = None,
) -> ScrapeResult:
    """Download a single URL. Returns a ScrapeResult with per-clip metadata."""
    result = ScrapeResult(url=url)
    out_template = str(downloads_dir / "%(extractor)s" / "%(uploader_id)s_%(id)s_%(title).60B.%(ext)s")

    cmd = [
        "yt-dlp",
        "--download-archive", str(archive_path),
        "--output", out_template,
        "--format", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-thumbnail",
        "--convert-thumbnails", "jpg",
        "--no-overwrites",
        "--retries", "5",
        "--fragment-retries", "5",
        "--no-progress",
        "--print", "after_move:__DONE__ %(filepath)s",
    ]
    # Anti-ban pacing: 0.75s between requests + randomized 10-20s between
    # downloads. Slower on purpose — this is what keeps a headless server from
    # looking like a scraper to YouTube/TikTok/X.
    if sleep_preset:
        cmd += ["-t", "sleep"]
    # A cookies.txt file wins; otherwise pull straight from a local browser
    # profile (only possible where a browser actually exists).
    if cookies_path and cookies_path.exists():
        cmd += ["--cookies", str(cookies_path)]
    elif cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if proxy:
        cmd += ["--proxy", proxy]
    cmd.append(url)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    result.log_lines = (proc.stdout + proc.stderr).splitlines()

    final_paths: list[str] = []
    for line in proc.stdout.splitlines():
        if line.startswith("__DONE__ "):
            final_paths.append(line[len("__DONE__ "):].strip())

    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()
        result.error = tail[-1] if tail else "yt-dlp failed"
        return result

    if not final_paths:
        # Success exit but nothing new -> already in the archive.
        result.ok = True
        result.already = True
        return result

    for vpath in final_paths:
        result.clips.append(_metadata_for(vpath, url))
    result.ok = True
    return result


def _metadata_for(video_path: str, url: str) -> dict:
    p = pathlib.Path(video_path)
    info_path = p.with_suffix(".info.json")
    thumb_path = p.with_suffix(".jpg")
    meta: dict = {
        "source_url": url,
        "platform": platform_of(url),
        "file_path": str(p),
        "thumb_path": str(thumb_path) if thumb_path.exists() else None,
        "size_bytes": p.stat().st_size if p.exists() else None,
    }
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text())
            meta.update({
                "video_id": info.get("id"),
                "uploader": info.get("uploader") or info.get("uploader_id"),
                "title": info.get("title"),
                "duration": info.get("duration"),
                "width": info.get("width"),
                "height": info.get("height"),
            })
        except Exception:
            pass
    return meta
