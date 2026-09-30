"""Queue Manager & Scheduler for automated social media publishing.

Replaces the legacy Google Sheets posting queue with a resilient,
in-database queue with full status lifecycle:
  Review -> Ready to post -> Posting -> Posted / Retry / Error -> Archived
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import pathlib
import threading
import time
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from .. import logbus
from ..db import SessionLocal
from ..models import Clip, Compilation, QueueItem
from . import metadata, outstand

logger = logging.getLogger("scrapper.social.queue")


async def publish_queue_item(item_id: int, s: Session) -> QueueItem:
    """Publish a specific queue item to Outstand."""
    item = s.get(QueueItem, item_id)
    if not item:
        raise ValueError(f"Queue item {item_id} not found")

    # Locate video file
    video_path: pathlib.Path | None = None
    thumb_path: pathlib.Path | None = None

    if item.video_path and pathlib.Path(item.video_path).exists():
        video_path = pathlib.Path(item.video_path)
    elif item.compilation_id:
        comp = s.get(Compilation, item.compilation_id)
        if comp and comp.output_path and pathlib.Path(comp.output_path).exists():
            video_path = pathlib.Path(comp.output_path)
    elif item.clip_id:
        clip = s.get(Clip, item.clip_id)
        if clip and clip.file_path and pathlib.Path(clip.file_path).exists():
            video_path = pathlib.Path(clip.file_path)

    if not video_path or not video_path.exists():
        item.status = "error"
        item.notes = "Video file not found on volume or disk."
        s.commit()
        raise FileNotFoundError(f"Video file not found for queue item {item_id}")

    if not item.accounts:
        # If no accounts are assigned, fetch available accounts
        try:
            available = await outstand.list_social_accounts()
            active_ids = [a["id"] for a in available if a.get("isActive") not in (0, False)]
            if not active_ids:
                raise ValueError("No active social accounts found in Outstand.")
            item.accounts = active_ids
        except Exception as exc:
            item.status = "error"
            item.notes = f"Failed to retrieve target social accounts: {exc}"
            s.commit()
            raise

    item.status = "posting"
    s.commit()
    logbus.log("info", "queue_posting", f"Posting item #{item.id} ('{item.title[:40]}')")

    try:
        # Step 1: Upload to Outstand presigned storage
        upload_res = await outstand.upload_media(video_path)
        item.media_url = upload_res.get("url")

        # Step 2: Combine title, description, and tags into clean caption
        parts = []
        if item.title:
            parts.append(item.title.strip())
        if item.description:
            parts.append(item.description.strip())
        if item.tags:
            parts.append(item.tags.strip())
        full_content = "\n\n".join(parts) or item.title or "New video"

        # Step 3: Create post on Outstand
        post_res = await outstand.create_social_post(
            account_ids=item.accounts,
            content=full_content,
            media_url=item.media_url,
            filename=video_path.name,
        )

        outstand_id = post_res.get("post", {}).get("id") or post_res.get("id")
        item.outstand_post_id = str(outstand_id) if outstand_id else None
        item.status = "posted"
        item.published_at = datetime.datetime.now(datetime.timezone.utc)
        item.notes = f"Posted to Outstand post ID {outstand_id} across {len(item.accounts)} accounts."
        s.commit()

        logbus.log("info", "queue_posted", f"Item #{item.id} successfully posted (Outstand: {outstand_id})")
        return item

    except Exception as exc:
        logger.exception("Failed to post queue item #%s: %s", item.id, exc)
        item.status = "retry"
        item.notes = f"Publish failed: {str(exc)[:200]}"
        s.commit()
        logbus.log("error", "queue_post_error", f"Item #{item.id} failed: {exc}")
        raise


def run_scheduler_tick():
    """Poll for due queue items and publish them."""
    now = datetime.datetime.now(datetime.timezone.utc)
    with SessionLocal() as s:
        # Find items marked 'ready' whose scheduled_at is past or due
        due_items = (
            s.query(QueueItem)
            .filter(QueueItem.status == "ready")
            .filter(QueueItem.scheduled_at <= now)
            .order_by(QueueItem.scheduled_at)
            .limit(3)
            .all()
        )

        for item in due_items:
            try:
                asyncio.run(publish_queue_item(item.id, s))
            except Exception as exc:
                logger.error("Error executing scheduled queue item #%s: %s", item.id, exc)


_scheduler_running = False


def start_scheduler_thread():
    """Start background scheduler loop."""
    global _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True

    def loop():
        while True:
            try:
                run_scheduler_tick()
            except Exception as exc:
                logger.error("Error in scheduler loop: %s", exc)
            time.sleep(30)

    t = threading.Thread(target=loop, daemon=True, name="posting_queue_scheduler")
    t.start()
