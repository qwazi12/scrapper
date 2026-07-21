"""Self-healing library reconcile.

The video files on the volume are the source of truth: every download keeps a
sibling `.info.json`. This reconciles the DB, the files, and the yt-dlp dedup
archive in one pass so the three never drift:

  1. Prune DB clip/compilation rows whose file no longer exists.
  2. Adopt on-disk videos that have no DB row (rebuild a lost/rolled-back DB).
  3. Delete orphaned `.info.json` sidecars whose `.mp4` is gone.
  4. Rebuild `archive.txt` to only reference videos still on disk, so a deleted
     video can be re-scraped instead of returning a phantom "already downloaded".

Runs on startup and via POST /api/rescan. Idempotent.
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


def _existing_mp4s() -> list[pathlib.Path]:
    return sorted(settings.downloads_path.rglob("*.mp4"))


def rescan_library(s: Session) -> dict:
    added_clips = added_comps = pruned_clips = pruned_comps = 0
    mp4_paths = {str(p) for p in _existing_mp4s()}

    # 1. Prune DB clips whose file is gone (leave in-flight ones alone).
    for clip in s.query(Clip).all():
        if clip.status in (Status.queued, Status.running):
            continue
        if not clip.file_path or clip.file_path not in mp4_paths:
            s.delete(clip)
            pruned_clips += 1
    if pruned_clips:
        s.commit()

    # 2. Adopt on-disk videos with no DB row.
    known = {c.file_path for c in s.query(Clip.file_path).all() if c.file_path}
    for video in _existing_mp4s():
        vpath = str(video)
        if vpath in known:
            continue
        info = _read_info(video)
        thumb = video.with_suffix(".jpg")
        url = info.get("webpage_url") or info.get("original_url") or ""
        s.add(Clip(
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
        ))
        added_clips += 1
    if added_clips:
        s.commit()

    # 3. Reconcile compilations with the compilations dir.
    comp_files = {str(p) for p in settings.compilations_path.glob("*.mp4")}
    for comp in s.query(Compilation).all():
        if comp.status in (Status.queued, Status.running):
            continue
        if not comp.output_path or comp.output_path not in comp_files:
            s.delete(comp)
            pruned_comps += 1
    if pruned_comps:
        s.commit()
    known_comp = {c.output_path for c in s.query(Compilation.output_path).all() if c.output_path}
    for video in sorted(settings.compilations_path.glob("*.mp4")):
        vpath = str(video)
        if vpath in known_comp:
            continue
        s.add(Compilation(
            clip_ids=[],
            orientation="landscape" if "_landscape" in video.name else "portrait",
            status=Status.done,
            progress=1.0,
            output_path=vpath,
            size_bytes=video.stat().st_size,
            duration=_probe_duration(video),
        ))
        added_comps += 1
    if added_comps:
        s.commit()

    # 4. Delete orphaned .info.json sidecars (no matching .mp4).
    for info in settings.downloads_path.rglob("*.info.json"):
        mp4 = info.parent / (info.name[: -len(".info.json")] + ".mp4")
        if str(mp4) not in mp4_paths:
            info.unlink(missing_ok=True)

    # 5. Rebuild archive.txt to only reference ids of videos still on disk.
    _reconcile_archive(mp4_paths)

    if added_clips or added_comps or pruned_clips or pruned_comps:
        log("info", "rescan",
            f"+{added_clips} clips, +{added_comps} comps, -{pruned_clips} clips, -{pruned_comps} comps")
    return {
        "clips_added": added_clips, "compilations_added": added_comps,
        "clips_pruned": pruned_clips, "compilations_pruned": pruned_comps,
    }


def _reconcile_archive(mp4_paths: set[str]) -> None:
    """Keep only archive lines whose video id appears in an existing filename."""
    arc = settings.archive_path
    if not arc.exists():
        return
    names = " ".join(pathlib.Path(p).name for p in mp4_paths)
    try:
        kept = []
        for line in arc.read_text().splitlines():
            parts = line.split()
            vid = parts[-1] if parts else ""
            if vid and vid in names:
                kept.append(line)
        arc.write_text("\n".join(kept) + ("\n" if kept else ""))
    except Exception as exc:
        log("warning", "archive_reconcile_failed", str(exc))
