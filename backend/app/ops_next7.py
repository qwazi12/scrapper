"""Read-only: every Ready/Review queue item's slot on each platform for the next 7 days (owner, 2026-11-07,
INSTR_SCRAPPER_20261107T1415Z). The Mac's 05:45 ET export writes SCRAPPER_NEXT7_<date>.csv from it.

Slots come from the scheduler's own plan: DramaRama from each platform's own sequence (drama_net_plans); other
queues post at the row's slot on every platform whose times include it, and a platform with other times takes its
next free time after that (projected, in posting order, one item per platform per hour). Nothing is written."""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .auth import require_token
from .db import get_session
from .models import QueueItem
from .social import queue_manager as qm
from .social import upload_post

router = APIRouter(prefix="/api/ops", dependencies=[Depends(require_token)])
UTC = datetime.timezone.utc
NETS = ("youtube", "facebook", "instagram", "x")


def owed(it: QueueItem, connected: dict[str, list[str]]) -> list[str]:
    """Platforms this row still has to go to: its accounts ("Profile:*" = the profile's connected platforms)
    minus the ones its catch-up record already delivered or holds (e.g. X waiting on a re-encode)."""
    done = {str(a) for a, *_ in ((it.catchup or {}).get("done") or [])} | set((it.catchup or {}).get("held") or {})
    out = []
    for acc in it.accounts or []:
        prof, _, net = str(acc).partition(":")
        for n in (connected.get(prof) or list(NETS)) if net == "*" else [net]:
            if f"{prof}:{n}" not in done and n not in out:
                out.append(n)
    return out


def plan(s: Session, now: datetime.datetime, connected: dict[str, list[str]], days: int = 7) -> list[dict]:
    qm.load_schedule(s)
    tz = ZoneInfo(qm._schedule_cfg["timezone"])
    end = now + datetime.timedelta(days=days)
    rows = (s.query(QueueItem).filter(QueueItem.status.in_(["ready", "review"]))
            .filter(QueueItem.scheduled_at.isnot(None)).all())
    out: list[dict] = []

    def add(it, net, slot, basis):
        if slot is not None and now - qm.DUE_GRACE < qm._aware(slot) <= end:
            out.append({"item": it, "platform": net, "slot": qm._aware(slot), "basis": basis})

    drama = [it for it in rows if it.pipeline == qm.DRAMARAMA]
    if drama:
        _, _, plans, _ = qm.drama_net_plans(s, now)
        planned = {(it.id, net) for net, p in plans.items() for it, _ in p}
        for net, p in plans.items():
            for it, slot in p:
                if it.status in ("ready", "review"):
                    add(it, net, slot, "platform sequence")
        for it in drama:                            # a pinned row or a platform without its own times
            for net in owed(it, connected):
                if (it.id, net) not in planned and net not in qm.timed_platforms(qm.DRAMARAMA):
                    add(it, net, it.scheduled_at, "row slot")

    groups: dict[str, list[QueueItem]] = {}
    for it in rows:
        if it.pipeline != qm.DRAMARAMA:
            groups.setdefault(qm.pipeline_group(it), []).append(it)
    for group, items in groups.items():
        items.sort(key=lambda it: (qm._aware(it.scheduled_at), it.id))
        timed = qm.timed_platforms(group)
        later: dict[str, list[QueueItem]] = {}
        taken: dict[str, set] = {}
        for it in items:
            at = qm._aware(it.scheduled_at)
            hhmm = at.astimezone(tz).strftime("%H:%M")
            for net in owed(it, connected):
                if net not in timed or hhmm in timed[net] or it.pinned_at is not None:
                    add(it, net, at, "row slot")
                    taken.setdefault(net, set()).add(at.replace(minute=0, second=0, microsecond=0))
                else:
                    later.setdefault(net, []).append(it)
        for net, waiting in later.items():          # catch-up: that platform's next free times, in posting order
            day = now.astimezone(tz).date()
            times = sorted(
                datetime.datetime.combine(day + datetime.timedelta(days=d), datetime.time.fromisoformat(h), tz).astimezone(UTC)
                for d in range(days + 1) for h in timed[net])
            free = [t for t in times if t > now and t not in taken.get(net, set())]
            for it in waiting:
                after = qm._aware(it.scheduled_at)
                slot = next((t for t in free if t > after), None)
                if slot is None:
                    continue
                free.remove(slot)
                add(it, net, slot, "projected catch-up")
    out.sort(key=lambda r: (r["slot"], r["item"].id, NETS.index(r["platform"]) if r["platform"] in NETS else 9))
    return out


def lane(it: QueueItem) -> str:
    g = qm.pipeline_group(it)
    return {"DramaRama": "DramaRama", "The ICK Room": "ICK", "LongForm": "ScreenCentral-LongForm",
            "Countdowns": "ScreenCentral-Countdowns", "Movie Clips": "ScreenCentral-MovieClips"}.get(g, g)


def text_for(it: QueueItem, net: str) -> tuple[str, str]:
    """(title, caption) as stored for that platform; each platform's hashtags are added at send."""
    own = (it.platform_copy or {}).get(net) or {} if net in ("youtube", "instagram", "x") else {}   # Facebook = the row
    if net == "x":
        return it.title or "", own.get("text") or it.description or ""
    if net == "instagram":
        return it.title or "", own.get("caption") or it.description or ""
    return own.get("title") or it.title or "", own.get("description") or it.description or ""


@router.get("/next7")
async def next7(s: Session = Depends(get_session)) -> dict:
    connected: dict[str, list[str]] = {}
    for a in await upload_post.list_accounts():
        prof, _, net = str(a.get("id") or "").partition(":")
        if prof and net:
            connected.setdefault(prof, []).append(net)
    now = datetime.datetime.now(UTC)
    tz = ZoneInfo(qm._schedule_cfg["timezone"])
    rows = []
    for r in plan(s, now, connected):
        it, net = r["item"], r["platform"]
        series, ep = qm.drama_series_episode(it) if it.pipeline == qm.DRAMARAMA else ("", None)
        title, caption = text_for(it, net)
        rows.append({"queue_id": it.id, "brand": lane(it), "platform": net,
                     "slot_et": r["slot"].astimezone(tz).strftime("%Y-%m-%d %H:%M"), "status": it.status.capitalize(),
                     "episode_series": f"{series} Ep. {ep}" if series and ep else series,
                     "title": title, "caption": caption, "drive_link": it.drive_link or "", "slot_basis": r["basis"]})
    return {"generated_at": now.isoformat(), "timezone": str(tz), "rows": rows}
