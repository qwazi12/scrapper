"""LongForm Studio API (mounted under /api/studio)."""

from __future__ import annotations

import pathlib
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import logbus
from ..auth import require_token
from ..config import settings
from ..db import get_session
from ..models import StudioProject
from . import runner, tmdb

router = APIRouter(prefix="/api/studio", dependencies=[Depends(require_token)])


class ProjectCreate(BaseModel):
    tmdb_id: int
    media_type: str = "movie"
    title: str = ""
    target_minutes: float = 3.0


class ProjectPatch(BaseModel):
    target_minutes: float | None = None
    script: dict | None = None     # owner edits to the script (sentences, metadata)
    plan: list | None = None       # owner swaps shots


class RunStage(BaseModel):
    stage: str
    auto: bool = False             # also run the following stages up to the script


def _out(p: StudioProject, full: bool = True) -> dict[str, Any]:
    d = {
        "id": p.id, "tmdb_id": p.tmdb_id, "media_type": p.media_type, "title": p.title,
        "target_minutes": p.target_minutes, "stage": p.stage, "stage_status": p.stage_status,
        "stage_message": p.stage_message, "queue_item_id": p.queue_item_id,
        "created_at": p.created_at, "updated_at": p.updated_at,
        "poster": (p.facts or {}).get("poster"),
        "has": {k: bool(getattr(p, k)) for k in ("facts", "research", "trailer", "shots", "script", "plan", "render")},
    }
    if full:
        d.update(facts=p.facts, research=p.research, trailer=p.trailer, shots=p.shots,
                 script=p.script, plan=p.plan, render=p.render)
    return d


@router.get("/status")
def status() -> dict[str, Any]:
    return {
        "tmdb": tmdb.configured(),
        "gemini": bool(settings.gemini_api_key),
        "tts": bool(settings.tts_api_key),
        "voice": settings.tts_voice,
        "region": settings.studio_region,
        "stages": runner.ORDER,
        "busy": runner.busy(),
        "attribution": tmdb.ATTRIBUTION,
    }


def _tmdb_call(fn, *args):
    try:
        return fn(*args)
    except tmdb.TMDBError as exc:
        raise HTTPException(exc.status_code if exc.status_code < 500 else 502, exc.message)


@router.get("/calendar")
def calendar() -> dict[str, Any]:
    return _tmdb_call(tmdb.calendar)


@router.get("/search")
def search(q: str) -> list[dict[str, Any]]:
    if not q.strip():
        return []
    return _tmdb_call(tmdb.search, q.strip())


@router.get("/projects")
def list_projects(s: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [_out(p, full=False) for p in s.query(StudioProject).order_by(desc(StudioProject.id)).all()]


@router.post("/projects")
def create_project(req: ProjectCreate, s: Session = Depends(get_session)) -> dict[str, Any]:
    if req.media_type not in ("movie", "tv"):
        raise HTTPException(400, "media_type must be movie or tv")
    if not 1 <= req.target_minutes <= 10:
        raise HTTPException(400, "target_minutes must be 1–10")
    p = StudioProject(tmdb_id=req.tmdb_id, media_type=req.media_type, title=req.title,
                      target_minutes=req.target_minutes)
    s.add(p)
    s.commit()
    s.refresh(p)
    logbus.log("info", "studio_created", f"Studio #{p.id}: {p.title or p.tmdb_id}", project=p.id)
    return _out(p)


def _get(s: Session, project_id: int) -> StudioProject:
    p = s.get(StudioProject, project_id)
    if not p:
        raise HTTPException(404, "studio project not found")
    return p


@router.get("/projects/{project_id}")
def get_project(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    return _out(_get(s, project_id))


@router.patch("/projects/{project_id}")
def patch_project(project_id: int, req: ProjectPatch, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get(s, project_id)
    if p.stage_status == "running":
        raise HTTPException(409, "a stage is running on this project; wait for it to finish")
    changed = []
    if req.target_minutes is not None:
        if not 1 <= req.target_minutes <= 10:
            raise HTTPException(400, "target_minutes must be 1–10")
        p.target_minutes = req.target_minutes
        changed.append("length")
    if req.script is not None:
        p.script = req.script
        changed.append("script")
    if req.plan is not None:
        p.plan = req.plan
        changed.append("shot plan")
    s.commit()
    s.refresh(p)
    if changed:
        logbus.log("info", "studio_edited", f"Studio #{p.id}: edited {', '.join(changed)}", project=p.id)
    return _out(p)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get(s, project_id)
    if p.stage_status == "running":
        raise HTTPException(409, "a stage is running on this project")
    import shutil
    shutil.rmtree(runner.project_dir(project_id), ignore_errors=True)
    s.delete(p)
    s.commit()
    logbus.log("info", "studio_deleted", f"Studio #{project_id} deleted (files removed)", project=project_id)
    return {"deleted": project_id}


@router.post("/projects/{project_id}/run")
def run_stage(project_id: int, req: RunStage, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get(s, project_id)
    try:
        runner.start(p.id, req.stage, auto=req.auto)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True, "stage": req.stage, "auto": req.auto}


@router.post("/projects/{project_id}/trailer-upload")
async def upload_trailer(project_id: int, file: UploadFile = File(...),
                         s: Session = Depends(get_session)) -> dict[str, Any]:
    """Manual fallback: the owner uploads the trailer file themselves."""
    p = _get(s, project_id)
    dest = runner.project_dir(project_id) / "trailer.mp4"
    size = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            fh.write(chunk)
            size += len(chunk)
    if size == 0:
        raise HTTPException(400, "empty upload")
    p.trailer = {"file": str(dest), "origin": "upload", "name": file.filename, "size": size}
    p.shots = None  # shots belong to the old trailer
    s.commit()
    logbus.log("info", "studio_trailer_upload", f"Studio #{p.id}: trailer uploaded ({round(size / 1e6)} MB)",
               project=p.id)
    return _out(p)


@router.get("/projects/{project_id}/file")
def project_file(project_id: int, path: str, s: Session = Depends(get_session)):
    """Serve a file from the project's folder (keyframes, audio, the render)."""
    _get(s, project_id)
    root = runner.project_dir(project_id).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target)
