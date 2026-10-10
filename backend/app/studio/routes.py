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
    force: bool = False            # owner confirmed going past today's limit


class ProjectPatch(BaseModel):
    target_minutes: float | None = None
    script: dict | None = None     # owner edits to the script (sentences, metadata)
    plan: list | None = None       # owner swaps shots


class RunStage(BaseModel):
    stage: str
    auto: bool = False             # also run the following stages up to `until`
    until: str = "script"          # "plan" = steps up to 5 (what the automation runs)


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
    d["review"] = p.review
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
    from . import auto, candidates
    if not req.force and f"{req.media_type}:{req.tmdb_id}" in candidates.made_keys(s):
        raise HTTPException(409, "already_made: A breakdown of this title was already made. Make another one anyway?")
    over = auto.over_limit(s, req.media_type)
    if over and not req.force:
        raise HTTPException(409, f"daily_limit: {over} Start it anyway?")
    p = StudioProject(tmdb_id=req.tmdb_id, media_type=req.media_type, title=req.title,
                      target_minutes=req.target_minutes)
    candidates.remember_made(s, req.media_type, req.tmdb_id)
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


ACTIVE_QUEUE = ("review", "ready", "retry", "error", "posting")
ARCHIVE_DAYS = 5   # owner: an archived breakdown is deleted 5 days later


def _queue_block(s: Session, p: StudioProject) -> str | None:
    """Why deleting would break a post that hasn't gone out yet, or None."""
    from ..models import QueueItem
    item = s.get(QueueItem, p.queue_item_id) if p.queue_item_id else None
    if item and item.status in ACTIVE_QUEUE:
        return f"it is in the Posting Queue as #{item.id} (status {item.status}) and hasn't posted"
    return None


def delete_project_now(s: Session, p: StudioProject, reason: str) -> None:
    """Remove the project, its files and (if it never posted) its queue row.
    The Drive copy stays in Drive. The title stays in the never-repeat ledger."""
    import shutil
    from ..models import QueueItem
    from . import candidates
    pid = p.id
    candidates.remember_made(s, p.media_type, p.tmdb_id)   # deleted ≠ eligible again
    if p.stage_status == "queued":
        runner.stop(pid)                       # take it out of the waiting line
    item = s.get(QueueItem, p.queue_item_id) if p.queue_item_id else None
    if item and item.status in ACTIVE_QUEUE:
        s.delete(item)
    shutil.rmtree(runner.project_dir(pid), ignore_errors=True)
    s.delete(p)
    s.commit()
    logbus.log("info", "studio_deleted", f"Studio #{pid} deleted ({reason}; files removed, Drive copy kept)",
               project=pid)


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, force: bool = False, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get(s, project_id)
    if p.stage_status == "running":
        raise HTTPException(409, "a step is running on this project — Stop it first")
    block = _queue_block(s, p)
    if block and not force:
        raise HTTPException(409, f"in_queue: {block}. Deleting also removes it from the queue.")
    delete_project_now(s, p, "deleted by owner")
    return {"deleted": project_id}


class ProjectArchiveIn(BaseModel):
    archived: bool


@router.put("/projects/{project_id}/archive")
def archive_project(project_id: int, req: ProjectArchiveIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Archive = delete automatically ARCHIVE_DAYS days later (undo any time before)."""
    import datetime
    p = _get(s, project_id)
    rv = dict(p.review or {})
    if req.archived:
        if p.stage_status in ("running", "queued"):
            raise HTTPException(409, f"this project is {p.stage_status} — Stop it first")
        now = datetime.datetime.now(datetime.timezone.utc)
        rv["archived_at"] = now.isoformat()
        rv["delete_after"] = (now + datetime.timedelta(days=ARCHIVE_DAYS)).isoformat()
    else:
        rv.pop("archived_at", None)
        rv.pop("delete_after", None)
    p.review = rv
    s.commit()
    logbus.log("info", "studio_archived" if req.archived else "studio_unarchived",
               f"Studio #{p.id}: " + (f"archived — deleted automatically after {rv['delete_after'][:10]}"
                                       if req.archived else "un-archived (won't be deleted)"), project=p.id)
    return _out(p, full=False)


def sweep_archived(now=None) -> list[int]:
    """Scheduler: delete archived projects whose 5 days are up. A project whose
    post hasn't gone out yet, or that is busy, waits (and says why)."""
    import datetime
    from ..db import SessionLocal
    now = now or datetime.datetime.now(datetime.timezone.utc)
    done = []
    with SessionLocal() as s:
        for p in s.query(StudioProject).filter(StudioProject.review.isnot(None)).all():
            due = (p.review or {}).get("delete_after")
            if not due or datetime.datetime.fromisoformat(due) > now:
                continue
            if p.stage_status in ("running", "queued") or _queue_block(s, p):
                continue
            delete_project_now(s, p, "archived 5 days ago")
            done.append(p.id)
    return done


@router.post("/projects/{project_id}/run")
def run_stage(project_id: int, req: RunStage, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get(s, project_id)
    if p.stage_status in ("running", "queued"):
        raise HTTPException(409, f"this project is already {p.stage_status}")
    try:
        state = runner.start_or_queue(p.id, req.stage, auto=req.auto, until=req.until)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "stage": req.stage, "auto": req.auto, "state": state}


@router.post("/recompute-usable")
def recompute_usable_all(s: Session = Depends(get_session)) -> dict[str, Any]:
    """Apply the current usability rule to every project's saved shot tags (no AI cost)."""
    from . import stage_shots
    out = {}
    for (pid,) in s.query(StudioProject.id).all():
        b, a = stage_shots.recompute_usable(pid)
        if b != a:
            out[pid] = {"before": b, "after": a}
    logbus.log("info", "studio_usable_recomputed", f"Shot usability re-applied (cards only): {out or 'no changes'}")
    return {"changed": out}


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """▶ Resume a stopped / failed / paused project from the step it was on,
    on through step 5 (or through render if it stopped while rendering)."""
    p = _get(s, project_id)
    if p.stage_status not in ("stopped", "error", "paused") or p.stage in ("new", None):
        raise HTTPException(409, f"nothing to resume (status {p.stage_status})")
    until = "render" if p.stage == "render" else "plan"
    stage = p.stage if p.stage in runner.ORDER else "gather"
    if runner.ORDER.index(stage) > runner.ORDER.index(until):
        until = stage
    state = runner.start_or_queue(p.id, stage, auto=True, until=until)
    logbus.log("info", "studio_resumed", f"Studio #{p.id}: resumed at {stage} ({state})", project=p.id)
    return {"ok": True, "stage": stage, "until": until, "state": state}


@router.post("/projects/{project_id}/move-up")
def move_up(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    p = _get(s, project_id)
    if p.stage_status != "queued" or not runner.move_to_front(p.id):
        raise HTTPException(409, "this project isn't waiting in line")
    logbus.log("info", "studio_moved_up", f"Studio #{p.id}: moved to the front of Studio's line", project=p.id)
    return {"ok": True}


@router.post("/projects/{project_id}/stop")
def stop_project(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Stop all running work on a studio project immediately."""
    p = _get(s, project_id)
    runner.stop(project_id)
    s.refresh(p)
    return _out(p)


class ThumbnailSelect(BaseModel):
    id: str


@router.post("/projects/{project_id}/thumbnail/select")
def select_thumbnail(project_id: int, req: ThumbnailSelect, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Choose which of the 3 thumbnail options to use."""
    from . import stage_render
    p = _get(s, project_id)
    try:
        stage_render.select_project_thumbnail(project_id, req.id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    s.refresh(p)
    return _out(p)


@router.post("/projects/{project_id}/thumbnail/generate")
def generate_thumbnails(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Generate or refresh the 3 thumbnail options for this project."""
    from . import stage_render
    p = _get(s, project_id)
    try:
        stage_render.generate_thumbnails_for_project(project_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    s.refresh(p)
    return _out(p)


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


LONGFORM_DESTINATION = ["default:*"]   # Upload-Post profile "default" — all channels (Screen Central)


@router.post("/projects/{project_id}/publish")
def publish(project_id: int, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Send the rendered video to the Posting Queue as Ready to Post (pipeline
    LongForm, profile "default" = Screen Central); it takes the next LongForm slot."""
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
        if not item.accounts:
            item.accounts = list(LONGFORM_DESTINATION)
        item.status = "ready"
    else:
        # Owner (2026-10-04): a sent breakdown is approved by sending it — straight to
        # Ready to Post on profile "default" (Screen Central), next LongForm slot.
        item = QueueItem(**fields, status="ready", accounts=list(LONGFORM_DESTINATION),
                         position=(s.query(func.max(QueueItem.position)).scalar() or 0) + 1)
        s.add(item)
        s.flush()
        undo.add_created(s, "queue", f"Add '{p.title}' from LongForm Studio", [item.id])
        p.queue_item_id = item.id
    s.commit()
    logbus.log("info", "studio_published", f"Studio #{p.id}: sent to Posting Queue as item #{item.id} (ready, default)",
               project=p.id, queue_item=item.id)
    from ..social import queue_manager
    queue_manager.wake()                      # give it its slot now, not at the next 5-min tick
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


# --- candidates, automation, batches -------------------------------------------
class MarkIn(BaseModel):
    mark: str | None = None        # pin | skip | None (clear)


class AutoIn(BaseModel):
    enabled: bool | None = None
    movies_per_day: int | None = None
    tv_per_day: int | None = None


class ReviewIn(BaseModel):
    reviewed: bool


class IdsIn(BaseModel):
    ids: list[int]


@router.get("/candidates")
def get_candidates() -> dict[str, Any]:
    from . import candidates
    if candidates.is_stale() and not candidates.state["refreshing"]:
        candidates.refresh_async()
    return candidates.listing()


@router.post("/candidates/refresh")
def refresh_candidates() -> dict[str, Any]:
    from . import candidates
    if not tmdb.configured():
        raise HTTPException(400, "TMDB_API_KEY is not set on the server")
    started = candidates.refresh_async()
    return {"started": started, "refreshing": True}


@router.put("/candidates/{key}/mark")
def mark_candidate(key: str, req: MarkIn) -> dict[str, Any]:
    from . import candidates
    try:
        return candidates.set_mark(key, req.mark or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/auto")
def get_auto() -> dict[str, Any]:
    from . import auto
    return auto.status()


@router.put("/auto")
def put_auto(req: AutoIn) -> dict[str, Any]:
    from . import auto
    try:
        auto.save_settings(req.model_dump())
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    if req.enabled:
        from ..social import queue_manager
        queue_manager.wake()  # first pick within seconds, not at the next 5-min tick
    return auto.status()


class PauseIn(BaseModel):
    paused: bool


@router.post("/auto/pause-today")
def auto_pause_today(req: PauseIn) -> dict[str, Any]:
    from . import auto
    return auto.pause_today(req.paused)


@router.post("/auto/skip-next")
def auto_skip_next() -> dict[str, Any]:
    from . import auto
    try:
        return auto.skip_next()
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.put("/projects/{project_id}/review")
def set_reviewed(project_id: int, req: ReviewIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    import datetime
    p = _get(s, project_id)
    rv = dict(p.review or {})
    rv["reviewed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat() if req.reviewed else None
    p.review = rv
    s.commit()
    logbus.log("info", "studio_reviewed", f"Studio #{p.id}: marked {'reviewed' if req.reviewed else 'not reviewed'}",
               project=p.id)
    return _out(p, full=False)


@router.get("/render-batch")
def render_batch_status() -> dict[str, Any]:
    from . import batch
    return batch.public()


@router.post("/render-batch")
def render_batch(req: IdsIn) -> dict[str, Any]:
    from . import batch
    try:
        out = batch.start(req.ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    logbus.log("info", "studio_render_batch_started", f"Render batch started: {len(req.ids)} breakdown(s)")
    return out


@router.post("/publish-batch")
def publish_batch(req: IdsIn, s: Session = Depends(get_session)) -> dict[str, Any]:
    """Send each rendered breakdown to the Posting Queue (same as the single
    button); one result per project, failures don't stop the others."""
    results = []
    for pid in list(dict.fromkeys(req.ids))[:50]:
        title = (s.get(StudioProject, pid).title if s.get(StudioProject, pid) else f"#{pid}")
        try:
            r = publish(pid, s)
            results.append({"id": pid, "title": title, "ok": True, "queue_item_id": r["queue_item_id"]})
        except HTTPException as exc:
            results.append({"id": pid, "title": title, "ok": False, "why": str(exc.detail)})
    ok = sum(1 for r in results if r["ok"])
    logbus.log("info", "studio_publish_batch", f"Sent {ok} of {len(results)} breakdown(s) to the Posting Queue")
    return {"results": results, "sent": ok}


from .compilations.routes import router as compilations_router
router.include_router(compilations_router)

