"""YouTube-only dialogue audio for DramaRama (owner, 2026-11-06, after an AdRev music claim blocked #6413).

The owner's Mac makes each copy ahead of time (Demucs vocals stem, H.264/AAC, picture unchanged) and puts it in
one Drive folder, named after the source file's Drive id. YouTube gets that copy or waits: never the original audio.
Facebook and X keep the original."""
from __future__ import annotations

import pathlib
import re
from typing import Any

from .drive_sync import download_drive_file, extract_drive_file_id, get_drive_service

FOLDER_ID = "1yXoOy7CgmeI6E3a9n6IgLRTMT7LTyKnV"   # Long Form Studio / DramaRama YouTube dialogue-only
_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,100}")


def copy_name(item: Any) -> str | None:
    """`<source Drive id>.mp4`, or None when the row has no Drive source."""
    src = extract_drive_file_id(item.drive_link or "") if item.drive_link else None
    return f"{src}.mp4" if src and _DRIVE_ID.fullmatch(src) else None


def fetch(item: Any) -> tuple[pathlib.Path | None, str | None]:
    """(local path of this episode's dialogue-only copy, None) or (None, why YouTube waits)."""
    name = copy_name(item)
    if not name:
        return None, "no Drive source to match a dialogue-only copy"
    try:
        files = get_drive_service().files().list(
            q=f"'{FOLDER_ID}' in parents and name = '{name}' and trashed = false", fields="files(id)",
            supportsAllDrives=True, includeItemsFromAllDrives=True, corpora="allDrives").execute().get("files") or []
        if len(files) != 1:
            return None, f"{len(files)} dialogue-only copies named {name} (needs exactly 1)"
        return download_drive_file(files[0]["id"]), None
    except Exception as exc:  # noqa: BLE001 — any failure means YouTube waits, never the original audio
        return None, f"dialogue-only copy unavailable ({str(exc)[:120]})"
