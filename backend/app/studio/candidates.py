"""Breakdown candidates: the top trending movies and TV shows, each checked and
ranked so the automation (and the owner) can go down one list.

Pool: TMDB trending this week, first POOL_PAGES pages per type. Each title gets
free checks (✓ / ⚠ / ✕ with the reason):
  not_made   no Studio project for this title yet
  trailer    IMDb has an official trailer (fully automatic) — ⚠ if only a
             TMDB YouTube trailer exists (needs the home Mac worker), ✕ if none
  facts      IMDb id, poster, overview and ≥3 cast
  release    movie opens within RELEASE_AHEAD days or opened within
             RELEASE_BEHIND days (US date); TV has an episode within those
             windows (next one coming, or one just aired)
  interest   TMDB popularity, trailer views or IMDb popularity rank clears a floor
Audience reaction ("sentiment") comes from IMDb (rating, votes, Metacritic,
newest user-review stars, MOVIEmeter/TVmeter rank and direction), the
trailer's YouTube like rate, and TMDB's vote average.

Score 0–100 (components kept on each row so the UI can show why):
  trailer heat 30 (views per day since upload), sentiment 20, TMDB
  popularity 15, trending position 15, trailer views 10, release timing 10,
  +3 if IMDb popularity is rising.

Never repeated: every title ever started (manual or automatic) goes into a
permanent ledger (app_settings "studio_made"), so it stays "already made"
even after its project is deleted.

The list shows the top TOP_N passing titles per type (pins first), plus the
ones that didn't pass with their reasons. Owner marks (⭐ pin / ✕ skip) are
kept per title across refreshes. Stored in app_settings ("studio_candidates",
"studio_candidate_marks"). Every call here is free (TMDB, IMDb, YouTube quota).
"""

from __future__ import annotations

import datetime
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .. import logbus
from ..db import SessionLocal
from ..models import AppSetting, StudioProject
from . import imdb, tmdb, youtube

logger = logging.getLogger("scrapper.studio.candidates")
UTC = datetime.timezone.utc

TOP_N = 25                 # shown per type (owner, 2026-10-03)
POOL_PAGES = 4             # TMDB trending pages per type (20 a page) — room for 25 to pass
RELEASE_AHEAD = 90         # days
RELEASE_BEHIND = 30        # days
MIN_POPULARITY = 20.0      # TMDB popularity floor …
MIN_TRAILER_VIEWS = 100_000  # … or trailer views …
MAX_METER_RANK = 1000      # … or IMDb meter rank
STALE_HOURS = 72           # auto-refresh every 3 days (owner); ↻ refreshes any time

_lock = threading.Lock()
state: dict[str, Any] = {"refreshing": False, "last_error": None}


def _key(media_type: str, tmdb_id: int) -> str:
    return f"{media_type}:{int(tmdb_id)}"


def _get_setting(s, key: str) -> dict:
    row = s.get(AppSetting, key)
    return dict(row.value or {}) if row else {}


def _put_setting(s, key: str, value: dict) -> None:
    row = s.get(AppSetting, key)
    if row:
        row.value = value
    else:
        s.add(AppSetting(key=key, value=value))


# --- gathering ----------------------------------------------------------------
def _pool() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mt in ("movie", "tv"):
        rank = 0
        for page in range(1, POOL_PAGES + 1):
            for r in tmdb.get(f"/trending/{mt}/week", page=page).get("results", []):
                rank += 1
                out.append({"tmdb_id": r["id"], "media_type": mt, "trending_rank": rank,
                            "popularity": r.get("popularity") or 0, "vote_average": r.get("vote_average")})
    return out


def _details(c: dict[str, Any]) -> dict[str, Any]:
    """TMDB detail + IMDb audience for one title (both free)."""
    mt = c["media_type"]
    append = "credits,videos,external_ids" + (",release_dates" if mt == "movie" else "")
    d = tmdb.get(f"/{mt}/{c['tmdb_id']}", append_to_response=append)
    yt = next((v for v in (d.get("videos") or {}).get("results", [])
               if v.get("site") == "YouTube" and v.get("type") == "Trailer" and v.get("official")), None) or \
        next((v for v in (d.get("videos") or {}).get("results", [])
              if v.get("site") == "YouTube" and v.get("type") == "Trailer"), None)
    row = {
        **c,
        "title": d.get("title") or d.get("name") or "",
        "overview": d.get("overview") or "",
        "poster": tmdb.image_url(d.get("poster_path"), "w342"),
        "cast_count": len((d.get("credits") or {}).get("cast", [])),
        "imdb_id": (d.get("external_ids") or {}).get("imdb_id"),
        "release": _release(mt, d),
        "youtube_trailer": yt.get("key") if yt else None,
        "tmdb_vote": d.get("vote_average"),
        "imdb": None,
    }
    if row["imdb_id"]:
        try:
            row["imdb"] = imdb.audience(row["imdb_id"])
        except Exception as exc:  # noqa: BLE001 — a missing reaction lowers the score, it doesn't drop the title
            row["imdb_error"] = str(exc)[:160]
    return row


def _release(mt: str, d: dict[str, Any]) -> dict[str, Any]:
    """The date that matters for a breakdown: US opening (movie) or the
    nearest episode (TV), with what kind it is."""
    if mt == "movie":
        best = None
        for country in (d.get("release_dates") or {}).get("results", []):
            if country.get("iso_3166_1") != "US":
                continue
            for r in country.get("release_dates", []):
                if r.get("type") in (2, 3, 4) and r.get("release_date"):
                    dt = r["release_date"][:10]
                    if best is None or dt < best["date"]:
                        best = {"date": dt, "kind": {2: "limited", 3: "theaters", 4: "digital"}[r["type"]]}
        return best or {"date": d.get("release_date") or "", "kind": "release"}
    nxt, last = d.get("next_episode_to_air") or {}, d.get("last_episode_to_air") or {}
    return {"date": nxt.get("air_date") or last.get("air_date") or d.get("first_air_date") or "",
            "kind": "next episode" if nxt.get("air_date") else "last episode",
            "next": nxt.get("air_date"), "last": last.get("air_date")}


def _days_from_today(date: str, today: datetime.date) -> int | None:
    try:
        return (datetime.date.fromisoformat(date[:10]) - today).days
    except (TypeError, ValueError):
        return None


# --- checks & score -------------------------------------------------------------
def check(row: dict[str, Any], made: set[str], today: datetime.date) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    def put(name, status, why):
        out[name] = {"status": status, "why": why}

    put("not_made", "fail" if _key(row["media_type"], row["tmdb_id"]) in made else "pass",
        "already made" if _key(row["media_type"], row["tmdb_id"]) in made else "not made yet")

    au = row.get("imdb") or {}
    if au.get("has_trailer"):
        put("trailer", "pass", "official trailer on IMDb")
    elif row.get("youtube_trailer"):
        put("trailer", "warn", "only a YouTube trailer — needs the home Mac worker (not auto-picked)")
    else:
        put("trailer", "fail", "no official trailer on IMDb or TMDB")

    missing = [n for n, ok in (("IMDb id", row.get("imdb_id")), ("poster", row.get("poster")),
                                ("summary", row.get("overview")), ("3+ cast", row.get("cast_count", 0) >= 3)) if not ok]
    put("facts", "fail" if missing else "pass", f"missing {', '.join(missing)}" if missing else "IMDb id, poster, summary, cast")

    rel = row.get("release") or {}
    if row["media_type"] == "movie":
        days = _days_from_today(rel.get("date") or "", today)
        ok = days is not None and -RELEASE_BEHIND <= days <= RELEASE_AHEAD
        why = ("no US release date" if days is None else
               f"opens in {days} days" if days > 0 else "opens today" if days == 0 else f"opened {-days} days ago")
    else:
        nd, ld = _days_from_today(rel.get("next") or "", today), _days_from_today(rel.get("last") or "", today)
        ok = (nd is not None and 0 <= nd <= RELEASE_AHEAD) or (ld is not None and -RELEASE_BEHIND <= ld <= 0)
        why = (f"next episode in {nd} days" if nd is not None and nd >= 0 else
               f"last episode {-ld} days ago" if ld is not None else "no episode dates")
    put("release", "pass" if ok else "fail", why + ("" if ok else f" (window: {RELEASE_BEHIND} days back to {RELEASE_AHEAD} ahead)"))

    views = (row.get("trailer_stats") or {}).get("views") or 0
    rank = au.get("meter_rank")
    signals = []
    if row.get("popularity", 0) >= MIN_POPULARITY:
        signals.append(f"TMDB popularity {row['popularity']:.0f}")
    if views >= MIN_TRAILER_VIEWS:
        signals.append(f"{_short(views)} trailer views")
    if rank and rank <= MAX_METER_RANK:
        signals.append(f"IMDb popularity #{rank}")
    put("interest", "pass" if signals else "fail", ", ".join(signals) if signals else "low interest on every signal")
    return out


def _short(n: float) -> str:
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e3 else str(int(n))


def sentiment(row: dict[str, Any]) -> dict[str, Any]:
    """0–1 audience reaction from whatever is available, plus the parts."""
    au, ts = row.get("imdb") or {}, row.get("trailer_stats") or {}
    parts: dict[str, float] = {}
    if au.get("rating") and (au.get("votes") or 0) >= 50:
        parts["imdb_rating"] = au["rating"] / 10
    if au.get("metascore"):
        parts["metascore"] = au["metascore"] / 100
    if au.get("review_avg"):
        parts["imdb_reviews"] = au["review_avg"] / 10
    if ts.get("views") and ts.get("likes") is not None and ts["views"] >= 1000:
        parts["trailer_likes"] = min(1.0, ts["likes"] / ts["views"] * 25)   # 4% like rate = 1.0
    if row.get("tmdb_vote") and row["tmdb_vote"] > 0:
        parts["tmdb_vote"] = row["tmdb_vote"] / 10
    value = sum(parts.values()) / len(parts) if parts else 0.5   # unknown = neutral
    return {"value": round(value, 3), "parts": {k: round(v, 3) for k, v in parts.items()}}


def score(row: dict[str, Any], today: datetime.date, now: datetime.datetime) -> dict[str, Any]:
    ts = row.get("trailer_stats") or {}
    views = ts.get("views") or 0
    per_day = 0.0
    if views and ts.get("published_at"):
        try:
            pub = datetime.datetime.fromisoformat(ts["published_at"].replace("Z", "+00:00"))
            per_day = views / max(1.0, (now - pub).total_seconds() / 86400)
        except ValueError:
            pass
    rel = row.get("release") or {}
    days = _days_from_today(rel.get("next") or rel.get("date") or "", today)
    timing = 0.0 if days is None else max(0.0, 1 - abs(days) / RELEASE_AHEAD)
    sent = sentiment(row)
    comp = {
        "trailer_heat": 30 * min(1.0, math.log10(per_day + 1) / 6),        # 1M/day = full
        "sentiment": 20 * sent["value"],
        "popularity": 15 * min(1.0, math.log10((row.get("popularity") or 0) + 1) / 3),
        "trending": 15 * max(0.0, 1 - (row.get("trending_rank", 40) - 1) / (POOL_PAGES * 20)),
        "trailer_views": 10 * min(1.0, math.log10(views + 1) / 8),          # 100M = full
        "timing": 10 * timing,
        "rising": 3.0 if (row.get("imdb") or {}).get("meter_change") == "UP" else 0.0,
    }
    return {"total": round(min(100.0, sum(comp.values())), 1),
            "parts": {k: round(v, 1) for k, v in comp.items()},
            "views_per_day": round(per_day), "sentiment": sent}


# --- build / read ---------------------------------------------------------------
def made_keys(s) -> set[str]:
    """Every title ever made: the permanent ledger + current projects."""
    ledger = set(_get_setting(s, "studio_made").get("keys", []))
    return ledger | {_key(p.media_type, p.tmdb_id) for p in s.query(StudioProject.media_type, StudioProject.tmdb_id).all()}


def remember_made(s, media_type: str, tmdb_id: int) -> None:
    """Add a title to the never-repeat ledger (caller commits)."""
    row = _get_setting(s, "studio_made")
    keys = set(row.get("keys", []))
    k = _key(media_type, tmdb_id)
    if k not in keys:
        keys.add(k)
        _put_setting(s, "studio_made", {"keys": sorted(keys)})


def build(now: datetime.datetime | None = None) -> dict[str, Any]:
    """Gather, check, score and store the list. Network-heavy (~2 TMDB + 1 IMDb
    call per title, 8 at a time; 1–2 YouTube calls); free."""
    now = now or datetime.datetime.now(UTC)
    today = now.date()
    pool = _pool()
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(_safe_details, pool))
    rows = [r for r in rows if r]
    yt_error = None
    if youtube.configured():
        try:
            stats = youtube.video_stats([r["youtube_trailer"] for r in rows if r.get("youtube_trailer")])
            for r in rows:
                r["trailer_stats"] = stats.get(r.get("youtube_trailer") or "")
        except youtube.YouTubeError as exc:
            yt_error = str(exc)[:200]
    else:
        yt_error = "YOUTUBE_API_KEY not set — trailer views not used"
    with SessionLocal() as s:
        made = made_keys(s)
    for r in rows:
        r["checks"] = check(r, made, today)
        r["score"] = score(r, today, now)
    rows.sort(key=lambda r: -r["score"]["total"])
    result = {"built_at": now.isoformat(), "youtube_note": yt_error, "rows": rows}
    with SessionLocal() as s:
        _put_setting(s, "studio_candidates", result)
        s.commit()
    logbus.log("info", "studio_candidates_built",
               f"Breakdown candidates refreshed: {len(rows)} titles checked"
               + (f" ({yt_error})" if yt_error else ""))
    return result


def _safe_details(c: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return _details(c)
    except Exception as exc:  # noqa: BLE001 — one bad title must not sink the list
        logger.warning("candidate %s:%s skipped: %s", c["media_type"], c["tmdb_id"], exc)
        return None


def refresh_async() -> bool:
    """Rebuild in the background; False if a rebuild is already running."""
    if not _lock.acquire(blocking=False):
        return False
    state["refreshing"] = True

    def work():
        try:
            build()
            state["last_error"] = None
        except Exception as exc:  # noqa: BLE001
            state["last_error"] = str(exc)[:300]
            logbus.log("error", "studio_candidates_failed", f"Breakdown candidates refresh failed: {exc}")
        finally:
            state["refreshing"] = False
            _lock.release()

    threading.Thread(target=work, daemon=True, name="studio-candidates").start()
    return True


def is_stale(now: datetime.datetime | None = None) -> bool:
    with SessionLocal() as s:
        built = _get_setting(s, "studio_candidates").get("built_at")
    if not built:
        return True
    age = (now or datetime.datetime.now(UTC)) - datetime.datetime.fromisoformat(built)
    return age.total_seconds() > STALE_HOURS * 3600


def passes(row: dict[str, Any], auto: bool = False) -> bool:
    """All checks pass. For the automation a ⚠ (YouTube-only trailer) doesn't count."""
    bad = ("fail", "warn") if auto else ("fail",)
    return all(c["status"] not in bad for c in row["checks"].values())


def listing(s=None) -> dict[str, Any]:
    """The stored list with live 'already made' status and owner marks applied,
    split into the top TOP_N passing per type and the ones that didn't pass."""
    own = s is None
    s = s or SessionLocal()
    try:
        data = _get_setting(s, "studio_candidates")
        marks = _get_setting(s, "studio_candidate_marks")
        made = made_keys(s)
        today = datetime.datetime.now(UTC).date()
        out: dict[str, Any] = {"built_at": data.get("built_at"), "youtube_note": data.get("youtube_note"),
                               "refreshing": state["refreshing"], "last_error": state["last_error"],
                               "movie": [], "tv": [], "not_passing": []}
        for r in data.get("rows", []):
            r = dict(r)
            k = _key(r["media_type"], r["tmdb_id"])
            r["key"] = k
            r["mark"] = marks.get(k)
            r["checks"] = check(r, made, today)       # "already made" / release window are live
            r["auto_ok"] = passes(r, auto=True) and r["mark"] != "skip"
            if r["mark"] == "skip" or not passes(r):
                out["not_passing"].append(r)
            else:
                out[r["media_type"]].append(r)
        for mt in ("movie", "tv"):
            out[mt].sort(key=lambda r: (r["mark"] != "pin", -r["score"]["total"]))
            extra = out[mt][TOP_N:]
            out[mt] = out[mt][:TOP_N]
            out["not_passing"].extend({**r, "beyond_top": True} for r in extra)
        return out
    finally:
        if own:
            s.close()


def set_mark(key: str, mark: str | None) -> dict[str, Any]:
    if mark not in (None, "pin", "skip"):
        raise ValueError("mark must be pin, skip or empty")
    mt, _, tid = key.partition(":")
    if mt not in ("movie", "tv") or not tid.isdigit():
        raise ValueError("key must look like movie:123 or tv:456")
    with SessionLocal() as s:
        marks = _get_setting(s, "studio_candidate_marks")
        if mark:
            marks[key] = mark
        else:
            marks.pop(key, None)
        _put_setting(s, "studio_candidate_marks", marks)
        s.commit()
    logbus.log("info", "studio_candidate_marked", f"Breakdown candidate {key}: {mark or 'cleared'}")
    return listing()


def next_pick(media_types: list[str]) -> dict[str, Any] | None:
    """Best title the automation may start: pins first, then score; only
    titles that pass every check with an IMDb trailer."""
    lst = listing()
    rows = [r for mt in media_types for r in lst[mt] if r["auto_ok"]]
    rows.sort(key=lambda r: (r["mark"] != "pin", -r["score"]["total"]))
    return rows[0] if rows else None
