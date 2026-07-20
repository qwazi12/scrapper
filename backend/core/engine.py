"""Engine health + self-healing (Reliability Layer 1).

yt-dlp breaks whenever X/YouTube change things, and the fix ships within days.
So we keep the engine fresh: update on startup (best-effort) and nightly.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def ytdlp_version() -> str | None:
    code, out = _run(["yt-dlp", "--version"], timeout=30)
    return out.splitlines()[0] if code == 0 and out else None


def ffmpeg_present() -> bool:
    return shutil.which("ffmpeg") is not None


def update_ytdlp() -> tuple[bool, str]:
    """Self-update yt-dlp. Tries the built-in updater, then pip."""
    before = ytdlp_version()
    code, out = _run(["yt-dlp", "-U"], timeout=180)
    if code != 0:
        code, out = _run(
            [sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp"],
            timeout=300,
        )
    after = ytdlp_version()
    changed = before != after
    msg = f"yt-dlp {before} -> {after}" if changed else f"yt-dlp up to date ({after})"
    return code == 0, msg


def health() -> dict:
    return {
        "yt_dlp": ytdlp_version(),
        "ffmpeg": ffmpeg_present(),
        "python": sys.version.split()[0],
    }
