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
        "segment_minutes": float((c.review or {}).get("segment_minutes") or SEGMENT_MINUTES.get(c.fmt, 1.0)),
        "auto": bool((c.review or {}).get("auto")),
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
        control.progress("vidIQ: what people search and the top existing videos")
        mk = market_data(cid)
        control.check()
        with costs.operation("countdown_research", ref=f"countdown:{cid}"):
            data = engine.generate_countdown(topic=topic, format_type=fmt, custom_instructions=notes,
                                             market=market_text(mk))
        data["market"] = mk
        data["contract_version"] = 1
        control.check()
        entries = []
        for e in data.get("entries") or []:
            r = engine._resolve_title(e)
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
        with SessionLocal() as s:
            auto = bool(((s.get(CountdownProject, cid).review) or {}).get("auto"))
        if auto:
            auto_titles(cid)
        return msg
    except control.Cancelled:
        _set(cid, stage_status="stopped", stage_message="Research stopped")
        raise
    except costs.DailyCapReached:
        _set(cid, stage_status="paused", stage_message="⏸ Research paused — today's AI budget is used; it carries on after midnight.")
        raise
    except Exception as exc:  # noqa: BLE001 — shown on the card
        _set(cid, stage_status="error", stage_message=f"Research failed: {str(exc)[:300]}")
        raise


def market_data(cid: int) -> dict[str, Any]:
    """vidIQ data for the ranking (≈ 10–15 credits, cached): searches around the topic, what people ask,
    and the most-viewed existing videos for it. Empty when vidIQ is off or capped."""
    from .. import vidiq
    if not vidiq.configured():
        return {}
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        topic, kw = c.topic, (c.review or {}).get("keyword")
    import re as _re
    kw = kw or _re.sub(r"\b(top\s*\d+|ranked|ranking|definitively|of all time|ever)\b", "", topic, flags=_re.I).strip(" ,-:") or topic
    m = vidiq.keyword_metrics(kw, purpose=f"countdown #{cid} research", cache_hours=168)
    subject = (m or {}).get("keyword") or kw
    return {"keyword": kw, "demand": {k: m[k] for k in ("monthly_searches", "competition", "growth_pct")} if m else None,
            "related": (m or {}).get("related", [])[:12],
            "questions": vidiq.questions(subject, purpose=f"countdown #{cid} research")[:10],
            "competitors": vidiq.competitor_videos(topic, purpose=f"countdown #{cid} research")[:10]}


def market_text(mk: dict[str, Any]) -> str:
    if not mk:
        return ""
    lines = []
    if mk.get("demand"):
        d = mk["demand"]
        lines.append(f"- \"{mk['keyword']}\": {d['monthly_searches']:,} searches/month, competition {d['competition']:.0f}/100")
    if mk.get("related"):
        lines.append("- Related searches: " + "; ".join(f"{r['keyword']} ({r['monthly_searches']:,}/mo)" for r in mk["related"]))
    if mk.get("questions"):
        lines.append("- What people ask: " + "; ".join(f"{q['question']} ({q['monthly_searches']:,}/mo)" for q in mk["questions"]))
    if mk.get("competitors"):
        lines.append("- Top existing videos: " + "; ".join(f"\"{v['title']}\" ({(v.get('views') or 0):,} views)" for v in mk["competitors"]))
    return "\n".join(lines)


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
    if (c.ranking or {}).get("contract_version") == 1:
        expected = COUNTS.get(c.fmt)
        if expected and len(entries) != expected:
            raise HTTPException(409, f"{FMT_NAME(c.fmt)} requires exactly {expected} entries; review and repair the ranking")
    identities = [(e.get("media_type") or "movie", e.get("tmdb_id")) for e in entries if e.get("tmdb_id")]
    if len(set(identities)) != len(identities):
        raise HTTPException(409, "Duplicate film/show identities in this ranking; replace duplicates before confirming")
    bad = [f"#{e.get('rank')} {e.get('title')}" for e in entries if not e.get("tmdb_id")]
    if bad:
        raise HTTPException(400, "These aren't matched to a movie or show (no trailer to use): "
                                 + ", ".join(bad) + ". Swap or remove them first.")
    if len(entries) < 2:
        raise HTTPException(400, "A countdown needs at least 2 titles")
    if (c.ranking or {}).get("contract_version") == 1:
        from .compilations import engine
        for e in entries:
            if not e.get("year") or not engine._resolve_title(e):
                raise HTTPException(409, f"Canonical title/year/medium could not be confirmed for #{e.get('rank')} {e.get('title')}. Repair the identity before making segments.")
    minutes = float((c.review or {}).get("segment_minutes") or SEGMENT_MINUTES.get(c.fmt, 1.0))
    total = len(entries)
    ids = []
    for e in _ordered(entries):
        ctx = {"countdown_id": c.id, "rank": e.get("rank"), "total": total, "topic": c.topic, "why_it_ranks": e.get("why_it_ranks"),
               "fun_fact": e.get("fun_fact"), "key_stat": e.get("key_stat"), "where_to_watch": e.get("where_to_watch")}
        p = (s.query(StudioProject)
             .filter(StudioProject.tmdb_id == int(e["tmdb_id"]), StudioProject.media_type == e.get("media_type", "movie"))
             .order_by(StudioProject.id.desc()).first())
        # Rank, editorial angle and CTA belong to this campaign. A LongForm or
        # another countdown's render must never stand in for this segment.
        if p is not None and ((p.facts or {}).get("countdown") or {}).get("countdown_id") != c.id:
            p = None
        if p is not None:
            f = dict(p.facts or {})
            changed = f.get("countdown") != ctx or p.target_minutes != minutes
            f.update(origin="countdown", is_countdown=True, countdown=ctx)
            p.facts = f
            if changed:
                p.script, p.plan, p.render = None, None, None
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


MIDDAY = (12, 15)          # owner (2026-10-13): countdowns get Studio's midday turn, 12:00–15:00 local


def _local_now(now: datetime.datetime | None = None) -> datetime.datetime:
    return (now or _now()).astimezone(costs.day_tz())


def midday(now: datetime.datetime | None = None) -> bool:
    return MIDDAY[0] <= _local_now(now).hour < MIDDAY[1]


def longform_first(s) -> bool:
    """Outside the midday turn, LongForm goes first while its automation still has
    breakdowns to start today (on, not paused, a type under its daily limit)."""
    from . import auto
    cfg = auto.settings_value(s)
    if not cfg.get("enabled") or auto.paused_now(cfg):
        return False
    made = auto.made_today(s)
    return any(made.get(mt, 0) < auto.limit_for(mt, cfg) for mt in ("movie", "tv"))


def fix_lengths(s, c: CountdownProject) -> int:
    """Segments made for this countdown that have no script yet get the format's
    length (reused LongForm breakdowns and finished segments are never changed)."""
    reused = {int(e["segment_id"]) for e in entries_of(c) if e.get("reused") and e.get("segment_id")}
    want, n = float((c.review or {}).get("segment_minutes") or SEGMENT_MINUTES.get(c.fmt, 1.0)), 0
    for pid in c.segment_ids or []:
        p = s.get(StudioProject, int(pid))
        if p is None or int(pid) in reused or p.script or p.stage_status == "running":
            continue
        if abs((p.target_minutes or 0) - want) > 0.01:
            p.target_minutes = want
            n += 1
    return n


def _budget_ok() -> bool:
    try:
        costs.check_budget()
        return True
    except Exception:  # noqa: BLE001 — DailyCapReached / BudgetExceeded
        return False


def tick(from_runner: bool = False) -> dict[str, Any] | None:
    """Advance countdowns. One countdown is made at a time (oldest first); its next
    segment goes into Studio's waiting line, so it starts the moment Studio is free
    and the following one chains straight after. Outside the midday turn it waits
    while LongForm's automation still has breakdowns to start today."""
    from . import runner
    did = None
    if not from_runner:
        try:
            auto_tick()
        except Exception as exc:  # noqa: BLE001 — never stops the segment work below
            auto_state["last_result"] = f"error: {exc}"
            logger.warning("countdown automation: %s", exc)
    with SessionLocal() as s:
        # Research/render the daily cap paused: carry on once there's budget again.
        for c in s.query(CountdownProject).filter(CountdownProject.stage.in_(["research", "render"]),
                                                  CountdownProject.stage_status == "paused").all():
            if _budget_ok() and not control.running("countdown", c.id):
                (start_research if c.stage == "research" else start_render)(c.id)
        active_taken = False
        for c in s.query(CountdownProject).filter(CountdownProject.stage == "segments",
                                                  CountdownProject.stage_status.in_(["waiting", "running", "paused"])) \
                .order_by(CountdownProject.id).all():
            if fix_lengths(s, c):
                s.commit()
            segs = _segments(s, c)
            made = sum(1 for x in segs if x["rendered"])
            # A step a deploy/restart cut off isn't a failure: put it back in line (owner never pressed Stop).
            for x in segs:
                if not x["rendered"] and x["stage_status"] == "stopped" and "restart" in (x.get("stage_message") or ""):
                    p = s.get(StudioProject, x["id"])
                    if p is not None:
                        p.stage_status, x["stage_status"] = "idle", "idle"
                        s.commit()
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
                c.stage_status = "done"
                c.stage_message = f"All {made} segments made. 👀 Check the titles and script, then press Render video."
                if not c.script:
                    c.script = default_script(c)
                s.commit()
                logbus.log("info", "countdown_segments_done", f"Countdown #{c.id}: all {made} segments made — ready for review")
                continue
            prefix = f"Making segments: {made} of {len(segs)} done"
            waiting = next((x for x in segs if x["stage_status"] == "awaiting_worker"), None)
            if waiting:
                import pathlib
                from ..models import Clip
                p = s.get(StudioProject, waiting["id"])
                clip = s.get(Clip, (p.trailer or {}).get("pending_clip_id")) if p.trailer else None
                if not clip or not clip.file_path or not pathlib.Path(clip.file_path).is_file():
                    c.stage_status = "waiting"
                    c.stage_message = f"{prefix} — “{p.title}” is awaiting footage from the home worker"
                    s.commit()
                    continue
            paused = next((x for x in segs if x["stage_status"] == "paused"), None)
            if paused:
                c.stage_status = "paused"
                c.stage_message = (f"⏸ {prefix} — today's AI budget is used; “{paused['title']}” carries on after "
                                   f"midnight (or raise today's cap in Settings → Spending).")
                s.commit()
                active_taken = True
                continue
            inflight = next((x for x in segs if x["stage_status"] in ("running", "queued")), None)
            if inflight:
                c.stage_status = "running"
                c.stage_message = (f"{prefix} — making “{inflight['title']}”" if inflight["stage_status"] == "running"
                                   else f"{prefix} — “{inflight['title']}” is waiting in Studio's line")
                s.commit()
                active_taken = True
                continue
            if active_taken:
                c.stage_status, c.stage_message = "waiting", f"{prefix} — starts after the countdown before it"
                s.commit()
                continue
            active_taken = True
            if not midday() and longform_first(s):
                c.stage_status = "waiting"
                c.stage_message = (f"{prefix} — LongForm breakdowns go first today; countdowns get Studio from "
                                   f"{MIDDAY[0]}:00 to {MIDDAY[1] - 12}:00 PM, and any time LongForm is done for the day.")
                s.commit()
                continue
            nxt = next(x for x in segs if not x["rendered"])
            p = s.get(StudioProject, nxt["id"])
            stage = _next_stage(p)
            s.commit()
            state = runner.start_or_queue(p.id, stage, auto=True, until="render")
            busy_with = runner.busy().get("project_id")
            c.stage_status = "running"
            c.stage_message = (f"{prefix} — making “{p.title}”" if state == "started"
                               else f"{prefix} — “{p.title}” is next in Studio's line (Studio is busy with #{busy_with})")
            s.commit()
            did = {"countdown": c.id, "segment": p.id, "stage": stage, "state": state}
            logbus.log("info", "countdown_segment", f"Countdown #{c.id}: “{p.title}” from step '{stage}' ({state})",
                       project=p.id)
        sweep_archived(s)
        sweep_posted(s)
    return did


def ensure_midday_schedule() -> bool:
    """Owner (2026-10-13): the Countdowns queue posts once a day at midday. Set once
    (the owner can change it in ⏰ Schedule afterwards; it isn't put back)."""
    from ..social import queue_manager
    with SessionLocal() as s:
        if s.get(AppSetting, "countdowns_schedule_set") is not None:
            return False
        cfg = queue_manager.load_schedule(s)
        pipes = dict(cfg.get("pipelines") or {})
        if PIPELINE not in pipes:
            pipes[PIPELINE] = {"times": ["12:00"]}
            queue_manager.save_schedule(s, {**cfg, "pipelines": pipes})
        s.add(AppSetting(key="countdowns_schedule_set", value={"at": _now().isoformat()}))
        s.commit()
    return True


def recover() -> None:
    """After a restart: research or render that was cut off starts again."""
    with SessionLocal() as s:
        cut = [(c.id, c.stage) for c in s.query(CountdownProject).filter(
            CountdownProject.stage.in_(["research", "render"]), CountdownProject.stage_status == "running").all()]
    for cid, stage in cut:
        logbus.log("info", "countdown_resumed", f"Countdown #{cid}: {stage} was cut off by a restart — starting it again")
        (start_research if stage == "research" else start_render)(cid)


# --- 4. script -----------------------------------------------------------------------
def default_script(c: CountdownProject) -> dict[str, Any]:
    r = c.ranking or {}
    titles = r.get("title_options") or [c.topic]
    return {"title": titles[0][:100], "title_options": titles, "intro": r.get("hook_script") or "",
            "outro": r.get("closing_question") or "", "thumbnail_text": r.get("thumbnail_text") or ""}


# --- 5. render ----------------------------------------------------------------------
def segment_fingerprints(s, c) -> dict:
    from .research_gate import require_checked
    from .media_quality import export_fingerprint
    from .runner import project_dir
    fingerprints = {}
    for pid in c.segment_ids or []:
        p = s.get(StudioProject, pid)
        if p is None:
            raise RuntimeError(f"Missing countdown segment {pid}")
        require_checked(p.script)
        fingerprints[str(pid)] = export_fingerprint(p, project_dir(pid))
    if not fingerprints:
        raise RuntimeError("No checked segments")
    return fingerprints


def inspect_countdown(res) -> dict:
    from pathlib import Path
    from .media_quality import inspect_export
    quality = inspect_export(Path(res["video_path"]), expected_seconds=float(res["total_seconds"]))
    if quality["errors"]:
        raise RuntimeError("Final countdown quality blocked: " + "; ".join(quality["errors"]))
    return quality


def render_job(cid: int) -> dict[str, Any]:
    from .compilations import stitch
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        ids, sc = list(c.segment_ids or []), dict(c.script or default_script(c))
    _set(cid, stage="render", stage_status="running", stage_message="Stitching the countdown video…")
    try:
        with SessionLocal() as s:
            fingerprints = segment_fingerprints(s, s.get(CountdownProject, cid))
        with costs.operation("countdown_render", ref=f"countdown:{cid}"):      # intro/outro voice-over
            res = stitch.stitch_countdown_compilation(project_ids=ids, title=sc.get("title") or "",
                                                      custom_intro=sc.get("intro") or "", custom_outro=sc.get("outro") or "",
                                                      send_to_queue=False)
        quality = inspect_countdown(res)
    except control.Cancelled:
        _set(cid, stage_status="stopped", stage_message="Render stopped")
        raise
    except costs.DailyCapReached:
        _set(cid, stage_status="paused", stage_message="⏸ Render paused — today's AI budget is used (intro/outro voice); it carries on after midnight.")
        raise
    except Exception as exc:  # noqa: BLE001
        _set(cid, stage_status="error", stage_message=f"Render failed: {str(exc)[:300]}")
        raise
    try:   # the stitch copied every segment into its folder; the final video is made, drop the copies
        shutil.rmtree(stitch.compilations_dir() / str(res["compilation_id"]) / "segments", ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    thumbs = {"poster": "thumb_option_1_poster.jpg", "action": "thumb_option_2_action.jpg", "split": "thumb_option_3_split.jpg"}
    render = {"compilation_id": res["compilation_id"], "file": res["video_path"], "thumbnail": res["primary_thumb"],
              "quality": quality, "segment_fingerprints": fingerprints,
              "thumbnails": thumbs, "selected_thumbnail": "poster", "chapters": res["chapters"],
              "seconds": res["total_seconds"], "minutes": res["total_minutes"], "rendered_at": _now().isoformat()}
    _set(cid, render=render, stage="render", stage_status="done",
         stage_message=f"Video made: {res['total_minutes']} min. Watch it, pick a thumbnail, then Send to the Countdowns queue.")
    logbus.log("info", "countdown_rendered", f"Countdown #{cid}: video made ({res['total_minutes']} min)")
    return res


def start_render(cid: int) -> str:
    return control.start_thread("countdown", f"Countdown #{cid}: render", "countdown", render_job, cid, ref=cid)


def comp_dir(c: CountdownProject):
    from .compilations import stitch
    return stitch.compilations_dir() / str((c.render or {}).get("compilation_id"))


def require_release(s, c: CountdownProject, approved: bool = True) -> str:
    from .media_quality import export_fingerprint, require_export_approval
    try:
        if segment_fingerprints(s, c) != (c.render or {}).get("segment_fingerprints"):
            raise RuntimeError("Segments changed since the final countdown render; render again")
        fingerprint = export_fingerprint(c, comp_dir(c))
        if approved:
            require_export_approval(c, comp_dir(c))
        return fingerprint
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.post("/{cid}/approve-export")
def approve_countdown_export(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        c = _get(s, cid)
        if c.stage_status in ("running", "waiting", "queued"):
            raise HTTPException(409, "Wait for the countdown to finish before approval")
        fingerprint = require_release(s, c, approved=False)
        c.review = {**(c.review or {}), "export_approval": {"fingerprint": fingerprint, "approved_at": _now().isoformat()}}
        s.commit()
        return out(s, c)


def publish(s, c: CountdownProject) -> QueueItem:
    """Send to the Countdowns queue as Ready to Post on Screen Central (like LongForm)."""
    from sqlalchemy import func
    from .. import undo
    from .compilations import engine
    if not c.render:
        raise HTTPException(400, "Render the video first")
    require_release(s, c)
    sc = c.script or default_script(c)
    entries = _ordered(entries_of(c))
    lines = [f"{ch['time_formatted']} {ch['title']}" for ch in (c.render or {}).get("chapters", [])]
    ranking = {**(c.ranking or {}), "topic": c.topic, "hook_script": sc.get("intro"), "closing_question": sc.get("outro")}
    tags = seo_tags(c, entries)
    fields = dict(pipeline=PIPELINE, source=SOURCE, video_name=f"Top {len(entries)} — {c.topic[:80]}",
                  video_path=c.render["file"], thumb_path=c.render.get("thumbnail"), title=(sc.get("title") or c.topic)[:100],
                  description=engine.countdown_description(ranking, entries, lines), tags=tags[:500],
                  research={"countdown_id": c.id, "chapters": c.render.get("chapters")})
    from ..social import queue_manager
    item = s.get(QueueItem, c.queue_item_id) if c.queue_item_id else None
    from ..social import enqueue
    if item:
        enqueue.ensure_editable(item)
    if item and item.status in ("review", "ready", "retry", "error"):
        undo.record(s, "queue", f"Update countdown '{c.topic[:40]}'", rows=[item])
        for k, v in fields.items():
            setattr(item, k, v)
        if not item.accounts:
            item.accounts = queue_manager.default_destination(DESTINATION)
        item.status = "ready"
    else:
        item = QueueItem(**fields, status="ready", accounts=queue_manager.default_destination(DESTINATION),
                         position=(s.query(func.max(QueueItem.position)).scalar() or 0) + 1)
        item, created = enqueue.claim(s, c, item)
        if created:
            undo.add_created(s, "queue", f"Add countdown '{c.topic[:40]}'", [item.id])
    s.commit()
    from ..social import queue_manager
    queue_manager.wake()
    logbus.log("info", "countdown_published", f"Countdown #{c.id}: sent to the Countdowns queue as #{item.id} (ready)")
    d = (c.render or {}).get("drive") or {}
    if d.get("status") != "saved" or d.get("rendered_at") != c.render.get("rendered_at"):
        start_drive(c.id)
    return item


def seo_tags(c: CountdownProject, entries: list[dict[str, Any]]) -> str:
    """Hashtags (the queue's format: "#tag #tag", used as YouTube tags and at the end of the
    description): vidIQ's best related searches for the topic first, then #1–#3 titles and
    the basics. At most 12 — YouTube ignores every hashtag when there are more than 15."""
    from .. import vidiq
    kw = (c.review or {}).get("keyword") or c.topic
    related = list((c.review or {}).get("keywords") or [])
    if not related:
        m = vidiq.keyword_metrics(kw, purpose=f"countdown #{c.id} tags")
        if m:
            related = [r["keyword"] for r in m["related"][:12]]
            rv = dict(c.review or {})
            rv["keywords"], rv["demand"] = related, {k: m[k] for k in ("overall", "monthly_searches", "growth_pct")}
            c.review = rv
    by_rank = sorted(entries, key=lambda e: int(e.get("rank") or 99))
    parts = [kw, *related[:6], *[(e.get("title") or "") for e in by_rank[:3]], f"top {len(entries)}", "countdown", "screen central"]
    out: list[str] = []
    for p in parts:
        tag = "#" + "".join(ch for ch in p.lower() if ch.isalnum())
        if len(tag) > 2 and tag not in out:
            out.append(tag)
        if len(out) >= 12:
            break
    return " ".join(out)


# --- ☁️ Drive copy (like LongForm: a "Countdowns" folder inside the LongForm Studio folder) ---------
def drive_job(cid: int) -> dict[str, Any]:
    from ..drive_sync import get_drive_service
    from . import archive, drive_store
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        if not c or not c.render:
            raise ValueError("Render the video first")
        video, thumb = comp_dir(c) / "final_compilation.mp4", comp_dir(c) / "thumbnails" / "thumbnail.jpg"
        rendered_at = c.render.get("rendered_at")
        title = drive_store._safe_name((c.script or {}).get("title") or c.topic)
        segs = list(c.segment_ids or [])
        reused = {int(e["segment_id"]) for e in entries_of(c) if e.get("reused") and e.get("segment_id")}
    _set_drive(cid, {"status": "uploading", "error": None})
    try:
        service = get_drive_service()
        parent = drive_store.folder_id(service)
        q = (f"name = 'Countdowns' and '{parent}' in parents and mimeType = 'application/vnd.google-apps.folder' "
             "and trashed = false")
        found = service.files().list(q=q, fields="files(id)", includeItemsFromAllDrives=True,
                                     **drive_store.ALL).execute().get("files", [])
        folder = found[0]["id"] if found else service.files().create(
            body={"name": "Countdowns", "mimeType": "application/vnd.google-apps.folder", "parents": [parent]},
            fields="id", **drive_store.ALL).execute()["id"]
        stamp = _now().strftime("%Y-%m-%d")
        v = drive_store._upload(service, video, folder, f"{title} ({stamp}).mp4")
        t = drive_store._upload(service, thumb, folder, f"{title} — Thumbnail ({stamp}).jpg") if thumb.exists() else None
    except control.Cancelled:
        _set_drive(cid, {"status": "stopped", "error": "stopped"})
        raise
    except Exception as exc:  # noqa: BLE001
        why = drive_store._explain(exc) if not isinstance(exc, drive_store.DriveStoreError) else str(exc)
        _set_drive(cid, {"status": "error", "error": why})
        logbus.log("error", "countdown_drive_failed", f"Countdown #{cid}: {why}")
        raise
    info = {"status": "saved", "error": None, "link": v.get("webViewLink"), "file_id": v["id"],
            "thumb_link": t.get("webViewLink") if t else None, "rendered_at": rendered_at, "saved_at": _now().isoformat()}
    _set_drive(cid, info)
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        if c.queue_item_id and (item := s.get(QueueItem, c.queue_item_id)) and item.status not in ("posted", "archived"):
            item.drive_link = info["link"]
            s.commit()
    for pid in segs:                      # the video is safe in Drive: free each segment's trailer footage
        if int(pid) not in reused:
            try:
                archive.free_footage(int(pid))
            except Exception:  # noqa: BLE001
                pass
    logbus.log("info", "countdown_drive_saved", f"Countdown #{cid}: saved to Drive (LongForm Studio / Countdowns)")
    return info


def _set_drive(cid: int, info: dict[str, Any]) -> None:
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        if c and c.render:
            r = dict(c.render)
            r["drive"] = {**(r.get("drive") or {}), **info}
            c.render = r
            s.commit()


def start_drive(cid: int) -> str | None:
    if control.running("countdown_drive", cid):
        return None
    return control.start_thread("countdown_drive", f"Countdown #{cid}: save to Drive", "countdown", drive_job, cid, ref=cid)


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


def sweep_posted(s) -> None:
    """Like LongForm: note when a countdown posts; N days later (studio archive setting), once
    its Drive copy is confirmed, delete the local video (thumbnails, titles and script stay)."""
    from . import archive
    cfg = archive.settings_dict(s)
    now = _now()
    for c in s.query(CountdownProject).filter(CountdownProject.queue_item_id.isnot(None)).all():
        rv = dict(c.review or {})
        if not rv.get("posted_at"):
            q = s.get(QueueItem, c.queue_item_id)
            if q is not None and q.status in ("posted", "archived"):
                rv["posted_at"] = (q.published_at or now).isoformat()
                c.review = rv
                s.commit()
            continue
        r = c.render or {}
        if not cfg.get("enabled") or r.get("archived_at") or (r.get("drive") or {}).get("status") != "saved":
            continue
        posted = datetime.datetime.fromisoformat(rv["posted_at"])
        posted = posted if posted.tzinfo else posted.replace(tzinfo=UTC)
        if now - posted >= datetime.timedelta(days=int(cfg.get("days") or 14)):
            (comp_dir(c) / "final_compilation.mp4").unlink(missing_ok=True)
            c.render = {**r, "archived_at": now.isoformat()}
            s.commit()
            logbus.log("info", "countdown_archived", f"Countdown #{c.id}: local video removed {cfg.get('days')} days after "
                                                     f"posting (the copy is in Drive)")


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
        # The old Save gave every save a new id, so one list could be saved many times:
        # match on topic + titles too, so each list comes over once.
        sig = lambda cd: (cd.get("topic"), tuple(sorted(str(e.get("title")) for e in cd.get("entries") or [])))
        seen_sigs = {sig(c.ranking or {}) for c in seen.values()}
        saved = s.get(AppSetting, "saved_countdowns")
        for cd in ((saved.value or {}).get("items") or []) if saved else []:
            key = cd.get("id")
            if key in seen or not cd.get("entries") or sig(cd) in seen_sigs:
                continue
            seen_sigs.add(sig(cd))
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


# --- 🤖 automation (owner, 2026-10-13: like LongForm; 1 a day; waits for you before render) ---------
AUTO_KEY = "countdown_auto"
AUTO_DEFAULTS = {"enabled": True, "per_day": 1, "paused_until": None}
SEGMENT_USD = 0.13                     # observed: ~$0.12–0.18 per segment (research + shots + script + voice)
auto_state: dict[str, Any] = {"last_check_at": None, "last_result": None, "last_started": None}


def auto_settings(s=None) -> dict[str, Any]:
    own = s is None
    s = s or SessionLocal()
    try:
        row = s.get(AppSetting, AUTO_KEY)
        return {**AUTO_DEFAULTS, **((row.value or {}) if row else {})}
    finally:
        if own:
            s.close()


def save_auto(patch: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as s:
        row = s.get(AppSetting, AUTO_KEY)
        val = {**auto_settings(s), **patch}
        if row:
            row.value = val
        else:
            s.add(AppSetting(key=AUTO_KEY, value=val))
        s.commit()
    return auto_status()


def estimate_usd(fmt: str) -> float:
    return round(0.02 + COUNTS.get(fmt, 10) * SEGMENT_USD, 2)


def started_today(s) -> int:
    start, end = costs.day_bounds()
    return s.query(CountdownProject).filter(CountdownProject.created_at >= start, CountdownProject.created_at < end).count()


def in_progress(s) -> CountdownProject | None:
    """A countdown still being made by the automation's steps (one at a time)."""
    for c in s.query(CountdownProject).order_by(CountdownProject.id).all():
        if (c.review or {}).get("delete_after"):
            continue
        if c.stage in ("new", "research") and c.stage_status in ("running", "paused"):
            return c
        if c.stage == "segments" and c.stage_status in ("waiting", "running", "paused"):
            return c
    return None


def _fmt_for(topic: dict[str, Any]) -> str:
    sizes = topic.get("sizes") or [5]
    want = COUNTS.get(topic.get("best_format") or "top10", 10)
    n = max([x for x in sizes if x <= want] or [min(sizes)])
    return f"top{n}"


LINE_KEY = "countdown_line"


def line(s=None) -> list[dict[str, Any]]:
    own = s is None
    s = s or SessionLocal()
    try:
        row = s.get(AppSetting, LINE_KEY)
        return list((row.value or {}).get("items") or []) if row else []
    finally:
        if own:
            s.close()


def _save_line(items: list[dict[str, Any]]) -> None:
    with SessionLocal() as s:
        row = s.get(AppSetting, LINE_KEY)
        if row:
            row.value = {"items": items}
        else:
            s.add(AppSetting(key=LINE_KEY, value={"items": items}))
        s.commit()


def line_add(topic: str, fmt: str, sig: str | None = None, notes: str = "") -> list[dict[str, Any]]:
    from . import countdown_topics
    items = line()
    sg = sig or countdown_topics.sig(topic)
    if any(i["sig"] == sg for i in items):
        raise HTTPException(409, "That topic is already in the line")
    items.append({"id": f"l{int(_now().timestamp() * 1000)}", "topic": topic[:255], "fmt": fmt if fmt in COUNTS else "top10",
                  "sig": sg, "from_topics": bool(sig), "notes": notes[:500], "added_at": _now().isoformat()})
    _save_line(items)
    if sig:
        countdown_topics.mark(sig, "made")          # leaves the topic list (it's in your line now)
    return items


def _topic_for(item: dict[str, Any]) -> dict[str, Any]:
    from . import countdown_topics
    t = countdown_topics.find(item["sig"]) if item.get("from_topics") else None
    return {**(t or {}), "topic": item["topic"], "sig": item["sig"], "fmt": item["fmt"], "notes": item.get("notes") or ""}


def next_pick() -> dict[str, Any] | None:
    """Your line first (in its order), then the best topic with real search demand."""
    from . import countdown_topics
    items = line()
    if items:
        return {**_topic_for(items[0]), "line_id": items[0]["id"], "score": None}
    for t in countdown_topics.listing()["topics"]:
        if t.get("sizes") and (t.get("checks", {}).get("demand", {}).get("status") != "fail"):
            return {**t, "fmt": _fmt_for(t)}
    return None


def _wait_reason(s, cfg: dict[str, Any], pick: dict[str, Any] | None) -> str | None:
    n, lim = started_today(s), int(cfg["per_day"])
    if n >= lim:
        return f"today's limit reached ({n} of {lim} started)"
    busy = in_progress(s)
    if busy:
        return f"countdown #{busy.id} is still being made"
    if pick is None:
        return "no topic passes the checks — press ↻ Refresh topics"
    need = estimate_usd(pick["fmt"])
    ccfg = costs.settings_dict(s)
    cap = costs.effective_cap(ccfg)
    left = cap - costs.today_spend(s) if cap else 999.0
    if left < need:
        return f"not enough budget left today for a {pick['fmt'].replace('top', 'Top ')} (~${need:.2f} needed, ${max(left, 0):.2f} left)"
    return None


def create_from_topic(topic: dict[str, Any], fmt: str, auto: bool = False) -> int:
    from . import countdown_topics
    titles = ", ".join(t["title"] for t in (topic.get("titles") or [])[:countdown_topics.ASK_TITLES])
    notes = " ".join(x for x in ((f"Rank from these real titles where they fit (checked on TMDB): {titles}." if titles else ""),
                                 topic.get("notes") or "") if x)
    with SessionLocal() as s:
        c = CountdownProject(topic=countdown_topics.sized(topic["topic"], COUNTS.get(fmt, 10))[:255], fmt=fmt, notes=notes or None,
                             review={"auto": auto, "topic_sig": topic.get("sig"), "keyword": topic.get("keyword"),
                                     "keywords": topic.get("keywords") or [], "demand": topic.get("demand")})
        s.add(c)
        s.commit()
        cid = c.id
    if topic.get("sig"):
        countdown_topics.mark(topic["sig"], "made")
    start_research(cid)
    return cid


def auto_tick() -> dict[str, Any] | None:
    from . import countdown_topics
    now = _now()
    auto_state["last_check_at"] = now.isoformat()
    if countdown_topics.is_stale(now):
        countdown_topics.refresh_async()
    cfg = auto_settings()
    if not cfg["enabled"]:
        auto_state["last_result"] = "off"
        return None
    pu = cfg.get("paused_until")
    if pu and now < datetime.datetime.fromisoformat(pu):
        auto_state["last_result"] = f"paused for today (until {pu[:16]} UTC)"
        return None
    pick = next_pick()
    with SessionLocal() as s:
        reason = _wait_reason(s, cfg, pick)
    if reason:
        auto_state["last_result"] = f"waiting: {reason}"
        return None
    cid = create_from_topic(pick, pick["fmt"], auto=True)
    if pick.get("line_id"):
        _save_line([i for i in line() if i["id"] != pick["line_id"]])
    started = {"countdown_id": cid, "topic": pick["topic"], "fmt": pick["fmt"], "score": pick["score"], "at": now.isoformat(),
               "from_line": bool(pick.get("line_id"))}
    auto_state["last_started"] = started
    auto_state["last_result"] = f"started #{cid} {pick['topic']}"
    logbus.log("info", "countdown_auto_started", f"Countdown automation started #{cid}: {pick['topic']} ({pick['fmt']})")
    return started


def auto_titles(cid: int) -> None:
    """After research on an automatic countdown: keep real titles, fill gaps from the topic's
    checked titles, then confirm — or stop at step 2 for the owner if it can't be filled."""
    from . import countdown_topics
    with SessionLocal() as s:
        c = s.get(CountdownProject, cid)
        need = COUNTS.get(c.fmt, 10)
        ents = [e for e in sorted(entries_of(c), key=lambda e: int(e.get("rank") or 99)) if e.get("tmdb_id")]
        have = {(e.get("tmdb_id"), e.get("media_type")) for e in ents}
        topic = countdown_topics.find((c.review or {}).get("topic_sig") or "") or {}
        for t in topic.get("titles") or []:
            if len(ents) >= need:
                break
            if (t["tmdb_id"], t.get("media_type")) not in have:
                ents.append({"title": t["title"], "year": t.get("year"), "tmdb_id": t["tmdb_id"],
                             "media_type": t.get("media_type", "movie"), "poster": t.get("poster"),
                             "why_it_ranks": "", "added_by": "automation"})
                have.add((t["tmdb_id"], t.get("media_type")))
        if len(ents) < need:
            smaller = max([n for n in (5, 10, 15) if n <= len(ents)] or [0])
            if not smaller:
                c.stage_status, c.stage_message = "done", (f"👀 Only {len(ents)} real titles found — add or swap titles "
                                                           f"in step 2, then Confirm titles.")
                s.commit()
                return
            c.fmt = f"top{smaller}"
            need = smaller
        ents = ents[:need]
        for i, e in enumerate(ents):
            e["rank"] = i + 1
        r = dict(c.ranking or {})
        r["entries"] = ents
        c.ranking = r
        confirm_titles(s, c)
        c.stage_message = (f"🤖 Titles checked and confirmed automatically ({len(ents)}). Segments are made one at a time; "
                           f"it stops for your review before the final render.")
        s.commit()
    logbus.log("info", "countdown_auto_titles", f"Countdown #{cid}: titles confirmed automatically")


def auto_status() -> dict[str, Any]:
    from . import countdown_topics
    cfg = auto_settings()
    pick = next_pick()
    with SessionLocal() as s:
        reason = _wait_reason(s, cfg, pick) if cfg["enabled"] else "automation is off"
        today = started_today(s)
    return {"settings": cfg, "state": dict(auto_state), "today": today,
            "next": ({"topic": pick["topic"], "fmt": pick["fmt"], "score": pick["score"], "sig": pick["sig"],
                      "estimate_usd": estimate_usd(pick["fmt"]), "from_line": bool(pick.get("line_id"))} if pick else None),
            "line": line(),
            "waiting": reason, "topics_built_at": countdown_topics.stored().get("built_at")}


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


class AutoIn(BaseModel):
    enabled: bool | None = None
    per_day: int | None = None


class TopicStart(BaseModel):
    sig: str
    fmt: str


class TopicMark(BaseModel):
    kind: str          # pinned | skipped
    on: bool = True


@router.get("/auto")
def get_auto() -> dict[str, Any]:
    return auto_status()


@router.put("/auto")
def put_auto(req: AutoIn) -> dict[str, Any]:
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if "per_day" in patch and not 0 <= int(patch["per_day"]) <= 5:
        raise HTTPException(400, "per_day must be 0–5")
    logbus.log("info", "countdown_auto_settings", f"Countdown automation: {patch}")
    return save_auto(patch)


@router.post("/auto/pause-today")
def pause_today(req: FlagIn) -> dict[str, Any]:
    _, midnight = costs.day_bounds()
    return save_auto({"paused_until": midnight.isoformat() if req.on else None})


@router.post("/auto/skip-next")
def skip_next() -> dict[str, Any]:
    from . import countdown_topics
    pick = next_pick()
    if not pick:
        raise HTTPException(400, "there is no next pick to skip")
    if pick.get("line_id"):
        _save_line([i for i in line() if i["id"] != pick["line_id"]])
    countdown_topics.mark(pick["sig"], "skipped")
    logbus.log("info", "countdown_auto_skipped", f"Countdown automation: skipped {pick['topic']}")
    return auto_status()


@router.get("/topics")
def get_topics() -> dict[str, Any]:
    from . import countdown_topics
    return countdown_topics.listing()


@router.post("/topics/refresh")
def refresh_topics() -> dict[str, Any]:
    from . import countdown_topics
    job = countdown_topics.refresh_async()
    return {"started": bool(job), "job_id": job}


@router.put("/topics/{topic_id}/mark")
def mark_topic(topic_id: str, req: TopicMark) -> dict[str, Any]:
    from . import countdown_topics
    if req.kind not in ("pinned", "skipped"):
        raise HTTPException(400, "kind must be pinned or skipped")
    countdown_topics.mark(topic_id, req.kind, req.on)
    return countdown_topics.listing()


@router.post("/topics/start")
def start_topic(req: TopicStart) -> dict[str, Any]:
    """Make a countdown from a checked topic at Top 5 / 10 / 15."""
    from . import countdown_topics
    t = countdown_topics.find(req.sig)
    if not t:
        raise HTTPException(404, "topic not found — refresh the list")
    n = COUNTS.get(req.fmt)
    if not n or n not in (t.get("sizes") or []):
        raise HTTPException(400, f"This topic has {len(t.get('titles') or [])} real titles — it can't fill a Top {n or '?'}")
    cid = create_from_topic(t, req.fmt, auto=False)
    with SessionLocal() as s:
        return out(s, s.get(CountdownProject, cid))


class BatchIn(BaseModel):
    action: str            # render | publish | review | archive | unarchive | delete
    ids: list[int]


class SettingsIn(BaseModel):
    segment_minutes: float | None = None


@router.post("/batch")
def batch(req: BatchIn) -> dict[str, Any]:
    """Like LongForm's Your videos: one action on many countdowns; one result each."""
    results = []
    for cid in req.ids:
        try:
            with SessionLocal() as s:
                c = _get(s, cid)
                title = (c.script or {}).get("title") or c.topic
                if req.action == "render":
                    if not out(s, c, full=False)["has"]["segments"]:
                        raise ValueError("segments aren't all made yet")
                    if _running(c):
                        raise ValueError("busy")
                elif req.action == "publish":
                    publish(s, c)
                elif req.action == "review":
                    c.review = {**(c.review or {}), "reviewed_at": _now().isoformat()}
                    s.commit()
                elif req.action in ("archive", "unarchive"):
                    rv = dict(c.review or {})
                    if req.action == "archive":
                        rv["delete_after"] = (_now() + datetime.timedelta(days=ARCHIVE_DAYS)).isoformat()
                    else:
                        rv.pop("delete_after", None)
                    c.review = rv
                    s.commit()
                elif req.action == "delete":
                    delete_now(s, c, "deleted by owner (batch)")
                else:
                    raise ValueError("unknown action")
            if req.action == "render":
                start_render(cid)
            results.append({"id": cid, "title": title, "ok": True})
        except (ValueError, HTTPException) as exc:
            results.append({"id": cid, "ok": False, "why": getattr(exc, "detail", None) or str(exc)})
    return {"results": results, "done": sum(1 for r in results if r["ok"])}


@router.post("/{cid}/resume")
def resume(cid: int) -> dict[str, Any]:
    """▶ Resume a stopped, failed or paused countdown from the step it was on."""
    with SessionLocal() as s:
        c = _get(s, cid)
        stage = c.stage
    if stage in ("new", "research"):
        start_research(cid)
    elif stage == "segments":
        return make_segments(cid)
    elif stage == "render":
        start_render(cid)
    with SessionLocal() as s:
        return out(s, _get(s, cid))


@router.post("/{cid}/move-up")
def move_up(cid: int) -> dict[str, Any]:
    """⏫ Put this countdown's waiting segment at the front of Studio's line."""
    from . import runner
    with SessionLocal() as s:
        c = _get(s, cid)
        queued = [x for x in _segments(s, c) if x["stage_status"] == "queued"]
    if not queued or not runner.move_to_front(queued[0]["id"]):
        raise HTTPException(409, "nothing of this countdown is waiting in Studio's line")
    return {"ok": True}


@router.put("/{cid}/settings")
def put_settings(cid: int, req: SettingsIn) -> dict[str, Any]:
    """Segment length (minutes) for segments not written yet."""
    with SessionLocal() as s:
        c = _get(s, cid)
        if req.segment_minutes is not None:
            if not 0.4 <= req.segment_minutes <= 3:
                raise HTTPException(400, "segment length must be 0.4–3 minutes")
            c.review = {**(c.review or {}), "segment_minutes": round(req.segment_minutes, 2)}
            fix_lengths(s, c)
        s.commit()
        return out(s, c)


@router.post("/{cid}/drive")
def drive(cid: int) -> dict[str, Any]:
    with SessionLocal() as s:
        if not _get(s, cid).render:
            raise HTTPException(400, "Render the video first")
    return {"job_id": start_drive(cid)}


@router.post("/{cid}/thumbnails/refresh")
def refresh_thumbnails(cid: int) -> dict[str, Any]:
    """🔄 New thumbnail options (different titles' posters and stills each press)."""
    from .compilations import stitch
    with SessionLocal() as s:
        c = _get(s, cid)
        if not c.render:
            raise HTTPException(400, "Render the video first")
        projects = [p for p in (s.get(StudioProject, int(x)) for x in c.segment_ids or []) if p is not None]
        rnd = int(c.render.get("thumb_round") or 0) + 1
        k = rnd % max(len(projects), 1)
        rotated = projects[k:] + projects[:k]
        title = (c.script or {}).get("thumbnail_text") or (c.script or {}).get("title") or c.topic
        d = comp_dir(c) / "thumbnails"
        stitch.generate_compilation_thumbnails(rotated, title, d)
        sel = c.render.get("selected_thumbnail") or "poster"
        shutil.copyfile(d / c.render["thumbnails"][sel], d / "thumbnail.jpg")
        c.render = {**c.render, "thumb_round": rnd, "thumb_v": _now().timestamp()}
        s.commit()
        return out(s, c)


@router.post("/{cid}/titles/score")
def score_titles(cid: int) -> dict[str, Any]:
    """vidIQ click-through score (0–100) for each title option (5 credits each, cached)."""
    from .. import vidiq
    with SessionLocal() as s:
        c = _get(s, cid)
        sc = dict(c.script or default_script(c))
        opts = list(dict.fromkeys([sc.get("title") or "", *(sc.get("title_options") or [])]))[:4]
    scores = {}
    for t in [o for o in opts if o]:
        r = vidiq.score_title(t, purpose=f"countdown #{cid} title")
        if r:
            scores[t] = r
    with SessionLocal() as s:
        c = _get(s, cid)
        c.script = {**(c.script or default_script(c)), "title_scores": scores}
        s.commit()
        return out(s, c)


@router.post("/{cid}/titles/suggest")
def suggest_titles(cid: int) -> dict[str, Any]:
    """vidIQ scored title ideas for this countdown (one call)."""
    from .. import vidiq
    with SessionLocal() as s:
        c = _get(s, cid)
        sc = dict(c.script or default_script(c))
        names = ", ".join(e.get("title", "") for e in sorted(entries_of(c), key=lambda e: e.get("rank", 99))[:10])
    raw = vidiq.safe("vidiq_generate_titles", {"title": sc.get("title") or c.topic, "numTitles": 5, "type": "long",
                                               "description": f"Countdown video: {c.topic}. Ranked: {names}",
                                               "language": "en"}, cache_hours=72, purpose=f"countdown #{cid} titles")
    if raw is None:
        raise HTTPException(502, "vidIQ couldn't suggest titles right now")
    data = raw if isinstance(raw, (dict, list)) else vidiq._embedded_json(raw) or raw
    items = data.get("titles") if isinstance(data, dict) else data
    found = []
    for it in items if isinstance(items, list) else []:
        t = it.get("title") if isinstance(it, dict) else str(it)
        if t:
            found.append(t[:100])
    with SessionLocal() as s:
        c = _get(s, cid)
        sc = dict(c.script or default_script(c))
        sc["title_options"] = list(dict.fromkeys([*(sc.get("title_options") or []), *found]))[:10]
        sc["vidiq_titles"] = data if isinstance(data, (dict, list)) else str(data)[:2000]
        c.script = sc
        s.commit()
        return out(s, c)


class LineIn(BaseModel):
    topic: str
    fmt: str = "top10"
    sig: str | None = None
    notes: str = ""


@router.get("/line")
def get_line() -> list[dict[str, Any]]:
    return line()


@router.post("/line")
def add_to_line(req: LineIn) -> dict[str, Any]:
    """+ Add to line: the automation makes it next (before its own pick), within the daily limit."""
    from . import countdown_topics
    topic = req.topic.strip()
    if req.sig:
        t = countdown_topics.find(req.sig)
        if t and COUNTS.get(req.fmt) not in (t.get("sizes") or []):
            raise HTTPException(400, f"This topic has {len(t.get('titles') or [])} real titles — it can't fill a {FMT_NAME(req.fmt)}")
        topic = topic or (t or {}).get("topic", "")
    if not topic:
        raise HTTPException(400, "Type a topic first")
    line_add(topic, req.fmt, req.sig, req.notes)
    logbus.log("info", "countdown_line_added", f"Countdown line: + {topic} ({req.fmt})")
    return auto_status()


@router.delete("/line/{item_id}")
def remove_from_line(item_id: str) -> dict[str, Any]:
    from . import countdown_topics
    items = line()
    gone = [i for i in items if i["id"] == item_id]
    _save_line([i for i in items if i["id"] != item_id])
    for i in gone:
        if i.get("from_topics"):
            countdown_topics.mark(i["sig"], "made", False)     # back in the topic list
    return auto_status()


@router.post("/line/{item_id}/up")
def line_up(item_id: str) -> dict[str, Any]:
    items = line()
    k = next((n for n, i in enumerate(items) if i["id"] == item_id), None)
    if k:
        items[k - 1], items[k] = items[k], items[k - 1]
        _save_line(items)
    return auto_status()


@router.post("/line/{item_id}/start")
def line_start(item_id: str) -> dict[str, Any]:
    """▶ Make now: start this line item right away (doesn't wait for the daily slot)."""
    item = next((i for i in line() if i["id"] == item_id), None)
    if not item:
        raise HTTPException(404, "not in the line")
    cid = create_from_topic(_topic_for(item), item["fmt"], auto=False)
    _save_line([i for i in line() if i["id"] != item_id])
    with SessionLocal() as s:
        return out(s, s.get(CountdownProject, cid))


def FMT_NAME(fmt: str) -> str:
    return {"top5": "Top 5", "top10": "Top 10", "top15": "Top 15"}.get(fmt, fmt)


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
        from .. import undo
        undo.record(s, f"countdown:{cid}", "Edit titles", rows=[c])
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
        from .. import undo
        undo.record(s, f"countdown:{cid}", "Edit script", rows=[c])
        sc = dict(c.script or default_script(c))
        for k in ("title", "intro", "outro"):
            v = getattr(req, k)
            if v is not None:
                sc[k] = v.strip()
        if any(sc.get(k) != (c.script or {}).get(k) for k in ("intro", "outro")):
            c.render = None
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
    return {"ok": True, "job_id": start_render(cid)}


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
