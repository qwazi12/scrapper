#!/usr/bin/env python3
"""
scrape.py — personal watch-later video downloader built on yt-dlp.

Usage:
    python3 scrape.py <url> [<url> ...]     # download specific URLs
    python3 scrape.py                       # download everything in urls.txt
    python3 scrape.py --no-push             # skip the git commit/push step

Everything is logged to logs/scraper.log and each run is summarized in
MEMORY.md. Downloaded video files live in downloads/ (gitignored — only
code, logs, and metadata are committed).

Works for X/Twitter, YouTube, TikTok, Instagram, and the ~1800 other
sites yt-dlp supports.
"""

import argparse
import datetime
import json
import logging
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
LOGS = ROOT / "logs"
URLS_FILE = ROOT / "urls.txt"
ARCHIVE = ROOT / "archive.txt"          # yt-dlp dedup ledger (video IDs)
MEMORY = ROOT / "MEMORY.md"
MANIFEST = LOGS / "manifest.jsonl"      # one JSON record per downloaded video

OUTPUT_TEMPLATE = str(DOWNLOADS / "%(extractor)s" / "%(uploader_id)s_%(id)s_%(title).60B.%(ext)s")

YTDLP_ARGS = [
    "yt-dlp",
    "--download-archive", str(ARCHIVE),
    "--output", OUTPUT_TEMPLATE,
    "--format", "bv*+ba/b",             # best video + best audio, fallback best
    "--merge-output-format", "mp4",
    "--write-info-json",
    "--no-overwrites",
    "--retries", "5",
    "--fragment-retries", "5",
    "--print", "after_move:__DONE__ %(id)s | %(uploader_id)s | %(title)s | %(filepath)s",
    "--no-progress",
]


def setup_logging() -> logging.Logger:
    LOGS.mkdir(exist_ok=True)
    logger = logging.getLogger("scraper")
    logger.setLevel(logging.DEBUG)
    fh = logging.FileHandler(LOGS / "scraper.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def read_url_queue() -> list[str]:
    if not URLS_FILE.exists():
        return []
    urls = []
    for line in URLS_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def download(url: str, log: logging.Logger) -> dict:
    """Run yt-dlp on one URL. Returns a result record."""
    log.info("START %s", url)
    proc = subprocess.run(
        YTDLP_ARGS + [url],
        capture_output=True, text=True,
    )
    record = {
        "url": url,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "status": "ok" if proc.returncode == 0 else "failed",
        "files": [],
    }
    for line in proc.stdout.splitlines():
        log.debug("yt-dlp: %s", line)
        if line.startswith("__DONE__"):
            parts = [p.strip() for p in line[len("__DONE__"):].split("|")]
            if len(parts) == 4:
                record["files"].append({
                    "id": parts[0], "uploader": parts[1],
                    "title": parts[2], "path": parts[3],
                })
    if proc.returncode == 0 and not record["files"]:
        # already in archive.txt from a previous run
        record["status"] = "skipped (already downloaded)"
    if proc.returncode != 0:
        record["error"] = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "unknown"
        log.error("FAILED %s — %s", url, record["error"])
        for line in proc.stderr.splitlines():
            log.debug("yt-dlp stderr: %s", line)
    else:
        log.info("DONE %s (%d file(s))", url, len(record["files"]))
    with MANIFEST.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def append_memory(results: list[dict]) -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## Run — {now}\n"]
    for r in results:
        if r["files"]:
            for f in r["files"]:
                lines.append(f"- ✅ **{f['uploader']}** — {f['title']} (`{pathlib.Path(f['path']).name}`)")
        elif r["status"].startswith("skipped"):
            lines.append(f"- ⏭️ already saved: {r['url']}")
        else:
            lines.append(f"- ❌ failed: {r['url']} — {r.get('error', '?')}")
    with MEMORY.open("a") as f:
        f.write("\n".join(lines) + "\n")


def git_commit_push(log: logging.Logger, push: bool = True) -> None:
    subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
    n = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    commit = subprocess.run(
        ["git", "commit", "-m", f"scrape run {n}: update logs and memory"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if commit.returncode != 0:
        log.info("git: nothing new to commit")
        return
    log.info("git: committed run %s", n)
    if push:
        p = subprocess.run(["git", "push", "origin", "main"],
                           cwd=ROOT, capture_output=True, text=True)
        if p.returncode == 0:
            log.info("git: pushed to origin/main")
        else:
            log.warning("git: push failed (will retry next run): %s", p.stderr.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Download videos for later watching.")
    parser.add_argument("urls", nargs="*", help="video URLs (default: read urls.txt)")
    parser.add_argument("--no-push", action="store_true", help="commit locally but don't push")
    args = parser.parse_args()

    log = setup_logging()
    DOWNLOADS.mkdir(exist_ok=True)

    urls = args.urls or read_url_queue()
    if not urls:
        log.error("No URLs given and urls.txt is empty.")
        return 1

    log.info("=== Run started: %d URL(s) ===", len(urls))
    results = [download(u, log) for u in urls]
    append_memory(results)

    ok = sum(1 for r in results if r["files"])
    skipped = sum(1 for r in results if r["status"].startswith("skipped"))
    failed = sum(1 for r in results if r["status"] == "failed")
    log.info("=== Run finished: %d downloaded, %d skipped, %d failed ===", ok, skipped, failed)

    git_commit_push(log, push=not args.no_push)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
