"""DramaRama Long: long-form compilations of each DramaRama series (owner, 2026-11-26).

Part 1 = whole episodes from Ep. 1 up to ~45 min; each next part adds ~45 min on top (Part 2 = Ep. 1 through ~90 min);
the last part is the full series. Every episode we have is used, whatever its queue status. Each part is two queue
rows in the "DramaRama Long" queue: Facebook (original audio) and YouTube (each episode's dialogue-only copy — owner
rule 2026-11-06: DramaRama YouTube never gets the original audio; it waits for the copies instead). Posting pace
(1 every other day) is the queue's schedule (`every_days`); the shorts keep their own queue and rules.

The queue rows are the record: video_name "DRL|<series id>|P<n>|<net>" is unique, research.kind == "drama_long".
Only one render runs at a time, one part ahead: the next Facebook part starts when the last one has posted.
Off switch: DRAMA_LONG=off.
"""
from __future__ import annotations

import datetime
import logging
import os
import pathlib
import re
import shutil
from typing import Any

from sqlalchemy.orm import Session

from ... import control, logbus
from ...config import settings
from ...db import SessionLocal
from ...models import QueueItem
from ...social import queue_manager as qm
from .. import media
from . import stitch

logger = logging.getLogger("scrapper.drama_long")

KIND = "drama_long"
PART_SECONDS = 45 * 60
TAIL_SECONDS = 10 * 60          # a last bit shorter than this joins the part before it
MIN_FREE_BYTES = 3 * 1024 ** 3   # episodes are 5-15 MB, so a 60-episode series peaks near 2.8 GB (measured 2026-11-27)
RETRY_AFTER = datetime.timedelta(hours=6)
SCOPE = "drama_long"
DONE = ("posted", "archived")
_failed: dict[str, datetime.datetime] = {}   # job key -> when it last failed (in memory: back off, don't loop)
_said: dict[str, str] = {}


def enabled() -> bool:
    return os.environ.get("DRAMA_LONG", "on").strip().lower() != "off"


def root() -> pathlib.Path:
    return settings.data_path / "drama_long"


def series_id(source: str) -> str:
    m = re.search(r"\[(\d+)\]\s*$", source or "")
    return m.group(1) if m else re.sub(r"[^\w-]+", "_", (source or "").partition(" / ")[2]).strip("_")[:60]


def key(sid: str, part: int, net: str) -> str:
    return f"DRL|{sid}|P{part}|{net}"


def _say(tag: str, level: str, event: str, msg: str) -> None:
    if _said.get(tag) != msg:            # log a waiting reason once, not every tick
        _said[tag] = msg
        logbus.log(level, event, msg)


# --- pure planning ----------------------------------------------------------------------------------

def part_size(durations: list[float], part: int) -> int:
    """How many episodes (from Ep. 1) Part `part` holds: whole episodes up to part x 45 min, at least one more than
    the part before; a tail under 10 min joins it. Returns len(durations) for the final part."""
    n = len(durations)
    prev = 0 if part <= 1 else part_size(durations, part - 1)
    if prev >= n:
        return n
    total, k = 0.0, 0
    for d in durations:
        if total + d > part * PART_SECONDS and k > prev:
            break
        total += d
        k += 1
    if sum(durations[k:]) < TAIL_SECONDS:
        k = n
    return k


def series_name(rows: list[QueueItem]) -> str:
    """The series as viewers know it: from a reviewed title "<hook> | <Series> Ep. N", else the Drive folder."""
    for it in rows:
        m = re.search(r"\|\s*(.+?)\s+Ep\.\s*\d+\s*$", it.title or "")
        if m:
            return m.group(1).strip()
    raw = re.sub(r"\s*\[\d+\]\s*$", "", (rows[0].source or "").partition(" / ")[2])
    return re.sub(r"_s\b", "'s", raw).replace("_", ":").strip()


def episodes(s: Session, source: str) -> list[tuple[int, QueueItem]]:
    """Every episode of the series we have (any status), one row per episode number, Ep. order."""
    best: dict[int, QueueItem] = {}
    for it in s.query(QueueItem).filter(QueueItem.pipeline == qm.DRAMARAMA, QueueItem.source == source).all():
        ep = qm.drama_series_episode(it)[1]
        if ep is None or not it.drive_link:
            continue
        if ep not in best or (it.status in DONE and best[ep].status not in DONE):
            best[ep] = it
    return sorted(best.items())


def clock(sec: float) -> str:
    sec = int(sec)
    h, m, x = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{x:02d}" if h else f"{m}:{x:02d}"


def copy_for(name: str, part: int, k: int, total_eps: int, eps: list[int], durs: list[float],
             base: QueueItem | None) -> tuple[str, str, str]:
    """(title, description with chapters, tags)."""
    final = k >= total_eps
    title = (f"{name} — Full Series (All {k} Episodes)" if final
             else f"{name} — Part {part} (Episodes 1–{eps[k - 1]})")
    if len(title) > 100:
        title = title[:99].rstrip() + "…"
    lines = [f"Watch {name} from the very beginning: episodes 1–{eps[k - 1]} in one video."
             + (" The complete series." if final else " The story continues in the next part."), "", "Chapters:"]
    t = 0.0
    for ep, d in zip(eps[:k], durs[:k]):
        lines.append(f"{clock(t)} Episode {ep}")
        t += d
    tags = " ".join(w for w in (base.tags if base else "").split() if w.lower().lstrip("#") != "shorts")
    return title, "\n".join(lines), tags


# --- rendering --------------------------------------------------------------------------------------

def _cached(sid: str, net: str, ep: int, row: QueueItem) -> pathlib.Path | None:
    """This episode with uniform 48 kHz stereo audio, cached for the later parts. None = YouTube's dialogue-only
    copy isn't there yet (the caller waits)."""
    d = root() / sid / net
    out = d / f"ep{ep:04d}.mp4"
    if out.is_file():
        return out
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "dl"
    tmp.mkdir(exist_ok=True)
    if net == "youtube":
        from ... import drama_dialogue
        raw, why = drama_dialogue.fetch(row)
        if not raw:
            raise _Wait(f"Ep. {ep}: {why}")
    else:
        from ...drive_sync import download_drive_file
        raw = download_drive_file(row.drive_link, dest_dir=str(tmp))
    try:
        stitch.uniform_audio(pathlib.Path(raw), out.with_suffix(".part.mp4"))
        out.with_suffix(".part.mp4").replace(out)
    finally:
        pathlib.Path(raw).unlink(missing_ok=True)
    return out


class _Wait(Exception):
    pass


def _shape(p: pathlib.Path) -> tuple:
    import json
    probe = json.loads(media.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                  "stream=codec_name,width,height,r_frame_rate,pix_fmt", "-of", "json", str(p)]).stdout)
    v = (probe.get("streams") or [{}])[0]
    return v.get("codec_name"), v.get("width"), v.get("height"), v.get("r_frame_rate"), v.get("pix_fmt")


def join(pieces: list[pathlib.Path], out: pathlib.Path) -> float:
    """Concatenate whole episodes into one file (video copied when every piece has the same shape, otherwise
    re-encoded to the first one's size), loudness -14 LUFS. Returns its length in seconds."""
    work = out.parent / (out.stem + "_work")
    work.mkdir(parents=True, exist_ok=True)
    try:
        first = _shape(pieces[0])
        _, w, h, *_ = first
        same = []
        for i, p in enumerate(pieces):
            control.check()
            if _shape(p) == first:
                same.append(p)
                continue
            fixed = work / f"fix{i:04d}.mp4"
            media.run(["ffmpeg", "-v", "error", "-y", "-i", str(p), "-vf",
                       f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,{stitch.NORM}",
                       *stitch.ENC, "-c:a", "copy", str(fixed)])
            same.append(fixed)
        lst = work / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in same))
        raw = work / "raw.mp4"
        media.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                   "-movflags", "+faststart", str(raw)], timeout=3 * 3600)
        total = stitch.video_seconds(raw)
        part = out.with_suffix(".part.mp4")
        media.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-c:v", "copy",
                   "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11,apad,atrim=0:{total:.3f}",
                   "-c:a", "aac", "-b:a", "160k", "-ar", str(stitch.AUDIO_RATE), "-ac", "2",
                   "-t", f"{total:.3f}", "-movflags", "+faststart", str(part)], timeout=3 * 3600)
        part.replace(out)
        return total
    finally:
        shutil.rmtree(work, ignore_errors=True)


def render(source: str, part: int, net: str) -> int | None:
    """Make Part `part` of this series for one platform and add its queue row (Ready). Returns the row id."""
    with SessionLocal() as s:
        sid = series_id(source)
        k_ = key(sid, part, net)
        if s.query(QueueItem.id).filter(QueueItem.video_name == k_).first():
            return None                                       # already made
        eps = episodes(s, source)
        if not eps:
            raise RuntimeError(f"{source}: no episodes with a Drive file")
        name = series_name([r for _, r in eps])
        fb = None
        if net == "youtube":                                  # YouTube follows the Facebook part's exact episodes
            fb = s.query(QueueItem).filter(QueueItem.video_name == key(sid, part, "facebook")).first()
            if not fb:
                raise RuntimeError(f"{k_}: no Facebook part to follow")
            ids = (fb.research or {}).get("episodes") or []
            eps = [(e, r) for e, r in eps if e in ids]
        pieces, durs = [], []
        for i, (ep, row) in enumerate(eps):
            control.check()
            control.progress(f"{name[:40]} Part {part} {net}: episode {i + 1} of {len(eps)}")
            p = _cached(sid, net, ep, row)
            pieces.append(p)
            durs.append(stitch.video_seconds(p))
        k = len(eps) if fb else part_size(durs, part)
        out_dir = root() / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{sid}_P{part}_{net}.mp4"
        control.progress(f"{name[:40]} Part {part} {net}: joining {k} episodes")
        seconds = join(pieces[:k], out)
        nums = [e for e, _ in eps]
        base = next((r for _, r in eps if (r.tags or "").strip()), None)
        title, desc, tags = copy_for(name, part, k, len(nums) if not fb else (fb.research or {}).get("series_episodes", k),
                                     nums, durs, base)
        item = QueueItem(pipeline=qm.DRAMARAMA_LONG, video_name=k_, video_path=str(out), media_kind="video",
                         source=source, title=title, description=desc, tags=tags,
                         accounts=[f"{next(iter(qm.DRAMARAMA_PROFILES))}:{net}"], status="ready",
                         notes=f"DramaRama Long: {name} Part {part}, Ep. {nums[0]}–{nums[k - 1]}, {clock(seconds)}",
                         research={"kind": KIND, "series": source, "part": part, "net": net,
                                   "episodes": nums[:k], "series_episodes": len(nums) if not fb else
                                   (fb.research or {}).get("series_episodes"),
                                   "final": k >= (len(nums) if not fb else (fb.research or {}).get("series_episodes", k)),
                                   "seconds": round(seconds, 1)})
        s.add(item)
        s.commit()
        logbus.log("info", "drama_long_ready", f"#{item.id} {title} ({net}, {clock(seconds)}) is Ready")
        return item.id


# --- the planner (called every scheduler tick; cheap) -------------------------------------------------

def long_rows(s: Session) -> list[QueueItem]:
    return (s.query(QueueItem).filter(QueueItem.pipeline == qm.DRAMARAMA_LONG)
            .order_by(QueueItem.id).all())


def scheduled() -> bool:
    """Only with the owner's pace set (else the queue would post at the global slots, many a day)."""
    cfg = (qm._schedule_cfg.get("pipelines") or {}).get(qm.DRAMARAMA_LONG) or {}
    return int(cfg.get("every_days") or 1) >= 2


def next_job(s: Session, now: datetime.datetime) -> tuple[str, int, str] | None:
    """(series source, part, platform) to render next, or None."""
    rows = [r for r in long_rows(s) if (r.research or {}).get("kind") == KIND]
    have = {r.video_name for r in rows}
    # 1. A YouTube part still owed for a Facebook part already made (waits while its dialogue copies are missing).
    for r in rows:
        rs = r.research or {}
        if rs.get("net") == "facebook":
            k_ = key(series_id(rs["series"]), rs["part"], "youtube")
            if k_ not in have and not _backing_off(k_, now):
                return rs["series"], rs["part"], "youtube"
    # 2. One Facebook part ahead: the next starts once every Facebook part made so far has posted.
    fbs = [r for r in rows if (r.research or {}).get("net") == "facebook"]
    if any(r.status not in DONE for r in fbs):
        return None
    if fbs and not (fbs[-1].research or {}).get("final"):
        rs = fbs[-1].research
        return rs["series"], rs["part"] + 1, "facebook"
    done = {(r.research or {}).get("series") for r in fbs if (r.research or {}).get("final")}
    first: dict[str, int] = {}
    for sid_, src in (s.query(QueueItem.id, QueueItem.source).filter(QueueItem.pipeline == qm.DRAMARAMA,
                                                                       QueueItem.source.like("DramaRama / %"))
                      .order_by(QueueItem.id)):
        first.setdefault(src, sid_)
    for src in sorted(first, key=first.get):              # series in arrival order
        if src not in done and not _backing_off(key(series_id(src), 1, "facebook"), now):
            return src, 1, "facebook"
    return None


def _backing_off(k_: str, now: datetime.datetime) -> bool:
    at = _failed.get(k_)
    return at is not None and now - at < RETRY_AFTER


def cleanup(s: Session, now: datetime.datetime) -> None:
    """Posted parts' files go after 2 days; a finished series' episode cache goes once its final parts posted."""
    rows = long_rows(s)
    for r in rows:
        if r.status in DONE and r.video_path and r.published_at and now - qm._aware(r.published_at) > datetime.timedelta(days=2):
            pathlib.Path(r.video_path).unlink(missing_ok=True)
    finals: dict[str, set[str]] = {}
    for r in rows:
        rs = r.research or {}
        if rs.get("final") and r.status in DONE:
            finals.setdefault(rs["series"], set()).add(rs["net"])
    for src, nets in finals.items():
        if nets >= {"facebook", "youtube"}:
            shutil.rmtree(root() / series_id(src), ignore_errors=True)


def maybe_start(s: Session, now: datetime.datetime) -> str | None:
    """Start the next render in the background if one is due. Returns the job id."""
    if not enabled():
        return None
    if not scheduled():
        _say("pace", "warning", "drama_long_waits",
             "DramaRama Long: no every-other-day schedule set for the queue — nothing is made")
        return None
    if control.running(SCOPE):
        return None
    cleanup(s, now)
    job = next_job(s, now)
    if not job:
        return None
    root().mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root()).free
    if free < MIN_FREE_BYTES:
        _say("disk", "warning", "drama_long_waits", f"DramaRama Long waits: {free // 1024 ** 3} GB free on the volume")
        return None
    src, part, net = job
    k_ = key(series_id(src), part, net)

    def work():
        try:
            return render(src, part, net)
        except _Wait as exc:
            _failed[k_] = datetime.datetime.now(datetime.timezone.utc)
            _say(k_, "warning", "drama_long_waits", f"DramaRama Long {k_} waits: {exc}")
        except control.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            _failed[k_] = datetime.datetime.now(datetime.timezone.utc)
            logbus.log("error", "drama_long_failed", f"DramaRama Long {k_} failed: {str(exc)[:300]}")
            raise
    return control.start_thread("drama_long", f"DramaRama Long {k_}", SCOPE, work)
