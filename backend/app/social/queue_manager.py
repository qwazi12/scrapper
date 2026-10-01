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

from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import logbus
from ..config import settings
from ..db import SessionLocal
from ..models import Clip, Compilation, QueueItem
from . import outstand

logger = logging.getLogger("scrapper.social.queue")

# Drive subfolders of the "Movie Clips" pipeline (one per source channel).
MOVIE_CLIPS_CHANNELS = [
    "Movie Clips", "@AlphaReels-1", "@CoruscateCuts", "@EditAetheris",
    "@FrameLegion", "@PixelDrift-f3c", "@QianaLucy", "@SceneVale",
    "@SolarrEditss", "@TheUsJournal17", "@VynixAE", "@clipscav",
    "@comet-cinema", "@hanganhoang3071", "@roebutt"
]

# A slot is still "due" this long after its time — covers a restart or a slow
# tick without ever back-filling a whole day of missed slots at once.
DUE_GRACE = datetime.timedelta(minutes=30)
ARCHIVE_SWEEP_EVERY = datetime.timedelta(minutes=10)
ARCHIVE_SWEEP_BATCH = 50  # scope limit per sweep

UTC = datetime.timezone.utc


def pipeline_group(item: QueueItem) -> str:
    """Top-level pipeline an item posts under ("Movie Clips / @X" -> "Movie Clips")."""
    if item.pipeline in MOVIE_CLIPS_CHANNELS:
        return "Movie Clips"
    if item.pipeline and not item.pipeline.startswith("@"):
        return item.pipeline
    src = item.source or ""
    if " / " in src:  # Drive subfolder item: "<pipeline> / @channel"
        return src.split(" / ", 1)[0].strip()
    return item.pipeline or "default"


def _aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    # SQLite hands back naive datetimes; everything stored is UTC.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def slots_after(t: datetime.datetime, n: int) -> list[datetime.datetime]:
    """The next n posting slots strictly after t, as UTC datetimes."""
    tz = ZoneInfo(settings.post_timezone)
    local = t.astimezone(tz)
    day = local.date()
    out: list[datetime.datetime] = []
    while len(out) < n:
        for h in range(settings.post_start_hour, settings.post_end_hour + 1, settings.post_interval_hours):
            slot = datetime.datetime.combine(day, datetime.time(h), tzinfo=tz)
            if slot > local:
                out.append(slot.astimezone(UTC))
                if len(out) == n:
                    break
        day += datetime.timedelta(days=1)
    return out


def _is_due(item: QueueItem, now: datetime.datetime) -> bool:
    at = _aware(item.scheduled_at)
    return at is not None and now - DUE_GRACE < at <= now


def plan_schedule(s: Session, now: datetime.datetime) -> int:
    """Give every Ready item a slot: per pipeline, in posting order, one item
    per slot. Items already due keep their slot. Returns rows changed."""
    ready = (
        s.query(QueueItem)
        .filter(QueueItem.status == "ready")
        .order_by(func.coalesce(QueueItem.position, QueueItem.id), QueueItem.id)
        .all()
    )
    groups: dict[str, list[QueueItem]] = {}
    for it in ready:
        if not _is_due(it, now):
            groups.setdefault(pipeline_group(it), []).append(it)

    changed = 0
    for items in groups.values():
        for it, slot in zip(items, slots_after(now, len(items))):
            if _aware(it.scheduled_at) != slot:
                it.scheduled_at = slot
                changed += 1
    if changed:
        s.commit()
    return changed


async def publish_queue_item(item_id: int, s: Session) -> QueueItem:
    """Publish a specific queue item to Outstand."""
    item = s.get(QueueItem, item_id)
    if not item:
        raise ValueError(f"Queue item {item_id} not found")

    # Locate video file
    video_path: pathlib.Path | None = None
    thumb_path: pathlib.Path | None = None
    is_temp_download = False

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

    # If file not found locally on disk, stream/download from Google Drive
    if (not video_path or not video_path.exists()) and item.drive_link:
        try:
            logbus.log("info", "queue_drive_fetch", f"Fetching video #{item.id} from Google Drive...")
            from ..drive_sync import download_drive_file
            video_path = download_drive_file(item.drive_link)
            is_temp_download = True
        except Exception as exc:
            logger.error("Failed to fetch video from Drive: %s", exc)

    if not video_path or not video_path.exists():
        item.status = "error"
        item.notes = "Video file not found on volume, disk, or Google Drive."
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
        upload_res = await outstand.upload_media(video_path, upload_name=f"queue{item.id}_{video_path.name}")
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
            filename=upload_res.get("filename") or video_path.name,
        )

        outstand_id = post_res.get("post", {}).get("id") or post_res.get("id")
        if not outstand_id:
            raise outstand.OutstandError(f"Outstand returned no post id: {post_res}")
        # Outstand publishes asynchronously: an accepted request is not a live
        # post. Stay in "posting" until reconcile_posting() sees each account's
        # real result — only then is it "posted" (and the 4-day clock starts).
        item.outstand_post_id = str(outstand_id)
        item.published_at = None
        item.notes = (f"Submitted to Outstand (post {outstand_id}) for {len(item.accounts)} account(s); "
                      "waiting for the platforms to confirm.")
        s.commit()

        logbus.log("info", "queue_submitted", f"Item #{item.id} submitted to Outstand (post {outstand_id})")
        return item

    except Exception as exc:
        logger.exception("Failed to post queue item #%s: %s", item.id, exc)
        item.status = "retry"
        item.notes = f"Publish failed: {str(exc)[:200]}"
        s.commit()
        logbus.log("error", "queue_post_error", f"Item #{item.id} failed: {exc}")
        raise

    finally:
        if is_temp_download and video_path and video_path.exists():
            try:
                video_path.unlink(missing_ok=True)
                logger.info("Cleaned up temporary Drive download file: %s", video_path)
            except Exception:
                pass


def _describe(acct: dict[str, Any]) -> str:
    return f"{acct.get('network', '?')} {acct.get('username') or acct.get('nickname') or acct.get('id')}"


def apply_post_result(item: QueueItem, post: dict[str, Any], now: datetime.datetime) -> bool:
    """Fold Outstand's per-account results into the item. Returns False while
    any account is still pending."""
    accts = post.get("socialAccounts") or []
    if not accts or any(a.get("status") not in ("published", "failed") for a in accts):
        return False
    ok = [a for a in accts if a.get("status") == "published"]
    bad = [a for a in accts if a.get("status") == "failed"]
    parts = [f"✓ {_describe(a)} {a.get('platformPostUrl') or ''}".strip() for a in ok]
    for a in bad:
        err = str(a.get("error") or "unknown error")
        if "quota" in err.lower() or "429" in err:
            err = "daily upload quota exceeded — " + err[:160]
        parts.append(f"✕ {_describe(a)}: {err[:220]}")
    if not bad:
        item.status = "posted"
        item.published_at = now
    else:
        # Retry only the accounts that failed, so a retry never double-posts.
        item.status = "retry"
        item.accounts = [a["id"] for a in bad if a.get("id")]
        item.published_at = None
    item.notes = " | ".join(parts)[:1000]
    return True


def reconcile_posting(s: Session, now: datetime.datetime) -> int:
    """Check submitted posts with Outstand; settle them to posted/retry."""
    pending = (
        s.query(QueueItem)
        .filter(QueueItem.status == "posting")
        .filter(QueueItem.outstand_post_id.isnot(None))
        .all()
    )
    settled = 0
    for item in pending:
        try:
            post = asyncio.run(outstand.get_post(item.outstand_post_id))
        except Exception as exc:
            logger.warning("Outstand status check failed for #%s: %s", item.id, exc)
            continue
        if apply_post_result(item, post, now):
            s.commit()
            settled += 1
            logbus.log("info" if item.status == "posted" else "error",
                       "queue_posted" if item.status == "posted" else "queue_post_failed",
                       f"Item #{item.id}: {item.notes}")
    return settled


def sweep_archive(s: Session, now: datetime.datetime) -> int:
    """Delete posted/archived items ARCHIVE_DELETE_DAYS after posting, moving
    their Drive file to trash first (recoverable there for 30 days)."""
    if settings.archive_delete_days <= 0:
        return 0
    from ..drive_sync import trash_drive_file

    cutoff = now - datetime.timedelta(days=settings.archive_delete_days)
    expired = (
        s.query(QueueItem)
        .filter(QueueItem.status.in_(["posted", "archived"]))
        .filter(QueueItem.published_at.isnot(None))
        .filter(QueueItem.published_at < cutoff)
        .order_by(QueueItem.published_at)
        .limit(ARCHIVE_SWEEP_BATCH)
        .all()
    )
    removed = 0
    for item in expired:
        trashed = False
        if item.drive_link:
            try:
                trashed = trash_drive_file(item.drive_link)
            except Exception as exc:
                # Keep the row; the next sweep retries. Never orphan a Drive file.
                logbus.log("error", "archive_trash_failed", f"Item #{item.id}: {exc}")
                continue
        logbus.log(
            "info", "archive_deleted",
            f"Item #{item.id} ('{(item.title or '')[:40]}') removed {settings.archive_delete_days}d after posting"
            + (" — Drive file moved to trash" if trashed else ""),
            drive_link=item.drive_link, published_at=str(item.published_at),
        )
        s.delete(item)
        s.commit()
        removed += 1
    return removed


_last_archive_sweep: datetime.datetime | None = None


def run_scheduler_tick():
    """Plan slots, publish what's due (one per pipeline), sweep the archive."""
    global _last_archive_sweep
    now = datetime.datetime.now(UTC)
    with SessionLocal() as s:
        reconcile_posting(s, now)
        plan_schedule(s, now)

        due = (
            s.query(QueueItem)
            .filter(QueueItem.status == "ready")
            .filter(QueueItem.scheduled_at <= now)
            .filter(QueueItem.scheduled_at > now - DUE_GRACE)
            .order_by(func.coalesce(QueueItem.position, QueueItem.id), QueueItem.id)
            .all()
        )
        seen: set[str] = set()
        for item in due:
            group = pipeline_group(item)
            if group in seen:
                continue
            seen.add(group)
            try:
                asyncio.run(publish_queue_item(item.id, s))
            except Exception as exc:
                logger.error("Error executing scheduled queue item #%s: %s", item.id, exc)

        if _last_archive_sweep is None or now - _last_archive_sweep >= ARCHIVE_SWEEP_EVERY:
            _last_archive_sweep = now
            sweep_archive(s, now)


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
