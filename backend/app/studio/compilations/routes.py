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
