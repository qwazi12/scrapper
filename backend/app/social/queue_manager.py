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
from ..models import Clip, Compilation, QueueItem, SocialPost
from . import upload_post

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
TICK_SECONDS = 30


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
    per slot. Items already due keep their slot; items with no accounts picked
    get no slot (nothing is ever posted to accounts the owner didn't choose).
    Returns rows changed."""
    ready = (
        s.query(QueueItem)
        .filter(QueueItem.status == "ready")
        .order_by(func.coalesce(QueueItem.position, QueueItem.id), QueueItem.id)
        .all()
    )
    groups: dict[str, list[QueueItem]] = {}
    changed = 0
    for it in ready:
        if not it.accounts:
            if it.scheduled_at is not None:
                it.scheduled_at = None
                changed += 1
            continue
        if not _is_due(it, now):
            groups.setdefault(pipeline_group(it), []).append(it)

    for items in groups.values():
        for it, slot in zip(items, slots_after(now, len(items))):
            if _aware(it.scheduled_at) != slot:
                it.scheduled_at = slot
                changed += 1
    if changed:
        s.commit()
    return changed


def _hashtags(tags: str | None) -> list[str]:
    return [t.lstrip("#") for t in (tags or "").split() if t.lstrip("#")]


async def submit_upload(
    video_path: pathlib.Path,
    accounts: list[str],
    *,
    title: str,
    description: str,
    tags: list[str],
    scheduled_date: str | None = None,
) -> list[dict[str, Any]]:
    """Send the video to Upload-Post: one async upload per profile.

    Returns one entry per profile — {"profile", "request_id", "submitted_at"} or
    {"profile", "error"} — so a failed profile is retried later on its own.
    Raises if the picked accounts aren't valid/connected (nothing is sent)."""
    if not accounts:
        raise upload_post.UploadPostError("No accounts picked for this video", status_code=400)
    # Whole-profile picks ("default:*") expand to that profile's channels now.
    accounts = upload_post.expand_targets(accounts, await upload_post.list_accounts())

    now = datetime.datetime.now(UTC).isoformat()
    entries: list[dict[str, Any]] = []
    for profile, platforms in upload_post.group_by_profile(accounts).items():
        try:
            rid = await upload_post.upload_video(
                video_path, profile=profile, platforms=platforms, title=title,
                description=description, tags=tags, privacy=settings.publish_privacy,
                scheduled_date=scheduled_date,
            )
            entries.append({"profile": profile, "platforms": platforms, "request_id": rid,
                            "submitted_at": scheduled_date or now})  # timeout counts from go-live
        except Exception as exc:
            entries.append({"profile": profile, "platforms": platforms, "error": str(exc)[:300]})
    return entries


async def publish_queue_item(item_id: int, s: Session) -> QueueItem:
    """Submit a queue item to Upload-Post for the accounts the owner picked."""
    item = s.get(QueueItem, item_id)
    if not item:
        raise ValueError(f"Queue item {item_id} not found")
    if not item.accounts:
        raise upload_post.UploadPostError(
            "No accounts picked. Choose where this video posts (Posts To column, or 🔗 Set Target Accounts).", status_code=400)

    # Locate video file
    video_path: pathlib.Path | None = None
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

    item.status = "posting"
    s.commit()
    logbus.log("info", "queue_posting", f"Posting item #{item.id} ('{item.title[:40]}')")

    try:
        description = "\n\n".join(p.strip() for p in (item.description, item.tags) if p and p.strip())
        entries = await submit_upload(
            video_path, list(item.accounts), title=item.title or item.video_name,
            description=description, tags=_hashtags(item.tags),
        )
        if not any(e.get("request_id") for e in entries):
            raise upload_post.UploadPostError("; ".join(e.get("error", "") for e in entries))
        # Accepted is not published: stay "posting" until reconcile_posting()
        # reads every platform's real result. Only then "posted" (4-day clock).
        item.publish_requests = entries
        item.published_at = None
        item.notes = (f"Submitted to Upload-Post for {', '.join(item.accounts)}; "
                      "waiting for the platforms to confirm.")
        s.commit()
        logbus.log("info", "queue_submitted", f"Item #{item.id} submitted to Upload-Post",
                   requests=[e.get("request_id") for e in entries])
        return item

    except Exception as exc:
        logger.exception("Failed to post queue item #%s: %s", item.id, exc)
        item.status = "retry"
        item.notes = f"Publish failed: {str(exc)[:300]}"
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


PENDING_TIMEOUT = datetime.timedelta(hours=3)


def resolve_results(
    entries: list[dict[str, Any]], statuses: dict[str, dict[str, Any]], now: datetime.datetime,
) -> dict[str, Any] | None:
    """Combine Upload-Post status responses into per-account outcomes. The
    accounts judged are exactly the ones submitted (whole-profile picks were
    expanded at submit time and recorded in each entry's platforms).

    Returns None while any request is still processing (and not timed out),
    else {"ok": [(acc, url)], "failed": {acc: reason}}."""
    accounts = [f"{e['profile']}:{net}" for e in entries for net in e.get("platforms", [])]
    outcome: dict[str, tuple[bool, str]] = {}
    for e in entries:
        if "error" in e:
            for net in e.get("platforms", []):
                outcome[f"{e['profile']}:{net}"] = (False, f"upload not accepted: {e['error']}")
            continue
        st = statuses.get(e["request_id"]) or {}
        if st.get("status") != "completed":
            submitted = _aware(datetime.datetime.fromisoformat(e["submitted_at"].replace("Z", "+00:00")))
            if now - submitted < PENDING_TIMEOUT:
                return None
            for net in e.get("platforms", []):
                outcome[f"{e['profile']}:{net}"] = (False, "Upload-Post still processing after 3h")
            continue
        for r in st.get("results") or []:
            acc = f"{r.get('profile_username') or e['profile']}:{r.get('platform')}"
            if r.get("success"):
                outcome[acc] = (True, r.get("post_url") or "")
            else:
                outcome[acc] = (False, str(r.get("error_message") or "failed"))
    ok = [(a, outcome[a][1]) for a in accounts if a in outcome and outcome[a][0]]
    failed = {a: (outcome[a][1] if a in outcome else "no result returned") for a in accounts
              if not (a in outcome and outcome[a][0])}
    return {"ok": ok, "failed": failed}


def _fetch_statuses(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for e in entries:
        rid = e.get("request_id")
        if rid:
            out[rid] = asyncio.run(upload_post.get_status(rid))
    return out


def apply_result(item: QueueItem, res: dict[str, Any], now: datetime.datetime) -> None:
    parts = [f"✓ {a} {url}".strip() for a, url in res["ok"]]
    parts += [f"✕ {a}: {why[:220]}" for a, why in res["failed"].items()]
    if not res["failed"]:
        item.status = "posted"
        item.published_at = now
    else:
        # Retry only what failed, so a retry never double-posts.
        item.status = "retry"
        item.accounts = list(res["failed"])
        item.published_at = None
    item.notes = " | ".join(parts)[:1000]


def reconcile_posting(s: Session, now: datetime.datetime) -> int:
    """Settle submitted queue items and compilation posts from Upload-Post status."""
    settled = 0
    for item in s.query(QueueItem).filter(QueueItem.status == "posting").all():
        if not item.publish_requests:
            continue  # pre-migration or mid-submit; nothing to poll
        try:
            res = resolve_results(item.publish_requests, _fetch_statuses(item.publish_requests), now)
        except Exception as exc:
            logger.warning("Upload-Post status check failed for #%s: %s", item.id, exc)
            continue
        if res is None:
            continue
        apply_result(item, res, now)
        s.commit()
        settled += 1
        logbus.log("info" if item.status == "posted" else "error",
                   "queue_posted" if item.status == "posted" else "queue_post_failed",
                   f"Item #{item.id}: {item.notes}")

    for post in s.query(SocialPost).filter(SocialPost.status == "submitted").all():
        if not post.publish_requests:
            continue
        try:
            res = resolve_results(post.publish_requests, _fetch_statuses(post.publish_requests), now)
        except Exception as exc:
            logger.warning("Upload-Post status check failed for post %s: %s", post.id, exc)
            continue
        if res is None:
            continue
        post.status = "published" if not res["failed"] else ("partial" if res["ok"] else "failed")
        post.error = "; ".join(f"{a}: {w}" for a, w in res["failed"].items())[:1000] or None
        s.commit()
        settled += 1
        logbus.log("info" if post.status == "published" else "error", "social_post_result",
                   f"Compilation post {post.id}: {post.status}")
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

# Heartbeat for the Settings page ("No silent work"): proves the auto-poster
# loop is alive and shows what it last did.
scheduler_status: dict[str, Any] = {
    "started_at": None, "last_tick_at": None, "last_error": None, "last_error_at": None,
    "ticks": 0, "last_submitted": None,
}


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
                scheduler_status["last_submitted"] = {"id": item.id, "at": now.isoformat()}
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
        scheduler_status["started_at"] = datetime.datetime.now(UTC).isoformat()
        while True:
            try:
                run_scheduler_tick()
            except Exception as exc:
                logger.error("Error in scheduler loop: %s", exc)
                scheduler_status["last_error"] = str(exc)[:300]
                scheduler_status["last_error_at"] = datetime.datetime.now(UTC).isoformat()
            scheduler_status["last_tick_at"] = datetime.datetime.now(UTC).isoformat()
            scheduler_status["ticks"] += 1
            time.sleep(TICK_SECONDS)

    t = threading.Thread(target=loop, daemon=True, name="posting_queue_scheduler")
    t.start()
