"""Bulk AI rewrite: Gemini title/description/hashtags for many queue items.

Runs in a background thread (hundreds of calls take minutes), 4 at a time,
one job at a time. Progress is in `status` for the UI to poll."""

from __future__ import annotations

import asyncio
import datetime
import logging
import threading
from typing import Any

from .. import logbus
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


def apply_ai(item: QueueItem, res: dict[str, Any]) -> None:
    item.title = res.get("title", item.title)
    item.description = res.get("caption", item.description)
    item.tags = " ".join(res.get("hashtags", []))


async def _one(item_id: int, sem: asyncio.Semaphore) -> None:
    async with sem:
        if status["aborted"]:
            return
        with SessionLocal() as s:
            item = s.get(QueueItem, item_id)
            if not item:
                status["failed"] += 1
                return
            try:
                res = await metadata.generate_social_metadata(ai_inputs(item, s), strict=True)
                apply_ai(item, res)
                s.commit()
                status["done"] += 1
            except metadata.MetadataError as exc:
                status["failed"] += 1
                if len(status["errors"]) < 5:
                    status["errors"].append(f"#{item_id}: {str(exc)[:200]}")
                if "GEMINI_API_KEY" in str(exc):
                    status["aborted"] = str(exc)  # every call would fail the same way


async def _run(ids: list[int]) -> None:
    sem = asyncio.Semaphore(CONCURRENCY)
    await asyncio.gather(*(_one(i, sem) for i in ids))


def start(ids: list[int]) -> dict[str, Any]:
    ids = list(dict.fromkeys(ids))[:MAX_ITEMS]
    with _lock:
        if status["running"]:
            raise RuntimeError("A bulk AI rewrite is already running")
        status.update(running=True, total=len(ids), done=0, failed=0, errors=[], aborted=None,
                      started_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), finished_at=None)

    def work() -> None:
        try:
            asyncio.run(_run(ids))
        except Exception as exc:  # never leave the job stuck "running"
            logger.exception("bulk AI crashed")
            status["aborted"] = str(exc)[:200]
        finally:
            status["running"] = False
            status["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            logbus.log("info" if not status["failed"] else "error", "queue_bulk_ai",
                       f"AI rewrite: {status['done']}/{status['total']} done, {status['failed']} failed"
                       + (f" (stopped: {status['aborted']})" if status["aborted"] else ""))

    threading.Thread(target=work, daemon=True, name="bulk_ai").start()
    return dict(status)
