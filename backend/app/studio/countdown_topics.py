"""Countdown topics — checked & ranked, like LongForm's trending list (2026-10-13).

Owner: "the topic ideas should be fresh and updated and need to work for Top 5 /
Top 10 / Top 15 … use vidIQ to its fullest … find trends."

build() (background job, every 3 days, on the 1st of the month, or ↻ Refresh):
  1. Signals: this month's and upcoming releases + TV airing (TMDB calendar) and
     countdown videos breaking out / trending on YouTube right now (vidIQ).
  2. The AI proposes ~18 countdown topics from those signals, each with up to 15
     candidate titles and a short search phrase.
  3. Checks per topic: New (never made or skipped), Titles (how many candidates are
     real TMDB titles → which of Top 5/10/15 it can fill), Demand (vidIQ search
     volume / competition / growth for the phrase), Timely, Debate.
  4. A 0–100 score; groups: 🔥 top · 📅 this month · evergreen.
Costs: one AI call + about 11 vidIQ calls (≈ 55 credits, cached) per build.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math
import re
from typing import Any

from .. import control, costs, logbus, vidiq
from ..db import SessionLocal
from ..models import AppSetting

logger = logging.getLogger("scrapper.countdown.topics")
UTC = datetime.timezone.utc
KEY, LEDGER = "countdown_topics_v2", "countdown_topic_ledger"
STALE = datetime.timedelta(days=1)          # owner (2026-10-13): fresh every day
SIZES = (5, 10, 15)
ASK_TITLES = 18     # owner (2026-10-14): ask for 15 + spares so most topics can fill a Top 15 once checked
_TOP_N = re.compile(r"\b(the\s+)?top\s+\d+\b", re.I)


def sized(topic: str, n: int) -> str:
    """The topic worded for the size picked: "Top 10 Scary Movies…" → "Top 15 Scary Movies…"."""
    if _TOP_N.search(topic):
        return _TOP_N.sub(lambda m: ("The " if m.group(1) else "") + f"Top {n}", topic, count=1)
    return f"Top {n} {topic}"
DEMAND_CACHE_H = 168              # vidIQ demand per search phrase, cached 7 days (5 credits each)
# Broad seeds: one vidIQ call each returns ~20 real search phrases with their monthly searches.
# Cached a week (≈ 30 credits/week), so every topic can be built on a phrase with known demand.
SEEDS = ["best movies", "best tv shows", "horror movies", "new movies", "netflix movies", "action movies"]
EXTRA_CHECKS = 3                  # per build: phrases not in the pool get looked up individually (5 credits each)
STRONG, WEAK = 20_000, 5_000      # monthly searches: Demand ✓ at 20K+, ⚠ 5–20K, ✕ below 5K
LISTY = re.compile(r"\b(top ?\d+|best|worst|ranked|ranking|every|greatest|underrated|overrated|scariest)\b", re.I)
SCREEN = re.compile(r"\b(movie|movies|film|films|show|shows|series|tv|anime|horror|thriller|netflix|marvel|disney|hbo|prime|sci-?fi)\b", re.I)


def sig(topic: str) -> str:
    """Topic identity for the never-repeat ledger (ignores 'Top 10', case, punctuation)."""
    t = re.sub(r"\btop\s*\d+\b", "", (topic or "").lower())
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return hashlib.sha1(" ".join(t.split()).encode()).hexdigest()[:16]


# --- ledger: made / skipped / pinned ------------------------------------------------------
def ledger(s=None) -> dict[str, Any]:
    own = s is None
    s = s or SessionLocal()
    try:
        row = s.get(AppSetting, LEDGER)
        v = dict(row.value or {}) if row else {}
        return {"made": list(v.get("made") or []), "skipped": list(v.get("skipped") or []), "pinned": list(v.get("pinned") or [])}
    finally:
        if own:
            s.close()


def mark(topic_sig: str, kind: str, on: bool = True) -> dict[str, Any]:
    with SessionLocal() as s:
        led = ledger(s)
        lst = set(led[kind])
        (lst.add if on else lst.discard)(topic_sig)
        led[kind] = sorted(lst)
        row = s.get(AppSetting, LEDGER)
        if row:
            row.value = led
        else:
            s.add(AppSetting(key=LEDGER, value=led))
        s.commit()
        return led


# --- signals --------------------------------------------------------------------------------
def _signals() -> dict[str, Any]:
    from .compilations import engine
    ctx = engine._gather_market_context()
    today = datetime.date.today()
    month_titles: list[str] = []
    try:
        from . import tmdb
        cal = tmdb.calendar() if tmdb.configured() else {}
        for m in cal.get("upcoming_movies") or []:
            if (m.get("date") or "")[:7] == today.isoformat()[:7] and m.get("title"):
                month_titles.append(f"{m['title']} ({m['date']})")
    except Exception as exc:  # noqa: BLE001
        logger.warning("calendar unavailable: %s", exc)
    videos: list[dict[str, Any]] = []
    if vidiq.configured():
        for v in (vidiq.breakout_videos("top 10 movies", purpose="countdown topics")
                  + vidiq.trending("top 10 movies ranked", purpose="countdown topics")):
            t = v.get("title") or ""
            if LISTY.search(t) and SCREEN.search(t) and t not in {x["title"] for x in videos}:
                videos.append(v)
    videos.sort(key=lambda v: -(v.get("views") or 0))
    pool: dict[str, dict[str, Any]] = {}
    if vidiq.configured():
        for seed in SEEDS:
            m = vidiq.keyword_metrics(seed, purpose="countdown phrase pool", cache_hours=DEMAND_CACHE_H)
            if not m:
                continue
            for k in [{"keyword": m.get("keyword") or seed, "monthly_searches": m.get("monthly_searches", 0),
                       "growth_pct": m.get("growth_pct", 0), "competition": m.get("competition", 0),
                       "overall": m.get("overall", 0)}, *(m.get("related") or [])]:
                if not k.get("keyword"):
                    continue
                key = k["keyword"].lower().strip()
                if k.get("monthly_searches", 0) >= WEAK and key not in pool:
                    pool[key] = k
    rising: list[dict[str, Any]] = []
    if vidiq.configured():
        # Searches climbing fastest this week: "trailers" + one film/TV category, rotating daily (10 credits).
        cats = ["trailers", vidiq.RISING_TOPICS[1 + today.toordinal() % (len(vidiq.RISING_TOPICS) - 1)]]
        for cat in cats:
            for r in vidiq.rising(cat, purpose="countdown topics"):
                if r["monthly_searches"] >= WEAK:
                    rising.append({**r, "category": cat})
        rising.sort(key=lambda r: -r["monthly_searches"])
    asks = ""
    if vidiq.configured():
        # What viewers of movie/TV countdown channels keep asking for (5 credits, refreshed weekly).
        raw = vidiq.safe("vidiq_comment_insights", {"mode": "discover", "niche": "movie and TV top 10 countdown and ranking videos",
                                                    "language": "en"}, cache_hours=168, purpose="countdown viewer requests")
        asks = (raw if isinstance(raw, str) else json.dumps(raw))[:1800] if raw else ""
    for r in rising:
        pool.setdefault(r["keyword"].lower().strip(), {"keyword": r["keyword"], "monthly_searches": r["monthly_searches"],
                                                       "growth_pct": r["change_pct"], "rising": True})
    phrases = sorted(pool.values(), key=lambda k: -k.get("monthly_searches", 0))[:70]
    return {**ctx, "month": month_titles[:25], "videos": videos[:20], "month_name": today.strftime("%B %Y"), "asks": asks,
            "rising": rising[:25], "pool": phrases}


def _prompt(sig_: dict[str, Any], avoid: list[str]) -> str:
    vids = "\n".join(f"- \"{v['title']}\" — {v.get('views') or 0:,} views" + (f", breakout ×{round(v['breakout'])}" if v.get("breakout") else "")
                     for v in sig_["videos"]) or "- (none available)"
    rising = "\n".join(f"- \"{r['keyword']}\" — {r['monthly_searches']:,}/month, +{r['change_pct']}% ({r['category']})"
                       for r in sig_.get("rising") or [])
    pool = "\n".join(f"- {p['keyword']} — {p['monthly_searches']:,}/month" for p in sig_.get("pool") or [])
    return f"""You plan Top 5 / Top 10 / Top 15 COUNTDOWN videos for the YouTube channel "Screen Central"
(movies and TV). Today is {sig_['today']}; this month is {sig_['month_name']}.

Signals (real data):
- Releasing this month in the US: {', '.join(sig_['month']) or 'n/a'}
- Upcoming next weeks: {', '.join(sig_['upcoming']) or 'n/a'}
- Trending titles: {', '.join(sig_['trending']) or 'n/a'}
- TV airing now: {', '.join(sig_['tv_airing']) or 'n/a'}
- Countdown/list videos breaking out on YouTube right now:
{vids}
- What viewers of countdown channels keep asking for in comments (vidIQ):
{sig_.get('asks') or '(not available)'}
- Searches rising fastest on YouTube this week (vidIQ, monthly searches, % change):
{rising or '- (none available)'}

Propose 15 distinct countdown topics with HIGH search demand right now (thousands of searches a month), new and timely:
- 5 tied to this month (new releases, streaming arrivals, seasonal/holiday, anniversaries) → "group": "month"
- 5 riding what's breaking out or rising on YouTube (same angle, our own take) → "group": "trend"
- 5 evergreen with big search volume (genre bests, underrated, director/actor rankings) → "group": "evergreen"
For "keyword": the search phrase this video targets. {"It MUST be copied exactly from this list of real YouTube searches (monthly searches shown) — build topics around the biggest ones:" + chr(10) + pool if pool else "Use the phrase people actually type into YouTube search (broad and popular, e.g. 'best horror movies')."}
Rules: every candidate must be a real MOVIE or TV SHOW (never scenes, theories, characters or moments).
Do not repeat or lightly reword these already-made topics: {', '.join(avoid[:40]) or 'none'}.

Return JSON array, each item:
{{"topic": "Top 10 ... (YouTube-style, no clickbait lies)",
  "keyword": "2-5 word search phrase people type (e.g. 'best horror movies 2026')",
  "group": "month|trend|evergreen", "why_now": "one sentence",
  "debate": 1-10, "best_format": "top5|top10|top15",
  "titles": [{{"title": "Real Title", "year": 2024}}, ... EXACTLY 18 real titles, strongest first]}}
Every topic needs 18 real, distinct titles (so it can be made as a Top 5, Top 10 OR Top 15) — pick angles broad enough for that."""


def _resolve_all(titles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .compilations import engine
    out = []
    for t in titles[:ASK_TITLES]:
        if not isinstance(t, dict) or not t.get("title"):
            continue
        r = engine._resolve_title({"title": t["title"], "year": t.get("year")})
        if r:
            out.append({"title": r.get("title") or t["title"], "year": t.get("year"), "tmdb_id": r["tmdb_id"],
                        "media_type": r.get("media_type", "movie"), "poster": r.get("poster")})
    return out


def _best(fmt: Any, sizes: list[int]) -> str:
    """The AI's preferred size, but never one the real titles can't fill."""
    want = int(str(fmt)[3:]) if fmt in ("top5", "top10", "top15") else 10
    return f"top{max([n for n in sizes if n <= want] or [min(sizes)])}"


def build() -> dict[str, Any]:
    """Rebuild the list (background job). Never raises for vidIQ problems."""
    from . import gemini
    control.progress("collecting release calendar and YouTube trends")
    sig_ = _signals()
    led = ledger()
    with SessionLocal() as s:
        from ..models import CountdownProject
        made_topics = [c.topic for c in s.query(CountdownProject).all()]
    control.check()
    control.progress("asking the AI for topics")
    with costs.operation("countdown_topics", ref="countdown:topics"):
        raw = gemini.ask_json(_prompt(sig_, made_topics), temperature=0.7, max_tokens=16384)
    if not isinstance(raw, list) or not raw:
        raise ValueError("The AI didn't return topics — try ↻ Refresh again")
    skip = set(led["made"]) | set(led["skipped"]) | {sig(t) for t in made_topics}
    topics = []
    for t in raw:
        if not isinstance(t, dict) or not t.get("topic"):
            continue
        sg = sig(t["topic"])
        if sg in skip or sg in {x["sig"] for x in topics}:
            continue
        control.check()
        control.progress(f"checking titles: {t['topic'][:50]}")
        real = _resolve_all(t.get("titles") or [])
        sizes = [n for n in SIZES if len(real) >= n]
        if not sizes:
            continue                                         # can't even fill a Top 5 with real titles
        topics.append({"sig": sg, "topic": t["topic"][:200], "keyword": (t.get("keyword") or t["topic"])[:80],
                       "group": t.get("group") if t.get("group") in ("month", "trend", "evergreen") else "evergreen",
                       "why_now": (t.get("why_now") or "")[:300], "debate": max(1, min(10, int(t.get("debate") or 5))),
                       "best_format": _best(t.get("best_format"), sizes),
                       "titles": real, "sizes": sizes})
    # Demand for every topic: from the phrase pool (already known), else a few individual lookups.
    pool = {p["keyword"].lower().strip(): p for p in sig_.get("pool") or []}
    extra = 0
    for t in topics:
        hit = pool.get(t["keyword"].lower().strip())
        if hit:
            t["demand"] = {"overall": hit.get("overall", 0), "volume": hit.get("volume", 0), "competition": hit.get("competition", 0),
                           "monthly_searches": int(hit.get("monthly_searches", 0)), "growth_pct": float(hit.get("growth_pct") or 0)}
            t["keywords"] = [p["keyword"] for p in pool.values()
                             if p["keyword"] != hit["keyword"] and set(p["keyword"].split()) & set(hit["keyword"].split())][:10]
            continue
        if extra >= EXTRA_CHECKS:
            continue
        control.check()
        control.progress(f"vidIQ demand: {t['keyword']}")
        m = vidiq.keyword_metrics(t["keyword"], purpose="countdown topic demand", cache_hours=DEMAND_CACHE_H)
        extra += 1
        if m:
            t["demand"] = {k: m[k] for k in ("overall", "volume", "competition", "monthly_searches", "growth_pct")}
            t["keywords"] = [r["keyword"] for r in m["related"][:12]]
    for t in topics:
        d = t.get("demand")
        searches = (d or {}).get("monthly_searches", 0)
        # 0 at 1K searches/month, 50 at ~17K, 100 at 300K (log scale)
        search_score = max(0.0, min(100.0, (math.log10(max(searches, 1)) - 3) / (math.log10(300_000) - 3) * 100)) if d else 35.0
        growth = min(max((d or {}).get("growth_pct", 0), -50), 100)
        timely = {"month": 100, "trend": 90, "evergreen": 45}[t["group"]]
        t["checks"] = {
            "new": {"status": "pass", "why": "Never made or skipped"},
            "titles": {"status": "pass" if 15 in t["sizes"] else "warn",
                       "why": f"{len(t['titles'])} real titles on TMDB — can make Top {', Top '.join(str(n) for n in t['sizes'])}"},
            "demand": ({"status": "pass" if searches >= STRONG else "warn" if searches >= WEAK else "fail",
                        "why": f"vidIQ: {searches:,} searches/month for “{t['keyword']}”, competition {d['competition']:.0f}/100, "
                               f"{'+' if d['growth_pct'] >= 0 else ''}{d['growth_pct']:.0f}% vs last month"
                               + ("" if searches >= STRONG else f" (✓ needs {STRONG:,}+)")}
                       if d else {"status": "warn", "why": "Not checked with vidIQ (cap reached or vidIQ off)"}),
            "timely": {"status": "pass" if t["group"] != "evergreen" else "warn",
                       "why": {"month": "Tied to this month", "trend": "Riding what's breaking out or rising on YouTube",
                               "evergreen": "Evergreen — not tied to this week"}[t["group"]]},
            "debate": {"status": "pass" if t["debate"] >= 7 else "warn", "why": f"Comment-debate potential {t['debate']}/10"},
        }
        t["score"] = round(0.45 * search_score + 0.10 * (50 + growth / 2) + 0.20 * timely + 0.15 * t["debate"] * 10
                           + 0.10 * min(100, len(t["titles"]) / 15 * 100), 1)
    topics.sort(key=lambda x: -x["score"])
    data = {"built_at": datetime.datetime.now(UTC).isoformat(), "month": sig_["month_name"], "topics": topics,
            "videos": sig_["videos"][:12], "vidiq": vidiq.configured(), "asks": sig_.get("asks") or "",
            "rising": sig_.get("rising") or []}
    with SessionLocal() as s:
        row = s.get(AppSetting, KEY)
        if row:
            row.value = data
        else:
            s.add(AppSetting(key=KEY, value=data))
        s.commit()
    logbus.log("info", "countdown_topics_built", f"Countdown topics rebuilt: {len(topics)} topics "
                                                 f"({sum(1 for t in topics if t.get('demand'))} checked with vidIQ, "
                                                 f"{sum(1 for t in topics if (t.get('demand') or {}).get('monthly_searches', 0) >= STRONG)} with {STRONG:,}+ searches/month)")
    return {"topics": len(topics)}


def stored() -> dict[str, Any]:
    with SessionLocal() as s:
        row = s.get(AppSetting, KEY)
        return dict(row.value or {}) if row else {}


def is_stale(now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(UTC)
    built = stored().get("built_at")
    if not built:
        return True
    b = datetime.datetime.fromisoformat(built)
    return now - b > STALE or (b.astimezone(costs.day_tz()).month != now.astimezone(costs.day_tz()).month)


def refresh_async() -> str | None:
    if control.running("countdown_topics"):
        return None
    return control.start_thread("countdown_topics", "Countdown topics: rebuild (calendar + vidIQ trends)",
                                "countdown_topics", build)


def listing() -> dict[str, Any]:
    """The list for the page, with pins first and made/skipped topics left out."""
    data = stored()
    led = ledger()
    gone = set(led["made"]) | set(led["skipped"])
    pinned = set(led["pinned"])
    topics = [{**t, "pinned": t["sig"] in pinned} for t in data.get("topics") or [] if t["sig"] not in gone]
    topics.sort(key=lambda t: (not t["pinned"], -t["score"]))
    return {"built_at": data.get("built_at"), "month": data.get("month"), "topics": topics,
            "videos": data.get("videos") or [], "asks": data.get("asks") or "", "rising": data.get("rising") or [],
            "building": bool(control.running("countdown_topics")),
            "skipped": [t for t in data.get("topics") or [] if t["sig"] in set(led["skipped"])],
            "vidiq": vidiq.configured()}


def find(topic_sig: str) -> dict[str, Any] | None:
    return next((t for t in stored().get("topics") or [] if t["sig"] == topic_sig), None)
