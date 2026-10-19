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


def clean_times(raw: Any, where: str = "") -> list[str]:
    """Exact posting times ["HH:MM", ...] → sorted, unique, validated (max 48)."""
    if isinstance(raw, str):
        raw = [t for t in raw.replace(";", ",").split(",")]
    out = set()
    for t in raw or []:
        t = str(t).strip()
        if not t:
            continue
        try:
            h, m = (int(x) for x in t.split(":"))
        except ValueError:
            raise ValueError(f"{where + ': ' if where else ''}'{t}' isn't a time like 10:00 or 18:30")
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"{where + ': ' if where else ''}'{t}' isn't a valid time")
        out.add(f"{h:02d}:{m:02d}")
    if len(out) > 48:
        raise ValueError(f"{where + ': ' if where else ''}at most 48 posting times a day")
    return sorted(out)


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
            if v.get("times"):
                entry["times"] = clean_times(v["times"], k)
            if isinstance(v.get("platforms"), dict):
                # Owner (2026-10-13): different daily numbers per platform, e.g. YouTube 1, Facebook 3, Instagram 3.
                # A number = that many of the queue's posts a day (the first ones); a list of times
                # = exactly those times (owner, 2026-10-13: keep YouTube's times, add Facebook/Instagram).
                plats: dict[str, Any] = {}
                for net, n in v["platforms"].items():
                    if n is None or n == "" or n == []:
                        continue
                    net = str(net).strip().lower()
                    if not net.isalnum():
                        raise ValueError(f"Override for {k}: unknown platform '{net}'")
                    if isinstance(n, (list, str)) and ":" in (",".join(n) if isinstance(n, list) else n):
                        plats[net] = clean_times(n, f"{k} {net}")
                        continue
                    n = int(n)
                    if not (0 <= n <= 48):
                        raise ValueError(f"Override for {k}: {net} posts/day must be 0-48")
                    plats[net] = n
                if plats:
                    entry["platforms"] = plats
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
        plats = override.get("platforms") or {}
        nums = [x for x in plats.values() if isinstance(x, int)]
        if nums and not override.get("times") and not any(isinstance(x, list) for x in plats.values()):
            ppd = max(nums) or ppd                                # the queue runs at its busiest platform's rate
        if override.get("start_hour") is not None:
            start = int(override["start_hour"])
        if override.get("end_hour") is not None:
            end = int(override["end_hour"])
        if override.get("interval_hours") is not None:
            step = int(override["interval_hours"])
    times = list((override or {}).get("times") or [])
    plat_times = {t for x in ((override or {}).get("platforms") or {}).values() if isinstance(x, list) for t in x}
    if plat_times:                      # each platform's own times: the queue posts at all of them
        times = sorted(set(times) | plat_times)

    return {
        "timezone": tz,
        "start_hour": start,
        "end_hour": end,
        "interval_hours": step,
        "posts_per_day": len(times) if times else ppd,
        "times": times,
    }


def day_slots_for_pacing(
    day: datetime.date,
    tz: ZoneInfo,
    start_hour: int,
    end_hour: int,
    interval_hours: int,
    posts_per_day: int | None,
    times: list[str] | None = None,
) -> list[datetime.datetime]:
    """Calculate the list of slot datetimes for a single day based on pacing rules.
    Exact times (owner-set, e.g. 10:00 and 18:30) win over posts/day spreading."""
    if times:
        out = []
        for t in times:
            h, m = (int(x) for x in t.split(":"))
            out.append(datetime.datetime.combine(day, datetime.time(h, m), tzinfo=tz))
        return out
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
            pacing["interval_hours"], pacing["posts_per_day"], pacing.get("times"),
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
            .filter(QueueItem.status.in_(["posting", "posted", "archived", "retry", "error"]))
            .filter(QueueItem.pinned_at.is_(None))      # pinned posts are extra, by the owner's choice
            .all())
    used = {(it.id, _slot_iso(it.scheduled_at)) for it in rows if pipeline_group(it) == group}
    # A catch-up video already used a slot today even though it's back in Ready with no slot.
    for it in s.query(QueueItem).filter(QueueItem.catchup.isnot(None)).all():
        if pipeline_group(it) != group:
            continue
        for iso in (it.catchup or {}).get("slots") or []:
            if start <= datetime.datetime.fromisoformat(iso) < end:
                used.add((it.id, iso))
    return len(used)


def _slot_iso(dt: datetime.datetime | None) -> str:
    return _aware(dt).astimezone(UTC).isoformat() if dt else ""


def timed_platforms(group: str) -> dict[str, list[str]]:
    """Platforms of this queue that post at their own times (e.g. YouTube 09:00, 17:00)."""
    return {n: lim for n, lim in platform_limits(group).items() if isinstance(lim, list)}


def fits_slot(item: QueueItem, group: str, slot: datetime.datetime) -> bool:
    """Does this slot belong to at least one platform the video still has to go to?
    (A video that already went to Facebook/Instagram only fits a YouTube time.)"""
    timed = timed_platforms(group)
    if not timed:
        return True
    hhmm = slot.astimezone(ZoneInfo(_schedule_cfg["timezone"])).strftime("%H:%M")
    for acc in item.accounts or []:
        net = str(acc).partition(":")[2]
        if net == "*" or net not in timed or hhmm in timed[net]:
            return True
    return False


def platform_limits(group: str) -> dict[str, Any]:
    """Daily posts per platform for this queue ({} = every post goes everywhere)."""
    return dict(((_schedule_cfg.get("pipelines") or {}).get(group) or {}).get("platforms") or {})


def platform_posts_today(s: Session, group: str, now: datetime.datetime) -> dict[str, int]:
    """Regular (scheduled, not pinned) posts each platform already got today from this queue."""
    tz = ZoneInfo(_schedule_cfg["timezone"])
    local = now.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    end = start + datetime.timedelta(days=1)
    counts: dict[str, int] = {}
    rows = (s.query(QueueItem)
            .filter(QueueItem.scheduled_at >= start, QueueItem.scheduled_at < end)
            .filter(QueueItem.status.in_(["posting", "posted", "archived", "retry", "error"]))
            .filter(QueueItem.pinned_at.is_(None)).all())
    for it in rows:
        if pipeline_group(it) != group:
            continue
        for e in it.publish_requests or []:
            if e.get("error") or e.get("skipped"):
                continue
            for net in e.get("platforms") or []:
                counts[net] = counts.get(net, 0) + 1
    return counts


def _is_due(item: QueueItem, now: datetime.datetime) -> bool:
    at = _aware(item.scheduled_at)
    return at is not None and now - DUE_GRACE < at <= now


def has_video_source(item: QueueItem) -> bool:
    """Is there anything to upload? (Audit 2026-10-11: Countdown rows were Ready
    with no video, took Screen Central's slots and would have failed at post time.)"""
    return bool(item.video_path or item.media_files or item.compilation_id or item.clip_id or item.drive_link or item.media_url)


def plan_schedule(s: Session, now: datetime.datetime) -> int:
    """Give every Ready item a slot: per pipeline, in posting order, one item
    per slot. Items already due keep their slot; items with no accounts picked
    or no video yet get no slot (nothing is ever posted to accounts the owner
    didn't choose, and a missing video never burns a slot). Returns rows changed."""
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
        if not it.accounts or not has_video_source(it):
            if it.scheduled_at is not None:
                it.scheduled_at = None
                changed += 1
            continue
        if it.pinned_at is not None:              # owner-pinned: its own time, never re-planned
            if _aware(it.scheduled_at) != _aware(it.pinned_at):
                it.scheduled_at = it.pinned_at
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
        if any(pipeline_group(it) == group and _slot_iso(cur) in ((it.catchup or {}).get("slots") or [])
               for it in ready):
            return True                           # a catch-up video posted in this slot, then went back to Ready
        return any(pipeline_group(it) == group for it in (
            s.query(QueueItem)
            .filter(QueueItem.scheduled_at == cur)
            .filter(QueueItem.status.in_(["posting", "posted", "retry", "error", "archived"]))
            .all()))

    from ..studio import momentum

    def _momentum_order_key(it: QueueItem) -> tuple:
        pos = it.position if it.position is not None else it.id
        if it.post_next_at is not None:
            # ⏫ Post next always goes first, newest choice first.
            return (-1, -_aware(it.post_next_at).timestamp(), pos)
        rel = momentum.extract_release_date(it, session=s)
        if not rel:
            return (2, pos)
        m = momentum.compute_momentum(rel, today=now.date())
        days = m["days_until_release"]
        if days is not None and days >= 0:
            # Pre-release momentum: sooner releases get earlier slots to post before release!
            return (0, days, pos)
        elif days is not None and days < 0:
            # Past release: momentum window closed, placed behind upcoming pre-release titles
            return (3, abs(days), pos)
        return (2, pos)

    for group, items in groups.items():
        if group == "LongForm" or any(bool(momentum.extract_release_date(it, session=s)) for it in items):
            items.sort(key=_momentum_order_key)

        primary_accs = items[0].accounts if items else None
        cur = current_slot(now, pipeline=group, accounts=primary_accs)
        if timed_platforms(group):
            # Catch-up (owner, 2026-10-14): each slot takes the first video (posting order)
            # that still owes one of that slot's platforms. Enough slots that even a run of
            # videos owing only one platform all get one of its times.
            want = len(items) * max(1, slots_per_day(pipeline=group, accounts=primary_accs))
            slots = slots_after(now, want, pipeline=group, accounts=primary_accs)
            if cur is not None and group not in due_groups and not slot_used(group, cur):
                slots = [cur] + slots
            left = list(items)
            plan: dict[int, datetime.datetime] = {}
            for slot in slots:
                if not left:
                    break
                for it in left:
                    if fits_slot(it, group, slot):
                        plan[it.id] = slot
                        left.remove(it)
                        break
            for it in items:
                slot = plan.get(it.id)
                if _aware(it.scheduled_at) != slot:
                    it.scheduled_at = slot
                    changed += 1
            continue
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
    allow: dict[str, bool] | None = None,
    on_intent=None,
    on_result=None,
    media_files: list[pathlib.Path] | None = None,
) -> list[dict[str, Any]]:
    """Send the video to Upload-Post: one async upload per profile.
    `allow` (network -> False) leaves out platforms that reached their daily number.

    Returns one entry per profile — {"profile", "request_id", "submitted_at"} or
    {"profile", "error"} — so a failed profile is retried later on its own.
    Raises if the picked accounts aren't valid/connected (nothing is sent)."""
    if not accounts:
        raise upload_post.UploadPostError("No accounts picked for this video", status_code=400)
    # Whole-profile picks ("default:*") expand to that profile's channels now.
    accounts = upload_post.expand_targets(accounts, await upload_post.list_accounts())

    now = datetime.datetime.now(UTC).isoformat()
    entries: list[dict[str, Any]] = []
    if allow:
        skipped = [a for a in accounts if allow.get(upload_post.split_account(a)[1]) is False]
        accounts = [a for a in accounts if a not in skipped]
        if skipped:
            entries.append({"skipped": skipped, "reason": "daily number for that platform reached", "at": now})
        if not accounts:
            return entries
    for account in dict.fromkeys(accounts):
        profile, network = upload_post.split_account(account)
        platforms = [network]
        import uuid
        request_key = uuid.uuid4().hex
        intent = {"profile": profile, "platforms": platforms, "state": "intent", "submitted_at": scheduled_date or now,
                  "client_request_id": request_key}
        if on_intent:
            on_intent(intent)
        try:
            if media_files:
                rid = await upload_post.upload_images(media_files, profile=profile, platforms=platforms,
                    title=title, description=description, idempotency_key=request_key)
            else:
                rid = await upload_post.upload_video(
                    video_path, profile=profile, platforms=platforms, title=title,
                    description=description, tags=tags, privacy=settings.publish_privacy,
                    scheduled_date=scheduled_date, thumbnail=thumbnail, idempotency_key=request_key,
                )
            result = {**intent, "state": "accepted", "request_id": rid}
        except Exception as exc:
            # Even a lost response can mean accepted. Never turn uncertainty
            # into a retry without provider reconciliation or owner resolution.
            result = {**intent, "state": "unknown", "error": str(exc)[:300]}
        entries.append(result)
        if on_result:
            on_result(result)
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


# Upload-Post profiles the Posting Queue and LongForm may NEVER post to
# (owner rule 2026-10-04: profile "mk" = Flamingo Remix is not for these videos).
BLOCKED_PROFILES = {"mk"}


def blocked_accounts(accounts: list[str] | None) -> list[str]:
    """The destinations in this list that belong to a blocked profile."""
    return [a for a in (accounts or []) if str(a).split(":", 1)[0].strip() in BLOCKED_PROFILES]


# Default "Posts To" (owner, 2026-10-14): every connected Upload-Post profile except blocked ones
# (today "Cinama:*" — YouTube, Facebook, Instagram). New rows get it, and the scheduler fills any row
# left with no destination or only disconnected profiles (the old "default" profile is gone).
DEST_TTL = 600
_dest_cache: dict[str, Any] = {"at": 0.0, "profiles": None}


def connected_profiles() -> list[str] | None:
    """Connected, non-blocked profiles (cached 10 min). None if Upload-Post can't be reached."""
    now = time.monotonic()
    if _dest_cache["profiles"] is not None and now - _dest_cache["at"] < DEST_TTL:
        return _dest_cache["profiles"]
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as ex:      # works inside or outside an event loop
            accs = ex.submit(asyncio.run, upload_post.list_accounts()).result(timeout=30)
    except Exception as exc:  # noqa: BLE001 — keep the last known list
        logger.warning("Upload-Post accounts unavailable: %s", exc)
        return _dest_cache["profiles"]
    profiles = sorted({a["profile"] for a in accs if a.get("profile") and a["profile"] not in BLOCKED_PROFILES})
    _dest_cache.update(at=now, profiles=profiles)
    return profiles


def default_destination(fallback: list[str] | None = None) -> list[str]:
    profiles = connected_profiles()
    return [f"{p}:*" for p in profiles] if profiles else list(fallback or [])


def fill_destinations(s: Session) -> int:
    """Rows waiting to post with no destination (or only disconnected profiles) get the default."""
    profiles = connected_profiles()
    if not profiles:
        return 0
    dest = [f"{p}:*" for p in profiles]
    changed = 0
    for it in s.query(QueueItem).filter(QueueItem.status.in_(["review", "ready", "retry"])).all():
        accs = list(it.accounts or [])
        live = [a for a in accs if str(a).partition(":")[0] in profiles]
        if accs and live == accs:
            continue
        it.accounts = live or dest
        changed += 1
    if changed:
        s.commit()
        logbus.log("info", "queue_destination_filled",
                   f"{changed} queued video(s) set to post to {', '.join(dest)} (no destination or a disconnected profile)")
    return changed


def archive_posted_rows(s: Session) -> int:
    """Startup: rows confirmed posted before 2026-10-04 move to the Posted Archive
    too (posting now archives directly). published_at — the clean-up clock — stays."""
    rows = s.query(QueueItem).filter(QueueItem.status == "posted").all()
    for it in rows:
        it.status = "archived"
    if rows:
        s.commit()
        logbus.log("info", "queue_posted_archived", f"Moved {len(rows)} posted item(s) to the Posted Archive")
    return len(rows)


def strip_blocked_accounts(s: Session) -> int:
    """Remove blocked-profile destinations from every queue row (startup clean-up).
    A row left with no destination simply stays unscheduled until one is picked."""
    changed = 0
    for it in s.query(QueueItem).filter(QueueItem.status.notin_(["posted", "archived"])).all():
        bad = blocked_accounts(it.accounts)
        if bad:
            it.accounts = [a for a in it.accounts if a not in bad]
            changed += 1
    if changed:
        s.commit()
        logbus.log("warning", "queue_blocked_profile_removed",
                   f"Removed profile {', '.join(sorted(BLOCKED_PROFILES))} from {changed} queue item(s) — never used for the queue")
    return changed


async def publish_queue_item(item_id: int, s: Session, regular: bool = False) -> QueueItem:
    from ..worker_leases import scope,LeaseLost
    try:
        with scope(f'publication:{item_id}'):
            try:
                return await _publish_queue_item_owned(item_id,s,regular)
            except BaseException:
                s.rollback()  # Release failed writer transactions before lease cleanup.
                raise
    except LeaseLost as exc:
        raise upload_post.UploadPostError(f"Submission unresolved or already in progress: {exc}",status_code=409) from exc


def require_native_queue(s,item):
    native_id = item.research["native_export_id"]
    from ..editorial.campaigns import exact_export,require_native_acceptance
    native, fingerprint = exact_export(s, native_id)
    require_native_acceptance(native,fingerprint)
    if native.format in ("community","poll"):raise upload_post.UploadPostError("Manual native exports cannot publish",status_code=409)
    if item.research.get("approval_fingerprint") != fingerprint:
        raise upload_post.UploadPostError("Native export approval changed; inspect it before posting", status_code=409)
    from ..models import CreativeCampaign
    campaign = s.get(CreativeCampaign, native.campaign_id)
    expected_caption = campaign.brief[{"carousel":"carousel_caption","image":"image_caption","reel":"reel_caption"}.get(native.format,"short_caption")]
    if (item.title != campaign.brief["title"] or item.description != expected_caption or item.tags
            or item.media_kind != ("images" if native.format in ("carousel","image") else "video")
            or item.thumb_path != native.manifest["cover"]["file"]
            or item.media_files != ([c["file"] for c in native.manifest["cards"]] if native.format in ("carousel","image") else [])
            or item.video_path != native.manifest.get("video", {}).get("file")):
        raise upload_post.UploadPostError("Native queue media/copy differs from its approved export; create and inspect a revised treatment", status_code=409)


async def _publish_queue_item_owned(item_id: int, s: Session, regular: bool = False) -> QueueItem:
    """Submit a queue item to Upload-Post for the accounts the owner picked."""
    item = s.get(QueueItem, item_id)
    if not item:
        raise ValueError(f"Queue item {item_id} not found")
    from .. import publication_journal as journal
    if journal.unresolved_late_receipt(s,item):
        raise upload_post.UploadPostError("A late provider receipt needs explicit review before another submission",status_code=409)
    if any(e.get("state") in ("intent", "unknown", "accepted") for e in item.publish_requests or []):
        raise upload_post.UploadPostError("An unresolved submission exists — reconcile before another attempt", status_code=409)
    if not item.accounts:
        raise upload_post.UploadPostError(
            "No accounts picked. Choose where this video posts (Posts To column, or 🔗 Set Target Accounts).", status_code=400)
    native_id = (item.research or {}).get("native_export_id")
    if native_id:require_native_queue(s,item)
    images = [pathlib.Path(p) for p in (item.media_files or [])] if item.media_kind == "images" else []
    if item.media_kind == "images":
        if not native_id or not 1 <= len(images) <= 10 or any(not p.is_file() for p in images):
            raise upload_post.UploadPostError("Inspect a native image export before submitting photos", status_code=409)
        if any(a.split(":")[-1] not in ("facebook", "instagram") for a in item.accounts):
            raise upload_post.UploadPostError("Choose explicit Instagram/Facebook photo destinations", status_code=400)
    if blocked_accounts(item.accounts):
        # Last line of defence: never send to a blocked profile, even if a row slipped through.
        item.accounts = [a for a in item.accounts if a not in blocked_accounts(item.accounts)]
        s.commit()
        if not item.accounts:
            raise upload_post.UploadPostError(
                f"Profile {', '.join(sorted(BLOCKED_PROFILES))} is never used for queue videos — pick another destination.",
                status_code=400)

    # Locate video file
    video_path: pathlib.Path | None = None
    is_temp_download = False

    if images:
        video_path = images[0]  # local transport handle; images go to /upload_photos
    elif item.video_path and pathlib.Path(item.video_path).exists():
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

    from ..file_use import pin
    paths=[video_path,*images]
    if item.thumb_path and not item.thumb_path.startswith('http'):paths.append(item.thumb_path)
    if native_id:
        from ..models import CreativeExport
        native=s.get(CreativeExport,native_id)
        paths.append(settings.data_path/'editorial'/'campaigns'/str(native.campaign_id)/native.id)
    try:
        with pin(paths,session=s):
            return await _submit_owned_media(item,s,video_path,images,regular)
    finally:
        if is_temp_download and video_path:
            video_path.unlink(missing_ok=True)


async def _submit_owned_media(item,s,video_path,images,regular):
    if (item.research or {}).get("native_export_id"):
        s.refresh(item)
        s.expire_all()
        require_native_queue(s,item)  # Recheck exact files/acceptance under the media claim.
        current_images=[pathlib.Path(p) for p in (item.media_files or [])] if item.media_kind=='images' else []
        current_video=current_images[0] if current_images else pathlib.Path(item.video_path)
        if current_images!=images or current_video!=video_path:
            raise upload_post.UploadPostError("Native transport changed while acquiring media; review it again",status_code=409)
    item_id=item.id
    from .. import publication_journal as journal
    if needs_auto_seo(item) and auto_seo_enabled(s):
        await auto_seo(item, s)  # every clip goes out SEO-optimised

    import hashlib
    import json
    from ..studio.media_quality import file_hash
    artifact_key = hashlib.sha256(json.dumps({"video": file_hash(video_path), "images": [file_hash(p) for p in images], "title": item.title,
        "description": item.description, "tags": item.tags, "accounts": sorted(item.accounts),
        "thumbnail": file_hash(pathlib.Path(item.thumb_path)) if item.thumb_path and pathlib.Path(item.thumb_path).is_file() else item.thumb_path},
        sort_keys=True).encode()).hexdigest()
    import uuid
    batch_key=uuid.uuid4().hex
    publication_identity=journal.identity(item)
    intent = [{"state": "intent", "client_request_id":batch_key, "accounts": list(item.accounts), "artifact_key": artifact_key,
               "submitted_at": datetime.datetime.now(UTC).isoformat()}]
    from sqlalchemy import String, cast, update
    # Claim and intent are ONE transaction: no posting-without-intent crash gap.
    # The version comparison also refuses metadata edited after it was hashed.
    claimed = s.execute(update(QueueItem).where(QueueItem.id == item_id,
                        QueueItem.status.in_(("ready", "retry", "review", "error")),
                        QueueItem.title == item.title, QueueItem.description == item.description,
                        QueueItem.tags == item.tags, cast(QueueItem.accounts, String) == json.dumps(item.accounts),
                        QueueItem.video_path == item.video_path, QueueItem.thumb_path == item.thumb_path,
                        QueueItem.media_kind == item.media_kind, cast(QueueItem.media_files, String) == json.dumps(item.media_files),
                        QueueItem.drive_link == item.drive_link)
                        .values(status="posting", publish_requests=intent),
                        execution_options={"synchronize_session": False}).rowcount
    from .. import publication_journal as journal
    if claimed: journal.record(s,item,intent[0],'batch_intent')
    s.commit()
    s.refresh(item)
    if not claimed:
        if is_temp_download and video_path:
            video_path.unlink(missing_ok=True)
        raise upload_post.UploadPostError("Already submitted or changed; reconcile or reload before another attempt", status_code=409)
    logbus.log("info", "queue_posting", f"Posting item #{item.id} ('{item.title[:40]}')")
    from .. import costs
    _op = costs.operation("post", ref=f"queue:{item.id}")
    _op.__enter__()

    try:
        description = "\n\n".join(p.strip() for p in (item.description, item.tags) if p and p.strip())
        thumb = pathlib.Path(item.thumb_path) if item.thumb_path and not item.thumb_path.startswith("http") else None
        allow = None
        group = pipeline_group(item)
        limits = platform_limits(group) if regular else {}
        if limits:      # owner (2026-10-13): e.g. YouTube 1/day, Facebook 3, Instagram 3 — or each its own times
            counts = platform_posts_today(s, group, datetime.datetime.now(UTC))
            at = _aware(item.scheduled_at)
            hhmm = at.astimezone(ZoneInfo(_schedule_cfg["timezone"])).strftime("%H:%M") if at else None
            allow = {net: (hhmm in lim) if isinstance(lim, list) else counts.get(net, 0) < lim
                     for net, lim in limits.items()}
        def persist(entry, result=False):
            from ..budget_reservations import lock
            from .. import worker_leases as leases
            entry = {**entry, "artifact_key": artifact_key}
            if result:
                # A receipt arriving after ownership loss must survive even when
                # the old generation is forbidden from changing the queue row.
                from ..file_use import suspend
                with leases.bind(None), suspend(), SessionLocal() as receipt_session:
                    journal.append(receipt_session,item_id,entry['client_request_id'],'transport_result',entry,queue_identity=publication_identity)
                    receipt_session.commit()
            lock(s);s.refresh(item)
            leases.guard(s)
            from ..file_use import assert_current
            assert_current(s)
            if item.status!='posting': raise upload_post.UploadPostError('Submission was stopped for review',status_code=409)
            journal.record(s,item,entry)
            saved = [e for e in (item.publish_requests or []) if e.get("profile")]
            previous=next((e for e in saved if e.get('client_request_id')==entry.get('client_request_id')),None)
            if previous and previous.get('state')=='confirmed_not_accepted' and entry.get('state')=='accepted':
                entry={**entry,'state':'unknown','error':'Late provider receipt contradicts owner nonacceptance; review again'}
                journal.record(s,item,entry,'evidence_conflict')
            if result:
                saved = [e for e in saved if (e.get("profile"), e.get("platforms")) != (entry.get("profile"), entry.get("platforms"))]
            item.publish_requests = [*saved, entry]
            s.commit()
        entries = await submit_upload(
            video_path, list(item.accounts), title=item.title or item.video_name,
            description=description, tags=_hashtags(item.tags), thumbnail=thumb, allow=allow,
            on_intent=lambda entry: persist(entry), on_result=lambda entry: persist(entry, True),
            **({"media_files": images} if images else {}),
        )
        from ..budget_reservations import lock
        lock(s);s.refresh(item)
        # Persisted decisions/late-response conflicts take precedence over a stale
        # transport-local list. Do not erase reviewed evidence on return.
        saved_by_id={e.get('client_request_id'):e for e in item.publish_requests or [] if e.get('client_request_id')}
        entries = [saved_by_id.get(e.get('client_request_id'),{**e,"artifact_key":artifact_key}) for e in entries]
        journal.append(s,item.id,batch_key,'batch_returned',{'entries':entries})
        skipped = [a for e in entries for a in e.get("skipped", [])]
        if skipped and not any(e.get("request_id") or e.get("error") for e in entries):
            # Every platform already had today's number: nothing to send now.
            item.status = "ready"
            item.publish_requests = entries
            item.notes = f"Waiting: {', '.join(skipped)} already had today's posts (per-platform limits)."
            s.commit()
            logbus.log("info", "queue_platform_limits", f"Item #{item.id}: all platforms at today's number — not sent")
            return item
        if not any(e.get("request_id") or e.get("state") == "unknown" for e in entries):
            raise upload_post.UploadPostError("; ".join(e.get("error", "") for e in entries if e.get("error")))
        # Accepted is not published: stay "posting" until reconcile_posting()
        # reads every platform's real result. Only then "posted" (4-day clock).
        item.publish_requests = entries
        item.published_at = None
        if regular and item.scheduled_at is not None:      # catch-up: this slot is used even if it returns to Ready
            cu = dict(item.catchup or {})
            cu["slots"] = (list(cu.get("slots") or []) + [_slot_iso(item.scheduled_at)])[-20:]
            item.catchup = cu
        item.notes = (f"Submitted to Upload-Post for {', '.join(item.accounts)}; "
                      "waiting for the platforms to confirm."
                      + (f" Not sent to {', '.join(skipped)} — that platform's daily number was reached." if skipped else ""))
        s.commit()
        logbus.log("info", "queue_submitted", f"Item #{item.id} submitted to Upload-Post",
                   requests=[e.get("request_id") for e in entries])
        return item

    except Exception as exc:
        logger.exception("Failed to post queue item #%s: %s", item.id, exc)
        item.status = "posting"
        item.notes = f"Submission outcome unknown — do not retry. Reconcile with provider before resolving: {str(exc)[:250]}"
        s.commit()
        logbus.log("error", "queue_post_error", f"Item #{item.id} failed: {exc}")
        raise

    finally:
        _op.__exit__(None, None, None)


PENDING_TIMEOUT = datetime.timedelta(hours=3)


def resolve_results(
    entries: list[dict[str, Any]], statuses: dict[str, dict[str, Any]], now: datetime.datetime,
) -> dict[str, Any] | None:
    """Combine Upload-Post status responses into per-account outcomes. The
    accounts judged are exactly the ones submitted (whole-profile picks were
    expanded at submit time and recorded in each entry's platforms).

    Returns None while any request is still processing (and not timed out),
    else {"ok": [(acc, url)], "failed": {acc: reason}}."""
    if any(e.get("state") in ("intent", "unknown") for e in entries):
        return None
    accounts = [f"{e['profile']}:{net}" for e in entries for net in e.get("platforms", [])]
    outcome: dict[str, tuple[bool, str]] = {}
    for e in entries:
        if e.get("skipped"):            # left out on purpose (platform's daily number reached)
            continue
        if "error" in e:
            for net in e.get("platforms", []):
                outcome[f"{e['profile']}:{net}"] = (False, f"upload not accepted: {e['error']}")
            continue
        st = statuses.get(e["request_id"]) or {}
        if st.get("status") != "completed":
            submitted = _aware(datetime.datetime.fromisoformat(e["submitted_at"].replace("Z", "+00:00")))
            if now - submitted < PENDING_TIMEOUT:
                return None
            return None  # elapsed time does not prove rejection; never blindly resubmit
        expected={f"{e['profile']}:{net}" for net in e.get('platforms',[])}
        seen=set()
        for r in st.get("results") or []:
            if not isinstance(r,dict) or type(r.get('success')) is not bool: return None
            acc = f"{r.get('profile_username') or e['profile']}:{r.get('platform')}"
            if acc not in expected or acc in seen:return None
            seen.add(acc)
            if r['success']:outcome[acc]=(True,r.get('post_url') or '')
            else:outcome[acc]=(False,str(r.get('error_message') or 'Provider confirmed failure'))
        if seen!=expected:return None
    ok = [(a, outcome[a][1]) for a in accounts if a in outcome and outcome[a][0]]
    failed = {a: (outcome[a][1] if a in outcome else "no result returned") for a in accounts
              if not (a in outcome and outcome[a][0])}
    return {"ok": ok, "failed": failed}


def _fetch_statuses(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for e in entries:
        rid = e.get("request_id")
        if rid:
            out[rid] = e.get("provider_status") or asyncio.run(upload_post.get_status(rid))
    return out


def apply_result(item: QueueItem, res: dict[str, Any], now: datetime.datetime) -> None:
    item.publish_requests = [{**e, "state": "completed"} if e.get("request_id") else e
                             for e in item.publish_requests or []]
    # Platforms left out because this slot wasn't theirs still get the video (catch-up, owner 2026-10-14).
    skipped = [a for e in item.publish_requests or [] for a in e.get("skipped", [])]
    cu = dict(item.catchup or {})
    done = [list(d) for d in cu.get("done") or []]
    for a, url in res["ok"]:
        if [a, url] not in done: done.append([a, url])
    parts = [f"✓ {a} {url}".strip() for a, url in done]
    parts += [f"✕ {a}: {why[:220]}" for a, why in res["failed"].items()]
    if res["failed"]:
        # Retry only what failed (and what's still owed), so a retry never double-posts.
        item.status = "retry"
        item.accounts = list(res["failed"]) + [a for a in skipped if a not in res["failed"]]
        item.published_at = None
    elif skipped:
        # Posted where this slot allowed; back to Ready for the other platforms' next slot.
        item.status = "ready"
        item.accounts = skipped
        item.scheduled_at = None
        item.published_at = None
        parts.append(f"⏳ catch-up: waiting for the next {', '.join(sorted({a.partition(':')[2] for a in skipped}))} slot")
    else:
        # Confirmed on every platform: straight to the Posted Archive (owner, 2026-10-04).
        item.status = "archived"
        item.published_at = now
    if done or skipped:
        item.catchup = {**cu, "done": done}
    item.notes = " | ".join(parts)[:1000]


def start_catchup_backfill(s: Session, now: datetime.datetime) -> int:
    """Startup, once per row: videos archived after a per-platform skip (before catch-up existed,
    still within the archive window) go back to Ready for the platforms they missed."""
    moved = 0
    for it in s.query(QueueItem).filter(QueueItem.status == "archived").all():
        if it.catchup:
            continue
        skipped = [a for e in it.publish_requests or [] for a in e.get("skipped", [])]
        if not skipped:
            continue
        done = [ln[2:].split(" ", 1) for ln in (it.notes or "").split(" | ") if ln.startswith("✓ ")]
        it.catchup = {"slots": [_slot_iso(it.scheduled_at)] if it.scheduled_at else [],
                      "done": [[d[0], d[1] if len(d) > 1 else ""] for d in done]}
        it.status, it.accounts, it.scheduled_at, it.published_at = "ready", skipped, None, None
        it.notes = ((it.notes or "") + " | ⏳ catch-up: waiting for the next "
                    f"{', '.join(sorted({a.partition(':')[2] for a in skipped}))} slot")[:1000]
        moved += 1
    if moved:
        s.commit()
        logbus.log("info", "queue_catchup_backfill", f"{moved} posted video(s) now wait for the platforms they skipped")
    return moved


def reconcile_posting(s: Session, now: datetime.datetime) -> int:
    """Settle submitted queue items and compilation posts from Upload-Post status."""
    settled = 0
    from .. import publication_journal as journal, worker_leases as leases
    from ..budget_reservations import lock
    for item in s.query(QueueItem).filter(QueueItem.status == "posting").all():
        if not item.publish_requests or leases.live(f'publication:{item.id}') or journal.unresolved_late_receipt(s,item):
            continue  # Do not settle a partially dispatched multi-platform batch.
        qid, row_identity = item.id, journal.identity(item)
        version = journal.digest(item.publish_requests)
        entries = list(item.publish_requests)
        s.rollback()  # Provider reads must not hold a database writer lock.
        try:
            statuses = _fetch_statuses(entries)
            lock(s)
            item = s.get(QueueItem, qid, populate_existing=True)
            if (not item or item.status != 'posting' or journal.identity(item) != row_identity
                    or journal.digest(item.publish_requests) != version
                    or leases.live(f'publication:{qid}')):
                s.rollback()
                continue  # An owner decision or new dispatch superseded this read.
            saved = []
            for entry in entries:
                status = statuses.get(entry.get('request_id'))
                if status:
                    journal.record(s, item, {**entry, 'observed_status': status}, 'provider_observation')
                # Cache only complete, strictly covered results. Malformed responses
                # remain pollable rather than poisoning the receipt permanently.
                if status and status.get('status') == 'completed' and resolve_results([entry], statuses, now) is not None:
                    entry = {**entry, 'provider_status': status}
                saved.append(entry)
            item.publish_requests = saved
            res = resolve_results(saved, statuses, now)
            if res is not None:
                apply_result(item, res, now)
                if item.status == 'retry' and any(e.get('state') == 'confirmed_not_accepted' for e in saved):
                    item.status = 'review'; item.scheduled_at = None
                for entry in item.publish_requests or []: journal.record(s, item, entry, 'settled')
            s.commit()  # Observation, outcome and append-only history are atomic.
        except Exception as exc:
            s.rollback()
            logger.warning("Upload-Post status check failed for #%s: %s", qid, exc)
            continue
        if res is None:
            continue
        settled += 1
        done = item.status in ("posted", "archived")
        pending = item.status == "ready"
        logbus.log("info" if done or pending else "error",
                   "queue_posted" if done else "queue_catchup_pending" if pending else "queue_post_failed",
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
    """Reclaim queue rows only after retaining their exact delivery history.

    Keep remote copies: their older bytes may have no other retained backup.
    """
    if settings.archive_delete_days <= 0:
        return 0
    from fastapi import HTTPException
    from ..delivery_archive import retain
    cutoff = now - datetime.timedelta(days=settings.archive_delete_days)
    expired = (s.query(QueueItem).filter(QueueItem.status.in_(["posted", "archived"]))
        .filter(QueueItem.published_at.isnot(None), QueueItem.published_at < cutoff)
        .order_by(QueueItem.published_at).limit(ARCHIVE_SWEEP_BATCH).all())
    removed = 0
    for item in expired:
        qid = item.id
        try:
            archive_id = retain(s, item)
            if item.pipeline == "LongForm":
                from ..studio import archive as studio_archive
                studio_archive.note_posted(s, item)
            s.delete(item); s.commit()
        except HTTPException as exc:
            s.rollback()
            logbus.log("warning", "archive_retention_blocked", f"Item #{qid} kept: {exc.detail}")
            continue
        logbus.log("info", "archive_deleted", f"Item #{qid}: queue row removed after retaining its delivery history; remote copies kept",
                   archive_id=archive_id)
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
        try:
            fill_destinations(s)
        except Exception as exc:
            logger.error("Error filling queue destinations: %s", exc)
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
            if item.pinned_at is None and done >= limit:
                if scheduler_status.get("limit_logged") != (group, now.date().isoformat()):
                    scheduler_status["limit_logged"] = (group, now.date().isoformat())
                    logbus.log("warning", "queue_daily_limit",
                               f"{group}: today's {limit} post(s) already went out — #{item.id} waits for tomorrow's slot")
                continue
            try:
                asyncio.run(publish_queue_item(item.id, s, regular=item.pinned_at is None))
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

        # Disk guard: at 80% full, back up first, then free rebuildable space (owner, 2026-10-05).
        try:
            from .. import space
            space.maybe_free(now)
        except Exception as exc:
            logger.error("Error in disk guard: %s", exc)

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

        # Countdowns Studio: segments only when Studio is free (after LongForm).
        try:
            from ..studio import countdown as countdown_studio
            countdown_studio.tick()
        except Exception as exc:
            logger.error("Error in Countdowns Studio: %s", exc)


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
