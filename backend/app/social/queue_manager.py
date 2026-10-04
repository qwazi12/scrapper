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

from .. import backup, logbus
from ..config import settings
from ..db import SessionLocal
from ..models import AppSetting, Clip, Compilation, QueueItem, SocialPost
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
# Owner's choice 2026-10-03: a quiet check every 5 minutes (was 30 s). Any queue
# change (approve, edit, schedule, pause…) wakes the poster at once via wake(),
# so a newly approved video still gets its time slot immediately; posts go out
# within 5 minutes of their slot (DUE_GRACE covers 30).
TICK_SECONDS = 300
_wake = threading.Event()


def wake() -> None:
    """Run the scheduler now instead of at the next 5-minute check."""
    _wake.set()


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


# --- posting schedule config ---------------------------------------------
# Env vars are the defaults; the Settings page saves overrides in app_settings.
def default_schedule() -> dict[str, Any]:
    return {
        "timezone": settings.post_timezone,
        "start_hour": settings.post_start_hour,
        "end_hour": settings.post_end_hour,
        "interval_hours": settings.post_interval_hours,
        "posts_per_day": settings.post_posts_per_day or None,   # owner rule: 1/day unless changed by hand
        "pipelines": {},
        "accounts": {},
    }


def validate_schedule(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a clean config or raise ValueError with a readable reason."""
    try:
        tz = str(cfg["timezone"])
        start, end, step = int(cfg["start_hour"]), int(cfg["end_hour"]), int(cfg["interval_hours"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("timezone, start_hour, end_hour and interval_hours are required")
    try:
        ZoneInfo(tz)
    except Exception:
        raise ValueError(f"Unknown timezone: {tz}")
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise ValueError("Hours must be between 0 and 23")
    if start > end:
        raise ValueError("First slot must be at or before the last slot")
    if not (1 <= step <= 24):
        raise ValueError("Interval must be 1-24 hours")

    posts_per_day = cfg.get("posts_per_day")
    if posts_per_day is not None and posts_per_day != "":
        try:
            posts_per_day = int(posts_per_day)
            if not (1 <= posts_per_day <= 48):
                raise ValueError("posts_per_day must be between 1 and 48")
        except (TypeError, ValueError):
            raise ValueError("posts_per_day must be an integer between 1 and 48")
    else:
        posts_per_day = None

    def _clean_overrides(raw_dict: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw_dict, dict):
            return {}
        cleaned: dict[str, dict[str, Any]] = {}
        for k, v in raw_dict.items():
            if not isinstance(v, dict):
                continue
            entry: dict[str, Any] = {}
            if v.get("posts_per_day") is not None and v.get("posts_per_day") != "":
                ppd = int(v["posts_per_day"])
                if not (1 <= ppd <= 48):
                    raise ValueError(f"Override for {k}: posts_per_day must be 1-48")
                entry["posts_per_day"] = ppd
            if v.get("start_hour") is not None and v.get("start_hour") != "":
                sh = int(v["start_hour"])
                if not (0 <= sh <= 23):
                    raise ValueError(f"Override for {k}: start_hour must be 0-23")
                entry["start_hour"] = sh
            if v.get("end_hour") is not None and v.get("end_hour") != "":
                eh = int(v["end_hour"])
                if not (0 <= eh <= 23):
                    raise ValueError(f"Override for {k}: end_hour must be 0-23")
                entry["end_hour"] = eh
            if "start_hour" in entry and "end_hour" in entry and entry["start_hour"] > entry["end_hour"]:
                raise ValueError(f"Override for {k}: first slot must be <= last slot")
            if v.get("interval_hours") is not None and v.get("interval_hours") != "":
                ih = int(v["interval_hours"])
                if not (1 <= ih <= 24):
                    raise ValueError(f"Override for {k}: interval must be 1-24 hours")
                entry["interval_hours"] = ih
            if entry:
                cleaned[str(k)] = entry
        return cleaned

    raw_pipes = cfg.get("pipeline_overrides") or cfg.get("pipelines") or {}
    raw_accs = cfg.get("account_overrides") or cfg.get("accounts") or {}

    return {
        "timezone": tz,
        "start_hour": start,
        "end_hour": end,
        "interval_hours": step,
        "posts_per_day": posts_per_day,
        "pipelines": _clean_overrides(raw_pipes),
        "accounts": _clean_overrides(raw_accs),
    }


_schedule_cfg: dict[str, Any] = default_schedule()


def schedule_config() -> dict[str, Any]:
    return dict(_schedule_cfg)


def load_schedule(s: Session) -> dict[str, Any]:
    """Refresh the active schedule from the DB (falls back to env defaults)."""
    global _schedule_cfg
    row = s.get(AppSetting, "schedule")
    try:
        _schedule_cfg = validate_schedule(row.value) if row and row.value else default_schedule()
    except ValueError as exc:  # bad stored value: keep running on defaults, say so
        logger.error("Stored schedule invalid (%s); using env defaults", exc)
        _schedule_cfg = default_schedule()
    return schedule_config()


def save_schedule(s: Session, cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Persist a new schedule (None = back to env defaults); audit-logged."""
    before = schedule_config()
    row = s.get(AppSetting, "schedule")
    if cfg is None:
        if row:
            s.delete(row)
    else:
        cfg = validate_schedule(cfg)
        if row:
            row.value = cfg
        else:
            s.add(AppSetting(key="schedule", value=cfg))
    s.commit()
    after = load_schedule(s)
    logbus.log("info", "schedule_changed",
               f"Posting schedule {before['start_hour']}-{before['end_hour']}h every {before['interval_hours']}h "
               f"{before['timezone']} -> {after['start_hour']}-{after['end_hour']}h every "
               f"{after['interval_hours']}h {after['timezone']} (pacing: {after.get('posts_per_day') or 'interval'} posts/day)",
               before=before, after=after)
    return after


def autopost_paused(s: Session) -> bool:
    row = s.get(AppSetting, "autopost")
    return bool(row and (row.value or {}).get("paused"))


def set_autopost_paused(s: Session, paused: bool) -> None:
    row = s.get(AppSetting, "autopost")
    if row:
        row.value = {"paused": paused}
    else:
        s.add(AppSetting(key="autopost", value={"paused": paused}))
    s.commit()
    logbus.log("warning" if paused else "info", "autopost_paused" if paused else "autopost_resumed",
               "Auto-posting PAUSED — nothing new will be submitted" if paused else "Auto-posting resumed")


def resolve_pacing(
    pipeline: str | None = None,
    accounts: list[str] | str | None = None,
) -> dict[str, Any]:
    """Resolve effective pacing rules for a specific pipeline or target accounts.
    Priority:
      1. Account override (e.g. "default", "mk", "default:*")
      2. Pipeline override (e.g. "Movie Clips", "LongForm", "@VynixAE")
      3. Global schedule setting
    """
    cfg = _schedule_cfg
    tz = cfg["timezone"]
    start = cfg["start_hour"]
    end = cfg["end_hour"]
    step = cfg["interval_hours"]
    ppd = cfg.get("posts_per_day")

    override = None
    # 1. Check account override
    if accounts and cfg.get("accounts"):
        acc_list = [accounts] if isinstance(accounts, str) else accounts
        for acc in acc_list:
            if not acc:
                continue
            clean_profile = acc.split(":")[0].strip()
            if acc in cfg["accounts"]:
                override = cfg["accounts"][acc]
                break
            elif clean_profile in cfg["accounts"]:
                override = cfg["accounts"][clean_profile]
                break

    # 2. Check pipeline override if no account override matched
    if not override and pipeline and cfg.get("pipelines"):
        if pipeline in cfg["pipelines"]:
            override = cfg["pipelines"][pipeline]

    if override:
        if override.get("posts_per_day") is not None:
            ppd = int(override["posts_per_day"])
        if override.get("start_hour") is not None:
            start = int(override["start_hour"])
        if override.get("end_hour") is not None:
            end = int(override["end_hour"])
        if override.get("interval_hours") is not None:
            step = int(override["interval_hours"])

    return {
        "timezone": tz,
        "start_hour": start,
        "end_hour": end,
        "interval_hours": step,
        "posts_per_day": ppd,
    }


def day_slots_for_pacing(
    day: datetime.date,
    tz: ZoneInfo,
    start_hour: int,
    end_hour: int,
    interval_hours: int,
    posts_per_day: int | None,
) -> list[datetime.datetime]:
    """Calculate the list of slot datetimes for a single day based on pacing rules."""
    if posts_per_day is not None and posts_per_day > 0:
        if posts_per_day == 1:
            return [datetime.datetime.combine(day, datetime.time(start_hour), tzinfo=tz)]
        window_minutes = (end_hour - start_hour) * 60 if end_hour > start_hour else 1440
        slots = []
        for i in range(posts_per_day):
            mins = round(i * window_minutes / (posts_per_day - 1))
            total_mins = start_hour * 60 + mins
            h = min(23, total_mins // 60)
            m = total_mins % 60
            slots.append(datetime.datetime.combine(day, datetime.time(h, m), tzinfo=tz))
        return slots
    return [
        datetime.datetime.combine(day, datetime.time(h), tzinfo=tz)
        for h in range(start_hour, end_hour + 1, interval_hours)
    ]


def slots_per_day(pipeline: str | None = None, accounts: list[str] | str | None = None) -> int:
    p = resolve_pacing(pipeline=pipeline, accounts=accounts)
    if p["posts_per_day"] is not None:
        return p["posts_per_day"]
    return len(range(p["start_hour"], p["end_hour"] + 1, p["interval_hours"]))


def slots_after(
    t: datetime.datetime,
    n: int,
    pipeline: str | None = None,
    accounts: list[str] | str | None = None,
) -> list[datetime.datetime]:
    """The next n posting slots strictly after t, as UTC datetimes,
    using effective pacing for the specified pipeline/account."""
    pacing = resolve_pacing(pipeline=pipeline, accounts=accounts)
    tz = ZoneInfo(pacing["timezone"])
    local = t.astimezone(tz)
    day = local.date()
    out: list[datetime.datetime] = []
    while len(out) < n:
        for slot in day_slots_for_pacing(
            day, tz,
            pacing["start_hour"], pacing["end_hour"],
            pacing["interval_hours"], pacing["posts_per_day"],
        ):
            if slot > local:
                out.append(slot.astimezone(UTC))
                if len(out) == n:
                    break
        day += datetime.timedelta(days=1)
    return out


def current_slot(
    now: datetime.datetime,
    pipeline: str | None = None,
    accounts: list[str] | str | None = None,
) -> datetime.datetime | None:
    """The slot that started within DUE_GRACE before now, if any."""
    s = slots_after(now - DUE_GRACE, 1, pipeline=pipeline, accounts=accounts)
    if not s:
        return None
    slot = s[0]
    return slot if slot <= now else None


def posted_today(s: Session, group: str, now: datetime.datetime) -> int:
    """Scheduled posts of this pipeline already sent (or attempted) today, in
    the posting schedule's timezone. Counts by slot time; Post now (no slot)
    isn't counted — that's a deliberate manual action."""
    tz = ZoneInfo(_schedule_cfg["timezone"])
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    end = (local.replace(hour=0, minute=0, second=0, microsecond=0) + datetime.timedelta(days=1, hours=2)) \
        .replace(hour=0).astimezone(UTC)
    rows = (s.query(QueueItem)
            .filter(QueueItem.scheduled_at >= start, QueueItem.scheduled_at < end)
            .filter(QueueItem.status.in_(["posting", "posted", "retry", "error"]))
            .all())
    return sum(1 for it in rows if pipeline_group(it) == group)


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
    due_groups: set[str] = set()
    changed = 0
    for it in ready:
        if not it.accounts:
            if it.scheduled_at is not None:
                it.scheduled_at = None
                changed += 1
            continue
        if _is_due(it, now):
            due_groups.add(pipeline_group(it))
        else:
            groups.setdefault(pipeline_group(it), []).append(it)

    def slot_used(group: str, cur: datetime.datetime) -> bool:
        """Did this pipeline already post (or try to) in ITS OWN current slot?
        Bug fixed 2026-10-04: this used the GLOBAL slot time, so a pipeline with
        its own pacing (Movie Clips 3/day 10–22 → 16:00) never saw its slot as
        used and every 5-min tick handed the same slot to the next video —
        8 posts in one 16:00 slot, YouTube's daily upload limit hit at 22:00."""
        return any(pipeline_group(it) == group for it in (
            s.query(QueueItem)
            .filter(QueueItem.scheduled_at == cur)
            .filter(QueueItem.status.in_(["posting", "posted", "retry", "error", "archived"]))
            .all()))

    for group, items in groups.items():
        primary_accs = items[0].accounts if items else None
        cur = current_slot(now, pipeline=group, accounts=primary_accs)
        slots = slots_after(now, len(items), pipeline=group, accounts=primary_accs)
        if cur is not None and group not in due_groups and not slot_used(group, cur):
            slots = [cur] + slots[:-1] if slots else [cur]
        for it, slot in zip(items, slots):
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
    thumbnail: pathlib.Path | None = None,
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
                scheduled_date=scheduled_date, thumbnail=thumbnail,
            )
            entries.append({"profile": profile, "platforms": platforms, "request_id": rid,
                            "submitted_at": scheduled_date or now})  # timeout counts from go-live
        except Exception as exc:
            entries.append({"profile": profile, "platforms": platforms, "error": str(exc)[:300]})
    return entries


def auto_seo_enabled(s: Session) -> bool:
    row = s.get(AppSetting, "seo")
    return (row.value or {}).get("auto", True) if row else True


def needs_auto_seo(item: QueueItem) -> bool:
    """A clip that never had the AI/TMDB pass and still carries its raw import
    text (Drive sync sets description == title). Owner-edited text is left alone."""
    if item.compilation_id or item.pipeline == "LongForm" or item.research is not None:
        return False
    return not item.description or item.description.strip() == (item.title or "").strip()


async def auto_seo(item: QueueItem, s: Session) -> bool:
    """Research + rewrite before posting. Never blocks the post: on any failure
    the clip goes out with its existing text and the reason is logged."""
    from .. import costs, undo
    from . import ai_bulk
    try:
        with costs.operation("post:auto_seo", ref=f"queue:{item.id}"):
            res, research = await ai_bulk.ai_for(item, s)
    except Exception as exc:  # noqa: BLE001 — any failure: post with existing text
        logbus.log("warning", "auto_seo_skipped", f"Item #{item.id}: posting without SEO rewrite — {str(exc)[:200]}")
        return False
    undo.record(s, "queue", f"Auto-SEO of #{item.id} before posting", rows=[item])
    ai_bulk.apply_ai(item, res, research)
    s.commit()
    logbus.log("info", "auto_seo", f"Item #{item.id}: SEO title/caption/hashtags written"
               + (f" (TMDB: {research.get('title')})" if research and research.get("matched") else ""))
    return True


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

    if needs_auto_seo(item) and auto_seo_enabled(s):
        await auto_seo(item, s)  # every clip goes out SEO-optimised

    item.status = "posting"
    s.commit()
    logbus.log("info", "queue_posting", f"Posting item #{item.id} ('{item.title[:40]}')")
    from .. import costs
    _op = costs.operation("post", ref=f"queue:{item.id}")
    _op.__enter__()

    try:
        description = "\n\n".join(p.strip() for p in (item.description, item.tags) if p and p.strip())
        thumb = pathlib.Path(item.thumb_path) if item.thumb_path and not item.thumb_path.startswith("http") else None
        entries = await submit_upload(
            video_path, list(item.accounts), title=item.title or item.video_name,
            description=description, tags=_hashtags(item.tags), thumbnail=thumb,
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
        _op.__exit__(None, None, None)
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
        # LongForm breakdowns stay in Drive permanently ("LongForm Studio" folder).
        if item.drive_link and item.pipeline != "LongForm":
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
        if item.pipeline == "LongForm":   # the studio archive rule needs to know when it posted
            from ..studio import archive as studio_archive
            studio_archive.note_posted(s, item)
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
        load_schedule(s)  # pick up changes made on the Settings page
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
        if autopost_paused(s):
            due = []  # paused: plan and check results, but submit nothing new
        seen: set[str] = set()
        for item in due:
            group = pipeline_group(item)
            if group in seen:
                continue
            seen.add(group)
            # Hard stop, whatever the planner did: never more scheduled posts in
            # one day than this pipeline's posts/day setting.
            limit = slots_per_day(pipeline=group, accounts=item.accounts)
            done = posted_today(s, group, now)
            if done >= limit:
                if scheduler_status.get("limit_logged") != (group, now.date().isoformat()):
                    scheduler_status["limit_logged"] = (group, now.date().isoformat())
                    logbus.log("warning", "queue_daily_limit",
                               f"{group}: today's {limit} post(s) already went out — #{item.id} waits for tomorrow's slot")
                continue
            try:
                asyncio.run(publish_queue_item(item.id, s))
                scheduler_status["last_submitted"] = {"id": item.id, "at": now.isoformat()}
            except Exception as exc:
                logger.error("Error executing scheduled queue item #%s: %s", item.id, exc)

        if _last_archive_sweep is None or now - _last_archive_sweep >= ARCHIVE_SWEEP_EVERY:
            _last_archive_sweep = now
            sweep_archive(s, now)

        # Trigger 3:00 AM nightly database snapshot (safe lock-free SQLite online backup)
        try:
            backup.maybe_run_nightly_backup()
            from ..studio import archive as studio_archive
            studio_archive.maybe_sweep()      # once a day: archive breakdowns 14 days after posting
        except Exception as exc:
            logger.error("Error checking nightly backup in scheduler: %s", exc)

        # Jobs the daily spend cap paused go first once there's budget again.
        try:
            from .. import resume
            resume.resume_budget_paused()
        except Exception as exc:
            logger.error("Error resuming budget-paused jobs: %s", exc)

        # Archived breakdowns: deleted 5 days after the owner archived them.
        try:
            from ..studio import routes as studio_routes
            studio_routes.sweep_archived()
        except Exception as exc:
            logger.error("Error deleting archived breakdowns: %s", exc)

        # Studio's waiting line (manual starts that arrived while it was busy).
        try:
            from ..studio import runner as studio_runner
            studio_runner.start_next_waiting()
        except Exception as exc:
            logger.error("Error starting a waiting Studio job: %s", exc)

        # Breakdown automation (after paused and waiting jobs, which go first).
        try:
            from ..studio import auto as studio_auto
            studio_auto.tick()
        except Exception as exc:
            logger.error("Error in breakdown automation: %s", exc)


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
            _wake.wait(TICK_SECONDS)
            _wake.clear()

    t = threading.Thread(target=loop, daemon=True, name="posting_queue_scheduler")
    t.start()
