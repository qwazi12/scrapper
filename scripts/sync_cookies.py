#!/usr/bin/env python3
"""Export cookies from a local browser and push them to the Scrapper server.

Why this exists: yt-dlp's `--cookies-from-browser` reads a real browser profile
off local disk. The Railway backend is a headless Linux container with no
browser, so it can never do that itself. This runs on YOUR machine, exports a
fresh cookie jar, and uploads it to the server's /api/cookies endpoint.

    python3 scripts/sync_cookies.py --browser chrome

Config comes from data/deploy.env (SERVER_URL / ACCESS_TOKEN) or flags.

To keep cookies fresh automatically, run it on a schedule. macOS launchd:
    scripts/install_cookie_sync.sh          # installs a daily 9am job

SECURITY: a cookie jar is a live credential — anyone holding it can act as your
logged-in account. Use a BURNER account, never your main one. The file is sent
over HTTPS and stored on the server volume.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = REPO / "data" / "deploy.env"

# A cheap page on each site that yt-dlp can touch to trigger the cookie export.
PROBE = {
    "x": "https://x.com/robots.txt",
    "youtube": "https://www.youtube.com/robots.txt",
    "tiktok": "https://www.tiktok.com/robots.txt",
}


def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def export_cookies(browser: str, probe_url: str, out: pathlib.Path) -> None:
    """Have yt-dlp read the browser profile and write a Netscape cookie jar."""
    cmd = [
        "yt-dlp",
        "--cookies-from-browser", browser,
        "--cookies", str(out),
        "--skip-download",
        "--no-warnings",
        "--simulate",
        probe_url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # yt-dlp often "fails" on a robots.txt probe but still writes the jar.
    if not out.exists() or out.stat().st_size == 0:
        err = (proc.stderr or proc.stdout).strip().splitlines()
        hint = err[-1] if err else "no output"
        raise SystemExit(
            f"Could not read cookies from {browser}: {hint}\n"
            f"Tips: quit the browser first; on macOS Safari needs Full Disk Access; "
            f"Firefox is usually the most reliable source."
        )


def upload(server: str, token: str, jar: pathlib.Path) -> None:
    body, boundary = build_multipart(jar)
    req = urllib.request.Request(
        f"{server.rstrip('/')}/api/cookies",
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "x-access-token": token,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        print(f"uploaded → {r.status} {r.read().decode()[:200]}")


def build_multipart(path: pathlib.Path) -> tuple[bytes, str]:
    boundary = "----scrapperCookieSync"
    data = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="cookies.txt"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return body, boundary


def main() -> int:
    env = load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--browser", default="chrome",
                    help="chrome | firefox | safari | edge | brave | opera | vivaldi")
    ap.add_argument("--site", default="x", choices=sorted(PROBE),
                    help="which site's cookies to prime (default: x)")
    ap.add_argument("--server", default=env.get("SERVER_URL", ""), help="Scrapper API base URL")
    ap.add_argument("--token", default=env.get("ACCESS_TOKEN", ""), help="API access token")
    ap.add_argument("--out", default="", help="also keep a copy of the jar here")
    args = ap.parse_args()

    if not args.server or not args.token:
        sys.exit("Need --server and --token (or set SERVER_URL/ACCESS_TOKEN in data/deploy.env)")

    with tempfile.TemporaryDirectory() as tmp:
        jar = pathlib.Path(args.out) if args.out else pathlib.Path(tmp) / "cookies.txt"
        jar.parent.mkdir(parents=True, exist_ok=True)
        print(f"reading cookies from {args.browser}…")
        export_cookies(args.browser, PROBE[args.site], jar)
        print(f"exported {jar.stat().st_size} bytes; uploading to {args.server}")
        upload(args.server, args.token, jar)
    return 0


if __name__ == "__main__":
    sys.exit(main())
