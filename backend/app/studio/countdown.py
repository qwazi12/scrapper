"""Countdowns Studio: Top 5 / 10 / 15 videos built like a LongForm breakdown.

Owner (2026-10-12): "keep what it's meant to do but have the whole set up and
interface like LongForm. Make it its own thing and give it its own posting tab."

A countdown is a CountdownProject that goes through five steps:
  1 research  — the AI ranks titles for the topic (background job, paid)
  2 titles    — the owner reviews the ranking; every entry must be a real TMDB
                title; confirming creates/reuses one Studio segment per title
  3 segments  — each title's segment is made through render, one at a time and
                only when Studio is idle (LongForm breakdowns go first)
  4 script    — title, intro hook, outro question (no AI; editable)
  5 render    — stitch the video in a background job; then Send to the
                Countdowns queue (Ready to Post on Screen Central, like LongForm)
"""

from __future__ import annotations

import datetime
import logging
import shutil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import control, costs, logbus
from ..auth import require_token
from ..db import SessionLocal
from ..models import AppSetting, CountdownProject, QueueItem, StudioProject

logger = logging.getLogger("scrapper.countdown")
UTC = datetime.timezone.utc

PIPELINE = "Countdowns"
SOURCE = "Countdowns Studio"
DESTINATION = ["default:*"]                     # Screen Central, like LongForm
STAGES = ["research", "titles", "segments", "script", "render"]
COUNTS = {"top5": 5, "top10": 10, "top15": 15}
# Segment length per title so the whole video lands around 8–12 minutes.
SEGMENT_MINUTES = {"top5": 1.6, "top10": 0.9, "top15": 0.6}
ARCHIVE_DAYS = 5

router = APIRouter(prefix="/api/countdowns", dependencies=[Depends(require_token)])


# --- helpers ---------------------------------------------------------------------
def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


def _get(s, cid: int) -> CountdownProject:
    c = s.get(CountdownProject, cid)
    if c is None:
        raise HTTPException(404, "countdown not found")
    return c


def _set(cid: int, **kw) -> None:
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        if c is None:
            return
        for k, v in kw.items():
            setattr(c, k, v)
        s.commit()


def entries_of(c: CountdownProject) -> list[dict[str, Any]]:
    return list((c.ranking or {}).get("entries") or [])


def _ordered(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lowest rank first (#10 … #1): the order the video plays."""
    return sorted(entries, key=lambda e: int(e.get("rank") or 0), reverse=True)


def _segments(s, c: CountdownProject) -> list[dict[str, Any]]:
    from .compilations.stitch import resolve_project_render
    out = []
    by_id = {int((e.get("segment_id") or 0)): e for e in entries_of(c)}
    for pid in c.segment_ids or []:
        p = s.get(StudioProject, int(pid))
        e = by_id.get(int(pid), {})
        if p is None:
            out.append({"id": pid, "title": e.get("title", f"#{pid}"), "rank": e.get("rank"), "missing": True,
                        "stage": "gone", "stage_status": "error", "rendered": False})
            continue
        out.append({
            "id": p.id, "title": p.title, "rank": e.get("rank"), "poster": (p.facts or {}).get("poster") or e.get("poster"),
            "stage": p.stage, "stage_status": p.stage_status, "stage_message": p.stage_message,
            "rendered": bool(resolve_project_render(p.id, p.render)),
            "seconds": (p.render or {}).get("seconds", 0), "reused": bool(e.get("reused")),
        })
    return out


def _cost(c: CountdownProject) -> float:
    total = costs.ref_cost(f"countdown:{c.id}")
    for pid in c.segment_ids or []:
        total += costs.ref_cost(f"studio:{pid}")
    return round(total, 4)


def out(s, c: CountdownProject, full: bool = True) -> dict[str, Any]:
    segs = _segments(s, c) if c.segment_ids else []
    made = sum(1 for x in segs if x["rendered"])
    entries = entries_of(c)
    unmatched = [e for e in entries if not e.get("tmdb_id")]
    est = sum((x["seconds"] or SEGMENT_MINUTES.get(c.fmt, 1) * 60) for x in segs) if segs else \
        len(entries) * SEGMENT_MINUTES.get(c.fmt, 1) * 60
    posters = [e.get("poster") for e in _ordered(entries) if e.get("poster")][-4:]
    d = {
        "id": c.id, "topic": c.topic, "fmt": c.fmt, "count": COUNTS.get(c.fmt, len(entries)),
        "stage": c.stage, "stage_status": c.stage_status, "stage_message": c.stage_message,
        "title": (c.script or {}).get("title") or ((c.ranking or {}).get("title_options") or [c.topic])[0],
        "queue_item_id": c.queue_item_id, "review": c.review or {}, "created_at": c.created_at, "updated_at": c.updated_at,
        "posters": posters, "entry_count": len(entries), "unmatched": len(unmatched),
        "segments_made": made, "segments_total": len(segs),
        "est_minutes": round((est + 40) / 60, 1),                     # + intro/outro and the #N cards
        "cost_usd": _cost(c),
        "has": {"ranking": bool(entries), "titles": bool(c.segment_ids), "segments": bool(segs) and made == len(segs),
                "script": bool(c.script), "render": bool(c.render)},
        "render": c.render,
    }
    if c.queue_item_id:
        q = s.get(QueueItem, c.queue_item_id)
        d["queue_status"] = q.status if q else None
    if full:
        d.update(ranking=c.ranking, notes=c.notes, script=c.script, segments=segs)
    return d


def _running(c: CountdownProject) -> bool:
    return c.stage_status == "running"


# --- 1. research -------------------------------------------------------------------
def research_job(cid: int) -> str:
    from .compilations import engine
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        topic, fmt, notes = c.topic, c.fmt, c.notes or ""
    _set(cid, stage="research", stage_status="running", stage_message="Researching and ranking titles…")
    try:
        control.check()
        with costs.operation("countdown_research", ref=f"countdown:{cid}"):
            data = engine.generate_countdown(topic=topic, format_type=fmt, custom_instructions=notes)
        control.check()
        entries = []
        for e in data.get("entries") or []:
            r = engine._resolve_title(e) if not e.get("tmdb_id") else \
                {"tmdb_id": e["tmdb_id"], "media_type": e.get("media_type") or "movie"}
            e["tmdb_id"] = (r or {}).get("tmdb_id")
            e["media_type"] = (r or {}).get("media_type") or e.get("media_type") or "movie"
            entries.append(e)
        data["entries"] = entries
        bad = [e for e in entries if not e.get("tmdb_id")]
        msg = (f"Ranked {len(entries)} titles. " +
               (f"{len(bad)} aren't matched to a movie or show — swap or remove them in step 2."
                if bad else "Check the ranking in step 2, then confirm the titles."))
        _set(cid, ranking=data, stage="research", stage_status="done", stage_message=msg)
        logbus.log("info", "countdown_researched", f"Countdown #{cid}: {msg}")
        return msg
    except control.Cancelled:
        _set(cid, stage_status="stopped", stage_message="Research stopped")
        raise
    except Exception as exc:  # noqa: BLE001 — shown on the card
        _set(cid, stage_status="error", stage_message=f"Research failed: {str(exc)[:300]}")
        raise


def start_research(cid: int) -> str:
    return control.start_thread("countdown", f"Countdown #{cid}: research", "countdown", research_job, cid, ref=cid)


# --- 2. titles ---------------------------------------------------------------------
def save_entries(c: CountdownProject, entries: list[dict[str, Any]]) -> None:
    """Owner's edit of the ranking (reorder / remove / swap). Ranks follow the list
    order: the first entry is #1. Changing titles after segments exist resets step 3."""
    if not entries:
        raise HTTPException(400, "A countdown needs at least 2 titles")
    clean = []
    for i, e in enumerate(entries):
        e = dict(e)
        e["rank"] = i + 1
        clean.append(e)
    r = dict(c.ranking or {})
    old_ids = [(e.get("tmdb_id"), e.get("media_type")) for e in _ordered(entries_of(c))]
    r["entries"] = clean
    c.ranking = r
    new_ids = [(e.get("tmdb_id"), e.get("media_type")) for e in _ordered(clean)]
    if c.segment_ids and old_ids != new_ids:
        c.segment_ids = None                   # titles changed: confirm them again
        c.render = None
        c.stage, c.stage_status = "research", "done"
        c.stage_message = "Titles changed — confirm them again (step 2)."


def confirm_titles(s, c: CountdownProject) -> None:
    """Create or reuse one Studio segment per title (lowest rank first)."""
    entries = entries_of(c)
    bad = [f"#{e.get('rank')} {e.get('title')}" for e in entries if not e.get("tmdb_id")]
    if bad:
        raise HTTPException(400, "These aren't matched to a movie or show (no trailer to use): "
                                 + ", ".join(bad) + ". Swap or remove them first.")
    if len(entries) < 2:
        raise HTTPException(400, "A countdown needs at least 2 titles")
    minutes = SEGMENT_MINUTES.get(c.fmt, 1.0)
    total = len(entries)
    ids = []
    for e in _ordered(entries):
        ctx = {"rank": e.get("rank"), "total": total, "topic": c.topic, "why_it_ranks": e.get("why_it_ranks"),
               "fun_fact": e.get("fun_fact"), "key_stat": e.get("key_stat"), "where_to_watch": e.get("where_to_watch")}
        p = (s.query(StudioProject)
             .filter(StudioProject.tmdb_id == int(e["tmdb_id"]), StudioProject.media_type == e.get("media_type", "movie"))
             .order_by(StudioProject.id.desc()).first())
        if p is not None and (p.queue_item_id or (p.review or {}).get("auto")):
            e["reused"] = True                 # a real LongForm breakdown: used as-is, never changed
        elif p is not None:
            f = dict(p.facts or {})
            f.update(origin="countdown", is_countdown=True, countdown=ctx)
            p.facts = f
        else:
            p = StudioProject(tmdb_id=int(e["tmdb_id"]), media_type=e.get("media_type", "movie"),
                              title=e.get("title") or "", target_minutes=minutes,
                              facts={"origin": "countdown", "is_countdown": True, "countdown": ctx})
            s.add(p)
            s.flush()
        e["segment_id"] = p.id
        ids.append(p.id)
    r = dict(c.ranking or {})
    r["entries"] = entries
    c.ranking = r
    c.segment_ids = ids
    if not c.script:
        c.script = default_script(c)
    c.stage, c.stage_status = "segments", "waiting"
    c.stage_message = f"Titles confirmed. Segments are made one at a time when Studio is free (0 of {len(ids)})."


# --- 3. segments (scheduler tick) ----------------------------------------------------
def _next_stage(p: StudioProject) -> str:
    from . import runner
    if p.stage_status == "done" and p.stage in runner.ORDER:
        return runner.ORDER[min(runner.ORDER.index(p.stage) + 1, len(runner.ORDER) - 1)]
    return p.stage if p.stage in runner.ORDER else "gather"


def tick() -> dict[str, Any] | None:
    """Advance every countdown whose segments are being made. Starts at most one
    Studio step per tick, and only when Studio is idle with nobody waiting."""
    from . import runner
    did = None
    with SessionLocal() as s:
        for c in s.query(CountdownProject).filter(CountdownProject.stage == "segments",
                                                  CountdownProject.stage_status.in_(["waiting", "running"])) \
                .order_by(CountdownProject.id).all():
            segs = _segments(s, c)
            made = sum(1 for x in segs if x["rendered"])
            failed = next((x for x in segs if not x["rendered"] and x["stage_status"] in ("error", "stopped")), None)
            if failed:
                c.stage_status = "error"
                c.stage_message = (f"Segment “{failed['title']}” {failed['stage_status']} at step '{failed['stage']}'"
                                   + (f": {failed.get('stage_message')}" if failed.get("stage_message") else "")
                                   + ". Open it to fix, then press ▶ Make segments.")
                s.commit()
                logbus.log("error", "countdown_segment_failed", f"Countdown #{c.id}: {c.stage_message}")
                continue
            if made == len(segs):
                c.stage_status, c.stage_message = "done", f"All {made} segments made. Check the script (step 4), then render."
                s.commit()
                logbus.log("info", "countdown_segments_done", f"Countdown #{c.id}: all {made} segments made")
                continue
            busy = any(x["stage_status"] in ("running", "queued", "paused") for x in segs if not x["rendered"])
            c.stage_status = "running" if busy else "waiting"
            c.stage_message = f"Making segments: {made} of {len(segs)} done" + ("" if busy else " — waiting for Studio to be free")
            s.commit()
            if did is None and not busy and not runner.busy().get("project_id") and not runner.queued_jobs():
                nxt = next(x for x in segs if not x["rendered"])
                p = s.get(StudioProject, nxt["id"])
                stage = _next_stage(p)
                try:
                    runner.start(p.id, stage, auto=True, until="render")
                    did = {"countdown": c.id, "segment": p.id, "stage": stage}
                    logbus.log("info", "countdown_segment", f"Countdown #{c.id}: making “{p.title}” from step '{stage}'",
                               project=p.id)
                except RuntimeError:
                    pass
        sweep_archived(s)
    return did


# --- 4. script -----------------------------------------------------------------------
def default_script(c: CountdownProject) -> dict[str, Any]:
    r = c.ranking or {}
    titles = r.get("title_options") or [c.topic]
    return {"title": titles[0][:100], "title_options": titles, "intro": r.get("hook_script") or "",
            "outro": r.get("closing_question") or "", "thumbnail_text": r.get("thumbnail_text") or ""}


# --- 5. render ----------------------------------------------------------------------
def render_job(cid: int) -> dict[str, Any]:
    from .compilations import stitch
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        ids, sc = list(c.segment_ids or []), dict(c.script or default_script(c))
    _set(cid, stage="render", stage_status="running", stage_message="Stitching the countdown video…")
    try:
        with costs.operation("countdown_render", ref=f"countdown:{cid}"):      # intro/outro voice-over
            res = stitch.stitch_countdown_compilation(project_ids=ids, title=sc.get("title") or "",
                                                      custom_intro=sc.get("intro") or "", custom_outro=sc.get("outro") or "",
                                                      send_to_queue=False)
    except control.Cancelled:
        _set(cid, stage_status="stopped", stage_message="Render stopped")
        raise
    except Exception as exc:  # noqa: BLE001
        _set(cid, stage_status="error", stage_message=f"Render failed: {str(exc)[:300]}")
        raise
    thumbs = {"poster": "thumb_option_1_poster.jpg", "action": "thumb_option_2_action.jpg", "split": "thumb_option_3_split.jpg"}
    render = {"compilation_id": res["compilation_id"], "file": res["video_path"], "thumbnail": res["primary_thumb"],
              "thumbnails": thumbs, "selected_thumbnail": "poster", "chapters": res["chapters"],
              "seconds": res["total_seconds"], "minutes": res["total_minutes"], "rendered_at": _now().isoformat()}
    _set(cid, render=render, stage="render", stage_status="done",
         stage_message=f"Video made: {res['total_minutes']} min. Watch it, pick a thumbnail, then Send to the Countdowns queue.")
    logbus.log("info", "countdown_rendered", f"Countdown #{cid}: video made ({res['total_minutes']} min)")
    return res


def comp_dir(c: CountdownProject):
    from .compilations import stitch
    return stitch.compilations_dir() / str((c.render or {}).get("compilation_id"))


def publish(s, c: CountdownProject) -> QueueItem:
    """Send to the Countdowns queue as Ready to Post on Screen Central (like LongForm)."""
    from sqlalchemy import func
    from .. import undo
    from .compilations import engine
    if not c.render:
        raise HTTPException(400, "Render the video first")
    sc = c.script or default_script(c)
    entries = _ordered(entries_of(c))
    lines = [f"{ch['time_formatted']} {ch['title']}" for ch in (c.render or {}).get("chapters", [])]
    ranking = {**(c.ranking or {}), "topic": c.topic, "hook_script": sc.get("intro"), "closing_question": sc.get("outro")}
    tags = f"countdown, top {len(entries)}, {c.topic.lower()}, movies, tv, ranking, screen central"
    for e in entries[-6:]:
        tags += f", {(e.get('title') or '').lower()}"
    fields = dict(pipeline=PIPELINE, source=SOURCE, video_name=f"Top {len(entries)} — {c.topic[:80]}",
                  video_path=c.render["file"], thumb_path=c.render.get("thumbnail"), title=(sc.get("title") or c.topic)[:100],
                  description=engine.countdown_description(ranking, entries, lines), tags=tags[:500],
                  research={"countdown_id": c.id, "chapters": c.render.get("chapters")})
    item = s.get(QueueItem, c.queue_item_id) if c.queue_item_id else None
    if item and item.status in ("review", "ready", "retry", "error"):
        undo.record(s, "queue", f"Update countdown '{c.topic[:40]}'", rows=[item])
        for k, v in fields.items():
            setattr(item, k, v)
        if not item.accounts:
            item.accounts = list(DESTINATION)
        item.status = "ready"
    else:
        item = QueueItem(**fields, status="ready", accounts=list(DESTINATION),
                         position=(s.query(func.max(QueueItem.position)).scalar() or 0) + 1)
        s.add(item)
        s.flush()
        undo.add_created(s, "queue", f"Add countdown '{c.topic[:40]}'", [item.id])
        c.queue_item_id = item.id
    s.commit()
    from ..social import queue_manager
    queue_manager.wake()
    logbus.log("info", "countdown_published", f"Countdown #{c.id}: sent to the Countdowns queue as #{item.id} (ready)")
    return item


# --- delete / archive -----------------------------------------------------------------
def delete_now(s, c: CountdownProject, reason: str) -> None:
    """Remove the countdown, its video and the segments made only for it
    (a real LongForm breakdown that was reused is never touched)."""
    from . import membership
    from .routes import delete_project_now
    item = s.get(QueueItem, c.queue_item_id) if c.queue_item_id else None
    if item and item.status in ("review", "ready", "retry", "error"):
        s.delete(item)
    if c.render and c.render.get("compilation_id"):
        shutil.rmtree(comp_dir(c), ignore_errors=True)
    segs = list(c.segment_ids or [])
    reused = {int(e["segment_id"]) for e in entries_of(c) if e.get("reused") and e.get("segment_id")}
    cid = c.id
    s.delete(c)
    s.commit()
    still_used = membership.countdown_ids(s)
    for pid in segs:
        p = s.get(StudioProject, int(pid))
        if p is None or int(pid) in reused or int(pid) in still_used or p.stage_status == "running":
            continue
        if (p.facts or {}).get("origin") == "countdown" and not p.queue_item_id:
            delete_project_now(s, p, f"segment of deleted countdown #{cid}")
    logbus.log("info", "countdown_deleted", f"Countdown #{cid} deleted ({reason})")


def sweep_archived(s) -> None:
    now = _now()
    for c in s.query(CountdownProject).all():
        after = (c.review or {}).get("delete_after")
        if after and datetime.datetime.fromisoformat(after) <= now and c.stage_status != "running":
            delete_now(s, c, "archived 5 days ago")


# --- migration of the old countdown data (once) -----------------------------------------
def migrate_legacy() -> int:
    """Turn the old saved countdowns + "Countdown Studio" queue rows into
    CountdownProjects (2026-10-12). Rows with no video are removed from the
    LongForm queue — everything they held moves into the project."""
    made = 0
    with SessionLocal() as s:
        flag = s.get(AppSetting, "countdowns_migrated")
        if flag is not None:
            return 0
        seen: dict[str, CountdownProject] = {}
        rows = s.query(QueueItem).filter(QueueItem.source == "Countdown Studio").order_by(QueueItem.id).all()
        for q in rows:
            r = q.research or {}
            cd = dict(r.get("countdown") or {})
            key = cd.get("id") or f"row-{q.id}"
            c = seen.get(key)
            if c is None:
                c = CountdownProject(topic=cd.get("topic") or q.title or "Countdown", fmt=cd.get("format") or "top10",
                                     ranking=cd or None, stage="research", stage_status="done",
                                     stage_message="Moved from the old Countdown Studio — check the titles (step 2).",
                                     review={"legacy_queue_rows": []})
                s.add(c)
                seen[key] = c
                made += 1
            c.review = {**(c.review or {}), "legacy_queue_rows": [*(c.review or {}).get("legacy_queue_rows", []), q.id]}
            pids = [int(x) for x in r.get("project_ids") or []]
            ents = _ordered(entries_of(c))
            if not c.segment_ids and pids and len(pids) == len(ents) and all(e.get("tmdb_id") for e in ents):
                # Its segments already exist: keep them attached, but don't spend until the owner says so.
                for e, pid in zip(ents, pids):
                    e["segment_id"] = pid
                c.ranking = {**(c.ranking or {}), "entries": ents}
                c.segment_ids = pids
                c.script = default_script(c)
                c.stage, c.stage_status = "segments", "stopped"
                c.stage_message = ("Moved from the old Countdown Studio with its segments. "
                                   "Press ▶ Make segments to finish them (uses the daily AI budget).")
            if q.video_path:                    # an old stitched video: keep the row, move it to its own queue
                q.pipeline = PIPELINE
                q.source = SOURCE
            else:
                s.delete(q)
        saved = s.get(AppSetting, "saved_countdowns")
        for cd in ((saved.value or {}).get("items") or []) if saved else []:
            key = cd.get("id")
            if key in seen or not cd.get("entries"):
                continue
            c = CountdownProject(topic=cd.get("topic") or "Countdown", fmt=cd.get("format") or "top10", ranking=cd,
                                 stage="research", stage_status="done",
                                 stage_message="Moved from Saved Countdowns — check the titles (step 2).")
            s.add(c)
            seen[key] = c
            made += 1
        s.add(AppSetting(key="countdowns_migrated", value={"at": _now().isoformat(), "made": made}))
        s.commit()
    if made:
        logbus.log("info", "countdowns_migrated", f"Moved {made} old countdown(s) into Countdowns Studio")
    return made


# --- API ---------------------------------------------------------------------------------
class NewCountdown(BaseModel):
    topic: str
    fmt: str = "top10"
    notes: str = ""


class FromBreakdowns(BaseModel):
    topic: str
    project_ids: list[int]          # #1 first


class EntriesIn(BaseModel):
    entries: list[dict[str, Any]]   # #1 first


class ScriptIn(BaseModel):
    title: str | None = None
    intro: str | None = None
    outro: str | None = None


class FlagIn(BaseModel):
    on: bool


class ThumbIn(BaseModel):
    option: str


@router.get("")
def list_countdowns() -> list[dict[str, Any]]:
    with SessionLocal() as s:
        return [out(s, c, full=False) for c in s.query(CountdownProject).order_by(CountdownProject.id.desc()).all()]


@router.post("")
def create(req: NewCountdown) -> dict[str, Any]:
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(400, "Type a topic, e.g. “Top 10 horror movies on Netflix”")
    fmt = req.fmt if req.fmt in COUNTS else "top10"
    with SessionLocal() as s:
        c = CountdownProject(topic=topic[:255], fmt=fmt, notes=req.notes.strip() or None)
        s.add(c)
        s.commit()
        cid = c.id
    start_research(cid)
    with SessionLocal() as s:
        return out(s, s.get(CountdownProject, cid))


@router.post("/from-breakdowns")
def create_from_breakdowns(req: FromBreakdowns) -> dict[str, Any]:
    """A countdown from rendered LongForm breakdowns you already have (no research step)."""
    if len(req.project_ids) < 2:
        raise HTTPException(400, "Pick at least 2 breakdowns")
    with SessionLocal() as s:
        entries = []
        for i, pid in enumerate(req.project_ids):
            p = s.get(StudioProject, pid)
            if p is None:
                raise HTTPException(400, f"Breakdown #{pid} not found")
            entries.append({"rank": i + 1, "title": p.title, "tmdb_id": p.tmdb_id, "media_type": p.media_type,
                            "poster": (p.facts or {}).get("poster"), "year": str((p.facts or {}).get("primary_date") or "")[:4]})
        n = len(entries)
        fmt = "top5" if n <= 5 else "top10" if n <= 10 else "top15"
        c = CountdownProject(topic=req.topic.strip() or f"Top {n}", fmt=fmt,
                             ranking={"entries": entries, "title_options": [req.topic.strip() or f"Top {n}"]},
                             stage="research", stage_status="done", stage_message="Made from your breakdowns.")
        s.add(c)
        s.flush()
        confirm_titles(s, c)
        s.commit()
        return out(s, c)


@router.get("/{cid}")
def get_one(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        return out(s, _get(s, cid))


@router.post("/{cid}/research")
def rerun_research(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        if _running(c):
            raise HTTPException(409, "This countdown is busy — Stop it first")
        if c.segment_ids:
            raise HTTPException(409, "Titles are confirmed; new research would replace them. Delete and start again instead.")
    start_research(cid)
    return {"ok": True}


@router.put("/{cid}/entries")
def put_entries(cid: int, req: EntriesIn) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        if c.stage in ("segments", "render") and _running(c):
            raise HTTPException(409, "Segments are being made — Stop first to change the titles")
        save_entries(c, req.entries)
        s.commit()
        return out(s, c)


@router.post("/{cid}/confirm")
def confirm(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        if not entries_of(c):
            raise HTTPException(400, "Research first")
        confirm_titles(s, c)
        s.commit()
        logbus.log("info", "countdown_confirmed", f"Countdown #{c.id}: {len(c.segment_ids)} titles confirmed")
        return out(s, c)


@router.post("/{cid}/make")
def make_segments(cid: int) -> dict[str, Any]:
    """▶ Make segments (also retries a failed or stopped segment from its step)."""
    with SessionLocal() as s:
        c = _get(s, cid)
        if not c.segment_ids:
            raise HTTPException(400, "Confirm the titles first (step 2)")
        for pid in c.segment_ids:
            p = s.get(StudioProject, int(pid))
            if p is not None and p.stage_status in ("error", "stopped"):
                p.stage_status = "idle"
        c.stage, c.stage_status = "segments", "waiting"
        c.stage_message = "Segments are made one at a time when Studio is free."
        s.commit()
    tick()
    with SessionLocal() as s:
        return out(s, _get(s, cid))


@router.post("/{cid}/stop")
def stop(cid: int) -> dict[str, Any]:
    from . import runner
    with SessionLocal() as s:
        c = _get(s, cid)
        for pid in c.segment_ids or []:
            p = s.get(StudioProject, int(pid))
            if p is not None and p.stage_status in ("running", "queued"):
                runner.stop(p.id)
        control.stop_jobs_for("countdown", cid)
        c.stage_status = "stopped"
        c.stage_message = "Stopped. Finished segments are kept; ▶ Make segments carries on."
        s.commit()
        return out(s, c)


@router.put("/{cid}/script")
def put_script(cid: int, req: ScriptIn) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        sc = dict(c.script or default_script(c))
        for k in ("title", "intro", "outro"):
            v = getattr(req, k)
            if v is not None:
                sc[k] = v.strip()
        c.script = sc
        s.commit()
        return out(s, c)


@router.post("/{cid}/render")
def render(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        if _running(c):
            raise HTTPException(409, "This countdown is busy")
        if not out(s, c, full=False)["has"]["segments"]:
            raise HTTPException(400, "Every segment must be made first (step 3)")
    job = control.start_thread("countdown", f"Countdown #{cid}: render", "countdown", render_job, cid, ref=cid)
    return {"ok": True, "job_id": job}


@router.post("/{cid}/thumbnail")
def pick_thumbnail(cid: int, req: ThumbIn) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        files = (c.render or {}).get("thumbnails") or {}
        if req.option not in files:
            raise HTTPException(400, "Unknown thumbnail option")
        d = comp_dir(c) / "thumbnails"
        shutil.copyfile(d / files[req.option], d / "thumbnail.jpg")
        r = dict(c.render)
        r["selected_thumbnail"] = req.option
        r["thumb_v"] = _now().timestamp()
        c.render = r
        s.commit()
        return out(s, c)


@router.post("/{cid}/publish")
def send_to_queue(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        item = publish(s, c)
        return {"queue_item_id": item.id, "status": item.status}


@router.put("/{cid}/review")
def set_review(cid: int, req: FlagIn) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        rv = dict(c.review or {})
        rv["reviewed_at"] = _now().isoformat() if req.on else None
        c.review = rv
        s.commit()
        return out(s, c, full=False)


@router.put("/{cid}/archive")
def set_archive(cid: int, req: FlagIn) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        rv = dict(c.review or {})
        if req.on:
            rv["delete_after"] = (_now() + datetime.timedelta(days=ARCHIVE_DAYS)).isoformat()
        else:
            rv.pop("delete_after", None)
        c.review = rv
        s.commit()
        return out(s, c, full=False)


@router.delete("/{cid}")
def delete(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        if _running(c) and c.stage != "segments":
            raise HTTPException(409, "This countdown is busy — Stop it first")
        delete_now(s, c, "deleted by owner")
    return {"deleted": cid}


@router.get("/{cid}/file")
def file(cid: int, path: str = "video"):
    """The countdown's video or a thumbnail — nothing else on the volume."""
    with SessionLocal() as s:
        c = _get(s, cid)
        if not c.render:
            raise HTTPException(404, "no video yet")
        d = comp_dir(c).resolve()
        names = {"video": d / "final_compilation.mp4", "thumbnail": d / "thumbnails" / "thumbnail.jpg"}
        for k, n in ((c.render or {}).get("thumbnails") or {}).items():
            names[f"thumb_{k}"] = d / "thumbnails" / n
    target = names.get(path)
    if target is None or not target.resolve().is_relative_to(d) or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target)
