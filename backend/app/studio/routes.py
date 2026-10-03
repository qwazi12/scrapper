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
from .. import control, costs, undo
from . import runner, tmdb

# What a Studio undo restores (text/plan level; footage and shots are files).
UNDO_FIELDS = ["script", "plan", "render", "target_minutes"]

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
        "cost_usd": round(costs.ref_cost(f"studio:{p.id}"), 4),
        "has": {k: bool(getattr(p, k)) for k in ("facts", "research", "trailer", "shots", "script", "plan", "render")},
    }
    if full:
        d.update(facts=p.facts, research=p.research, trailer=p.trailer, shots=p.shots,
                 script=p.script, plan=p.plan, render=p.render)
    d["drive"] = p.drive
    d["archive"] = p.archive
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


@router.get("/imdb-videos")
def imdb_videos(imdb_id: str) -> dict[str, Any]:
    """Read-only check: which official videos IMDb lists for a title, and
    whether a direct MP4 link can be fetched from this server."""
    from . import imdb
    try:
        vids = imdb.list_videos(imdb_id)
        playable = bool(vids) and bool(imdb.mp4_url(vids[0]["id"]))
    except imdb.IMDbError as exc:
        raise HTTPException(502, str(exc))
    return {"videos": vids, "first_has_mp4": playable}


class MotionIn(BaseModel):
    mode: str | None = None          # off | compare | on
    cast_cards: int | None = None    # 0–4 name cards per video (default 2: the two leads)


def _motion_settings(s: Session) -> dict[str, Any]:
    from ..models import AppSetting
    from . import motion
    row = s.get(AppSetting, "studio_motion")
    v = (row.value or {}) if row else {}
    ok, why = motion.available()
    return {"mode": v.get("mode", "compare"), "cast_cards": int(v.get("cast_cards", 2)),
            "available": ok, "reason": why or None}


@router.get("/motion")
def get_motion(s: Session = Depends(get_session)) -> dict[str, Any]:
    return _motion_settings(s)


@router.put("/motion")
def put_motion(req: MotionIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """The motion-graphics switch. compare = render both looks for review (the
    published file stays the static one); on = motion look is the video; off = static only."""
    from ..models import AppSetting
    if req.mode is not None and req.mode not in ("off", "compare", "on"):
        raise HTTPException(400, "mode must be off, compare or on")
    if req.cast_cards is not None and not 0 <= req.cast_cards <= 4:
        raise HTTPException(400, "cast_cards must be 0–4")
    row = s.get(AppSetting, "studio_motion")
    before = dict((row.value or {}) if row else {})
    if row:
        undo.record(s, "settings", "Change motion-graphics settings", rows=[row], model="app_settings")
    else:
        undo.add_created(s, "settings", "Change motion-graphics settings", ["studio_motion"], model="app_settings")
    value = {**before, **{k: v for k, v in req.model_dump().items() if v is not None}}
    if row:
        row.value = value
    else:
        s.add(AppSetting(key="studio_motion", value=value))
    s.commit()
    logbus.log("info", "studio_motion", f"Motion graphics: mode={value.get('mode', 'compare')}, "
               f"cast cards={value.get('cast_cards', 2)} (applies to the next render)")
    return _motion_settings(s)


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
    what = "Edit script" if req.script is not None else "Swap shots" if req.plan is not None else "Change length"
    undo.record(s, f"studio:{project_id}", what, rows=[p], model="studio_projects", fields=UNDO_FIELDS)
    changed = []
    if req.target_minutes is not None:
        if not 1 <= req.target_minutes <= 10:
            raise HTTPException(400, "target_minutes must be 1–10")
        p.target_minutes = req.target_minutes
        changed.append("length")
    if req.script is not None:
        old = [x.get("text") for x in (p.script or {}).get("sentences", [])]
        new = [x.get("text") for x in req.script.get("sentences", [])]
        p.script = req.script
        if old != new:  # new words -> new voice timings -> old plan/render are stale
            p.plan = None
            p.render = None
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


class ArchiveIn(BaseModel):
    enabled: bool | None = None
    days: int | None = None


@router.get("/archive")
def archive_status() -> dict[str, Any]:
    """The 14-day rule's settings and a dry run: which breakdowns would be archived and why not."""
    from . import archive
    return archive.preview()


@router.put("/archive")
def archive_settings(req: ArchiveIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    from ..models import AppSetting
    from . import archive
    if req.days is not None and not 1 <= req.days <= 365:
        raise HTTPException(400, "days must be 1–365")
    row = s.get(AppSetting, "studio_archive")
    if row:
        undo.record(s, "settings", "Change breakdown archiving", rows=[row], model="app_settings")
    else:
        undo.add_created(s, "settings", "Change breakdown archiving", ["studio_archive"], model="app_settings")
    value = {**((row.value or {}) if row else {}), **{k: v for k, v in req.model_dump().items() if v is not None}}
    if row:
        row.value = value
    else:
        s.add(AppSetting(key="studio_archive", value=value))
    s.commit()
    logbus.log("info", "studio_archive_settings", f"Breakdown archiving: {value}")
    return archive.preview()


@router.post("/archive/run")
def archive_now() -> dict[str, Any]:
    from . import archive
    done = archive.sweep()
    return {"archived": done, **archive.preview()}


@router.post("/projects/{project_id}/restore")
def restore_footage(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    from . import archive
    p = _get(s, project_id)
    if not (p.archive or {}).get("archived_at"):
        raise HTTPException(400, "This breakdown isn't archived")
    return {"job_id": archive.start_restore(p.id, p.title)}


class ShotUse(BaseModel):
    usable: bool


@router.put("/projects/{project_id}/shots/{shot_id}")
def set_shot_usable(project_id: int, shot_id: str, req: ShotUse, s: Session = Depends(get_session)) -> dict[str, Any]:
    """The owner's call overrides the automatic one: any shot (title card, dark,
    text, untagged) can be used, and any shot can be left out. Undoable."""
    p = _get(s, project_id)
    if p.stage_status == "running":
        raise HTTPException(409, "A step is running; wait for it to finish")
    shots = [dict(x) for x in (p.shots or [])]
    sh = next((x for x in shots if x.get("id") == shot_id), None)
    if not sh:
        raise HTTPException(404, "shot not found")
    undo.record(s, f"studio:{p.id}", f"{'Use' if req.usable else 'Leave out'} shot {shot_id}", rows=[p],
                model="studio_projects", fields=["shots"])
    if "auto_usable" not in sh:
        sh["auto_usable"] = bool(sh.get("usable"))     # what the tagger decided, kept for reference
    sh["usable"] = req.usable
    sh["owner_set"] = True
    p.shots = shots
    s.commit()
    logbus.log("info", "studio_shot", f"Studio #{p.id}: shot {shot_id} {'in' if req.usable else 'out'} (owner)",
               project=p.id)
    return _out(p)


@router.post("/projects/{project_id}/publish")
def publish(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Send the rendered video to the Posting Queue (status Review, pipeline
    LongForm). The owner picks where it posts and approves it there."""
    from sqlalchemy import func
    from ..models import QueueItem

    p = _get(s, project_id)
    root = runner.project_dir(project_id)
    if not p.render or not (root / p.render["file"]).exists():
        raise HTTPException(400, "Render the video first")
    sc = p.script or {}
    tags = " ".join("#" + "".join(ch for ch in t if ch.isalnum()) for t in sc.get("tags", []) if t.strip())
    fields = dict(
        pipeline="LongForm", video_name=f"{p.title} — Trailer Breakdown",
        video_path=str(root / p.render["file"]),
        thumb_path=str(root / p.render["thumbnail"]) if p.render.get("thumbnail") else None,
        title=sc.get("youtube_title") or p.title, description=sc.get("description", ""), tags=tags,
        source="LongForm Studio",
    )
    item = s.get(QueueItem, p.queue_item_id) if p.queue_item_id else None
    if item and item.status in ("review", "ready", "retry", "error"):
        undo.record(s, "queue", f"Update '{p.title}' from LongForm Studio", rows=[item])
        for k, v in fields.items():          # re-publishing a re-render updates the same row
            setattr(item, k, v)
    else:
        item = QueueItem(**fields, status="review", accounts=[],
                         position=(s.query(func.max(QueueItem.position)).scalar() or 0) + 1)
        s.add(item)
        s.flush()
        undo.add_created(s, "queue", f"Add '{p.title}' from LongForm Studio", [item.id])
        p.queue_item_id = item.id
    s.commit()
    logbus.log("info", "studio_published", f"Studio #{p.id}: sent to Posting Queue as item #{item.id}",
               project=p.id, queue_item=item.id)
    # Every breakdown sent to the queue is also kept in Drive (unless this render is already there).
    drive_job = None
    if (p.drive or {}).get("rendered_at") != p.render.get("rendered_at") or (p.drive or {}).get("status") != "saved":
        drive_job = _start_drive(p)
    return {"queue_item_id": item.id, "status": item.status, "drive_job_id": drive_job}


def _start_drive(p: StudioProject) -> str | None:
    from . import drive_store

    if any(j.kind == "drive" for j in control.running("studio", p.id)):
        return None
    return drive_store.start(p.id, p.title)


@router.post("/projects/{project_id}/drive")
def save_to_drive(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Save (or re-save) the current render to the "LongForm Studio" Drive folder."""
    p = _get(s, project_id)
    if not p.render:
        raise HTTPException(400, "Render the video first")
    job_id = _start_drive(p)
    if not job_id:
        raise HTTPException(409, "Already saving to Drive")
    return {"job_id": job_id}


@router.get("/projects/{project_id}/file")
def project_file(project_id: int, path: str, s: Session = Depends(get_session)):
    """Serve a file from the project's folder (keyframes, audio, the render)."""
    _get(s, project_id)
    root = runner.project_dir(project_id).resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target)
