"""Bulk AI rewrite: Gemini title/description/hashtags for many queue items.

Runs in a background thread (hundreds of calls take minutes), 4 at a time,
one job at a time. Progress is in `status` for the UI to poll."""

from __future__ import annotations

import asyncio
import datetime
import logging
import threading
from typing import Any

from .. import control, logbus
from ..db import SessionLocal
from ..models import Clip, Compilation, QueueItem
from . import metadata

logger = logging.getLogger("scrapper.social.ai_bulk")

CONCURRENCY = 4
MAX_ITEMS = 1000  # batch cap per job

_lock = threading.Lock()
status: dict[str, Any] = {
    "running": False, "total": 0, "done": 0, "failed": 0, "errors": [],
    "started_at": None, "finished_at": None, "aborted": None,
}


def ai_inputs(item: QueueItem, s) -> list[str]:
    """What Gemini sees for an item: its title, or the compilation's clip titles."""
    titles = [item.title or item.video_name]
    if item.compilation_id:
        comp = s.get(Compilation, item.compilation_id)
        if comp and comp.clip_ids:
            clips = s.query(Clip).filter(Clip.id.in_(comp.clip_ids)).all()
            titles = [c.title for c in clips if c.title] or titles
    return titles


def apply_ai(item: QueueItem, res: dict[str, Any], research: dict[str, Any] | None = None) -> None:
    item.title = res.get("title", item.title)
    item.description = res.get("caption", item.description)
    item.tags = " ".join(res.get("hashtags", []))
    if research is not None:
        item.research = research


async def ai_for(item: QueueItem, s, cache: dict | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Metadata for one item. Clips: TMDB-researched (clip_research). Compilations:
    the clip-titles prompt. Raises metadata.MetadataError with a readable reason."""
    if item.compilation_id:
        return await metadata.generate_social_metadata(ai_inputs(item, s), strict=True), None
    from ..studio import gemini as sgem, tmdb
    from . import clip_research
    try:
        return await asyncio.to_thread(clip_research.generate, item, cache)
    except (sgem.GeminiError, tmdb.TMDBError) as exc:
        raise metadata.MetadataError(str(exc)) from exc


_cache: dict = {}  # TMDB lookups shared across one bulk run (many clips, few titles)


_rid: int | None = None   # ResumableJob row of the running bulk run


async def _one(item_id: int, sem: asyncio.Semaphore) -> None:
    async with sem:
        if control.stopped():
            status["aborted"] = "stopped by user"
        if status["aborted"]:
            return
        with SessionLocal() as s:
            item = s.get(QueueItem, item_id)
            if not item:
                status["failed"] += 1
                return
            try:
                from .. import costs
                with costs.operation("queue:bulk_ai", ref=f"queue:{item_id}"):
                    res, research = await ai_for(item, s, _cache)
                apply_ai(item, res, research)
                s.commit()
                status["done"] += 1
                from .. import resume
                resume.add_done(_rid, item_id)        # a resume after a restart skips it
            except metadata.MetadataError as exc:
                status["failed"] += 1
                if len(status["errors"]) < 5:
                    status["errors"].append(f"#{item_id}: {str(exc)[:200]}")
                if "GEMINI_API_KEY" in str(exc) or "Monthly budget reached" in str(exc):
                    status["aborted"] = str(exc)  # every call would fail the same way


async def _run(ids: list[int]) -> None:
    _cache.clear()
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*(_one(i, sem) for i in ids))


def start(ids: list[int], rid: int | None = None, already_done: list[int] | None = None) -> dict[str, Any]:
    """Recorded as a ResumableJob: after a restart it continues with the videos
    it hadn't finished (already_done = the checkpoint)."""
    global _rid
    from .. import resume
    ids = list(dict.fromkeys(ids))[:MAX_ITEMS]
    todo = [i for i in ids if i not in set(already_done or [])]
    with _lock:
        if status["running"]:
            raise RuntimeError("A bulk AI rewrite is already running")
        status.update(running=True, total=len(todo), done=0, failed=0, errors=[], aborted=None,
                      started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), finished_at=None)
    if rid:
        resume.reopen(rid)
    else:
        rid = resume.begin("bulk_ai", f"AI rewrite of {len(ids)} video(s)", {"ids": ids})
    _rid = rid
    label = f"AI rewrite of {len(todo)} video(s)" + (" (resumed after a restart)" if already_done is not None else "")

    def work() -> None:
        outcome = "done"
        try:
            with control.job("ai", label, scope="socialpilot"):
                asyncio.run(_run(todo))
            if status["aborted"]:
                outcome = "stopped" if "stopped" in status["aborted"] else "failed"
        except control.Cancelled:
            status["aborted"] = "stopped by user"
            outcome = "stopped"
        except Exception as exc:  # never leave the job stuck "running"
            logger.exception("bulk AI crashed")
            status["aborted"] = str(exc)[:200]
            outcome = "failed"
        finally:
            resume.finish(rid, outcome, status["aborted"])
            status["running"] = False
            status["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            logbus.log("info" if not status["failed"] else "error", "queue_bulk_ai",
                       f"AI rewrite: {status['done']}/{status['total']} done, {status['failed']} failed"
                       + (f" (stopped: {status['aborted']})" if status["aborted"] else ""))

    threading.Thread(target=work, daemon=True, name="bulk_ai").start()
    return dict(status)


def _resume(p: dict, cp: dict, rid: int) -> dict[str, Any]:
    return start(p.get("ids", []), rid=rid, already_done=cp.get("done", []))


from .. import resume as _resume_mod  # noqa: E402  (registered at import)
_resume_mod.launcher("bulk_ai")(_resume)
