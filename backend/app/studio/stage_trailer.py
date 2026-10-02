"""Stage: trailer — collect the footage pool for one title.

Order of preference (owner: avoid YouTube):
  1. A file the owner uploaded (kept as is).
  2. IMDb: the newest official trailer + extra official clips/teasers/promos
     (more distinct shots to rotate through), capped by total length.
  3. The TMDB official YouTube trailer via the home Mac worker (YouTube blocks
     the server). The stage hands it to the worker and says so; run the stage
     again once the worker has uploaded it.
"""

from __future__ import annotations

import pathlib

from ..db import SessionLocal
from ..models import Clip, StudioProject, Status
from ...core.scraper import platform_of
from .. import control
from . import imdb
from .runner import project_dir, stage

EXTRA_TYPES = ("Clip", "Teaser", "Promo", "Featurette", "Trailer")
MAX_EXTRA_SECONDS = 300   # extra footage beyond the main trailer
MAX_FILES = 6


def pick_videos(videos: list[dict]) -> list[dict]:
    """Main trailer first (official 'Trailer', longest-newest), then extras."""
    trailers = [v for v in videos if v["type"] == "Trailer"]
    if not trailers:
        return []
    main = trailers[0]  # IMDb lists the featured/newest first
    chosen, total = [main], 0
    for v in videos:
        if v is main or v["type"] not in EXTRA_TYPES or not v.get("seconds"):
            continue
        if total + v["seconds"] > MAX_EXTRA_SECONDS or len(chosen) >= MAX_FILES:
            continue
        chosen.append(v)
        total += v["seconds"]
    return chosen


def _from_worker(project_id: int, facts: dict, trailer: dict | None) -> tuple[dict, str]:
    """Hand the YouTube trailer to the home worker, or collect what it fetched."""
    with SessionLocal() as s:
        cid = (trailer or {}).get("pending_clip_id")
        if cid:
            clip = s.get(Clip, cid)
            if clip and clip.file_path and pathlib.Path(clip.file_path).exists():
                main = {"id": f"yt-clip-{cid}", "name": "Official Trailer (YouTube)", "type": "Trailer",
                        "origin": "youtube-worker", "file": clip.file_path, "page": clip.source_url}
                return {"file": clip.file_path, "origin": "youtube-worker", "sources": [main]}, \
                    "trailer received from the home worker"
            return trailer, "still waiting for the home Mac worker to download the YouTube trailer"
        yt = next((v for v in facts.get("videos", []) if v["site"] == "YouTube" and v["type"] == "Trailer"), None)
        if not yt:
            raise RuntimeError("No trailer found on IMDb or TMDB — upload one with 'Upload trailer'")
        clip = Clip(source_url=yt["url"], platform=platform_of(yt["url"]), status=Status.failed,
                    error="LongForm Studio trailer — queued for the home Mac worker", selected=False)
        s.add(clip)
        s.commit()
        return {"pending_clip_id": clip.id, "origin": "youtube-worker", "page": yt["url"]}, \
            f"no IMDb trailer; YouTube trailer queued for the home Mac worker (clip #{clip.id}) — run again once it arrives"


@stage("trailer")
def trailer(project_id: int) -> str:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        facts, current = p.facts, p.trailer
    if not facts:
        raise RuntimeError("Run 'gather' first")
    if current and current.get("origin") == "upload" and pathlib.Path(current.get("file", "")).exists():
        return "using the uploaded trailer"

    footage = project_dir(project_id) / "footage"
    footage.mkdir(exist_ok=True)
    result: dict | None = None
    note = ""
    if facts.get("imdb_id"):
        try:
            chosen = pick_videos(imdb.list_videos(facts["imdb_id"]))
            got = []
            for v in chosen:
                control.check()
                control.progress(f"downloading {v.get('type')} {v['id']}")
                dest = footage / f"{v['id']}.mp4"
                try:
                    if not dest.exists() or dest.stat().st_size == 0:
                        imdb.download(v["id"], dest)
                    got.append({**v, "file": str(dest)})
                except imdb.IMDbError as exc:
                    note += f" {v['id']}: {exc}."
                    dest.unlink(missing_ok=True)
            if got and got[0]["type"] == "Trailer":
                result = {"file": got[0]["file"], "origin": "imdb", "sources": got}
        except imdb.IMDbError as exc:
            note = f" IMDb unavailable: {exc}."

    if result is None:
        result, msg = _from_worker(project_id, facts, current)
        note = msg + note
    else:
        extra = len(result["sources"]) - 1
        note = f"IMDb trailer + {extra} extra official video(s)" + note

    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        if p.trailer != result:
            p.shots = None  # new footage -> old shot list is stale
        p.trailer = result
        s.commit()
    return note.strip()
