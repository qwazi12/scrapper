"""API routes for Top 5 & Top 10 Countdown Compilations."""

from __future__ import annotations

import logging
import pathlib
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("scrapper.compilations.routes")

from ...config import settings
from ...db import SessionLocal
from ...models import StudioProject
from ... import control
from . import curation
from . import stitch
from .stitch import resolve_project_render

router = APIRouter(prefix="/compilations", tags=["compilations"])


class CurateRequest(BaseModel):
    theme_id: str | None = None
    custom_query: str | None = None
    media_type: str = "movie"
    genre_id: int | None = None
    limit: int = 10


class StitchRequest(BaseModel):
    project_ids: list[int] = Field(..., description="Project IDs ordered lowest rank to #1")
    title: str = Field(..., description="Compilation countdown title")
    custom_intro: str = ""
    custom_outro: str = ""
    accounts: list[str] | None = None
    channel_name: str = "Screen Central"


@router.get("/themes")
def get_themes() -> list[dict[str, Any]]:
    """Return all curated countdown themes."""
    return curation.list_themes()


@router.post("/curate")
def curate_candidates(req: CurateRequest) -> list[dict[str, Any]]:
    """Find candidate titles for a countdown theme."""
    return curation.discover_theme_candidates(
        theme_id=req.theme_id,
        custom_query=req.custom_query,
        media_type=req.media_type,
        genre_id=req.genre_id,
        limit=req.limit,
    )


@router.get("/eligible-projects")
def get_eligible_projects() -> list[dict[str, Any]]:
    """List all rendered Studio projects that can be stitched into a countdown."""
    with SessionLocal() as s:
        projects = (
            s.query(StudioProject)
            .filter(StudioProject.render.isnot(None))
            .order_by(StudioProject.id.desc())
            .all()
        )

    out = []
    for p in projects:
        render = p.render or {}
        p_file = resolve_project_render(p.id, render)
        if not p_file:
            continue

        facts = p.facts or {}
        local = facts.get("local") or {}
        poster_url = f"/api/studio/projects/{p.id}/file?path=poster.jpg" if local.get("poster") else None
        thumb_url = f"/api/studio/projects/{p.id}/file?path=thumbnail.jpg"

        out.append({
            "id": p.id,
            "title": p.title,
            "media_type": p.media_type,
            "tmdb_id": p.tmdb_id,
            "seconds": render.get("seconds", 0.0),
            "rendered_at": render.get("rendered_at"),
            "poster_url": poster_url,
            "thumb_url": thumb_url,
            "facts": {
                "primary_date": facts.get("primary_date"),
                "genres": facts.get("genres", []),
                "tagline": facts.get("tagline", ""),
            },
        })
    return out


@router.post("/preview-chapters")
def preview_chapters(req: StitchRequest) -> dict[str, Any]:
    """Estimate countdown runtime and chapter timestamps without rendering."""
    with SessionLocal() as s:
        projects = []
        for pid in req.project_ids:
            p = s.get(StudioProject, pid)
            if not p or not p.render:
                raise HTTPException(400, f"Project #{pid} is not rendered")
            projects.append(p)

    total_count = len(projects)
    cur_time = 16.0  # ~16s Intro
    chapters = [{"time": 0.0, "time_formatted": "0:00", "title": "Intro & Preview"}]

    for idx, p in enumerate(projects):
        rank = total_count - idx
        chapters.append({
            "time": round(cur_time, 2),
            "time_formatted": stitch._format_time(cur_time),
            "title": f"#{rank}: {p.title}",
            "project_id": p.id,
        })
        cur_time += 2.5 + float(p.render.get("seconds", 120.0))

    chapters.append({
        "time": round(cur_time, 2),
        "time_formatted": stitch._format_time(cur_time),
        "title": "Outro & Final Thoughts",
    })
    cur_time += 16.0  # ~16s Outro

    return {
        "title": req.title,
        "total_seconds": round(cur_time, 2),
        "total_minutes": round(cur_time / 60, 2),
        "item_count": total_count,
        "chapters": chapters,
    }


@router.post("/stitch")
def stitch_compilation(req: StitchRequest) -> dict[str, Any]:
    """Stitch selected projects into a countdown compilation MP4."""
    try:
        result = stitch.stitch_countdown_compilation(
            project_ids=req.project_ids,
            title=req.title,
            custom_intro=req.custom_intro,
            custom_outro=req.custom_outro,
            accounts=req.accounts,
            channel_name=req.channel_name,
            send_to_queue=True,
        )
        # Record into deduplication ledger
        curation.record_compilation_made(req.title, req.project_ids)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Compilation stitch failed: {str(e)}")


from . import cinemeta
from . import engine
from . import fmhy


class ResearchRequest(BaseModel):
    topic: str
    format: str = "top10"  # top5, top10, top15
    custom_instructions: str = ""


@router.get("/cinemeta/top")
def get_cinemeta_top(media_type: str = "movie") -> list[dict[str, Any]]:
    """Fetch top trending titles from Stremio Cinemeta catalog without API keys."""
    if media_type == "tv" or media_type == "series":
        return cinemeta.get_top_series()
    return cinemeta.get_top_movies()


@router.get("/cinemeta/meta")
def get_cinemeta_meta(imdb_id: str, media_type: str = "movie") -> dict[str, Any]:
    """Fetch enriched metadata (cast, director, rating, trailers) from Cinemeta by IMDb ID."""
    if media_type == "tv" or media_type == "series":
        data = cinemeta.get_series_meta(imdb_id)
    else:
        data = cinemeta.get_movie_meta(imdb_id)
    if not data:
        raise HTTPException(404, f"No Cinemeta metadata found for {imdb_id}")
    return data


@router.get("/fmhy/resources")
def get_fmhy_resources() -> list[dict[str, Any]]:
    """Return curated video scraping, media tracking, and audio tools from FMHY."""
    return fmhy.get_curated_resources()


@router.get("/triggers")
def get_demand_triggers() -> list[dict[str, Any]]:
    """Return the 5 demand triggers: Trending, Calendar, Streaming, Debate, Evergreen."""
    return engine.DEMAND_TRIGGERS


@router.get("/topics")
def get_candidate_topics(trigger: str | None = None, refresh: bool = False) -> list[dict[str, Any]]:
    """STEP 1: Discover and score 15 candidate topics across the 5 demand triggers (cached for instant load)."""
    return engine.get_or_discover_topics(category_filter=trigger, force_refresh=refresh)


@router.post("/research")
def research_countdown(req: ResearchRequest) -> dict[str, Any]:
    """STEPS 2-5: Generate fully researched countdown rankings and retention script."""
    if not req.topic.strip():
        raise HTTPException(400, "Topic cannot be empty")
    fmt = req.format.lower()
    if fmt not in ("top5", "top10", "top15"):
        fmt = "top10"
    try:
        res = engine.generate_countdown(
            topic=req.topic.strip(),
            format_type=fmt,
            custom_instructions=req.custom_instructions.strip(),
        )
        # Automatically save researched countdown so searched items are always preserved and available!
        engine.save_countdown_item(res)
        return res
    except Exception as e:
        logger.error("Failed to generate countdown research: %s", e)
        raise HTTPException(500, f"Countdown research failed: {str(e)}")


@router.get("/saved")
def get_saved_countdowns() -> list[dict[str, Any]]:
    """List saved researched countdowns."""
    return engine.list_saved_countdowns()


@router.post("/saved")
def save_countdown(countdown: dict[str, Any]) -> dict[str, Any]:
    """Save a researched countdown list."""
    if not countdown or not countdown.get("topic"):
        raise HTTPException(400, "Invalid countdown payload")
    return engine.save_countdown_item(countdown)


@router.delete("/saved/{countdown_id}")
def delete_saved_countdown_route(countdown_id: str) -> dict[str, bool]:
    """Delete a saved researched countdown list."""
    ok = engine.delete_saved_countdown(countdown_id)
    return {"deleted": ok}


class AutoQueueCountdownRequest(BaseModel):
    countdown: dict[str, Any]
    target_count: int | None = None


@router.post("/auto-queue")
def auto_queue_countdown(req: AutoQueueCountdownRequest) -> dict[str, Any]:
    """Auto-queue a researched countdown for video production:
    1. Ensures each ranked title has a Studio breakdown project (creates and triggers gather if needed).
    2. Builds the complete YouTube master package (Hook, auto-chapters, streaming links, discussion question).
    3. Enqueues the master countdown compilation video into the Posting Queue (QueueItem) with status 'ready'.
    """
    try:
        return engine.auto_queue_researched_countdown(
            countdown=req.countdown,
            target_count=req.target_count,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Auto-queue countdown failed: %s", e)
        raise HTTPException(500, f"Auto-queue failed: {str(e)}")



