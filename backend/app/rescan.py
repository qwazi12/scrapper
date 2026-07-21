"""Self-healing library rebuild.

The video files on the volume are the source of truth: every download keeps a
sibling `.info.json` with its metadata. This walks the volume and reconstructs
any missing DB rows, so a lost/rolled-back database recovers automatically
(runs on startup) and orphaned files can always be re-adopted (Rescan button).
"""

from __future__ import annotations

import json
import pathlib

from sqlalchemy.orm import Session

from ..core.compiler import _probe_duration
from ..core.scraper import platform_of
from .config import settings
from .logbus import log
from .models import Clip, Compilation, Status


def _read_info(video: pathlib.Path) -> dict:
    info_path = video.with_suffix(".info.json")
    if info_path.exists():
        try:
            return json.loads(info_path.read_text())
        except Exception:
            return {}
    return {}


def rescan_library(s: Session) -> dict:
    """Rebuild Clip + Compilation rows from files on the volume. Idempotent."""
    added_clips = 0
    added_comps = 0

    known_clip_paths = {c.file_path for c in s.query(Clip.file_path).all() if c.file_path}
    for video in sorted(settings.downloads_path.rglob("*.mp4")):
        vpath = str(video)
        if vpath in known_clip_paths:
            continue
        info = _read_info(video)
        thumb = video.with_suffix(".jpg")
        url = info.get("webpage_url") or info.get("original_url") or ""
        clip = Clip(
            source_url=url or vpath,
            platform=platform_of(url) if url else (info.get("extractor") or None),
            video_id=info.get("id"),
            uploader=info.get("uploader") or info.get("uploader_id"),
            title=info.get("title"),
            duration=info.get("duration"),
            width=info.get("width"),
            height=info.get("height"),
            file_path=vpath,
            thumb_path=str(thumb) if thumb.exists() else None,
            size_bytes=video.stat().st_size,
            status=Status.done,
            selected=True,
        )
        s.add(clip)
        added_clips += 1
    if added_clips:
        s.commit()

    known_comp_paths = {c.output_path for c in s.query(Compilation.output_path).all() if c.output_path}
    for video in sorted(settings.compilations_path.glob("*.mp4")):
        vpath = str(video)
        if vpath in known_comp_paths:
            continue
        comp = Compilation(
            clip_ids=[],
            orientation="landscape" if "_landscape" in video.name else "portrait",
            status=Status.done,
            progress=1.0,
            output_path=vpath,
            size_bytes=video.stat().st_size,
            duration=_probe_duration(video),
        )
        s.add(comp)
        added_comps += 1
    if added_comps:
        s.commit()

    if added_clips or added_comps:
        log("info", "rescan", f"recovered {added_clips} clip(s), {added_comps} compilation(s) from disk")
    return {"clips_added": added_clips, "compilations_added": added_comps}
