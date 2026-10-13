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
import logging
import re
from typing import Any

from .. import control, costs, logbus, vidiq
from ..db import SessionLocal
from ..models import AppSetting

logger = logging.getLogger("scrapper.countdown.topics")
UTC = datetime.timezone.utc
KEY, LEDGER = "countdown_topics_v2", "countdown_topic_ledger"
STALE = datetime.timedelta(days=3)
SIZES = (5, 10, 15)
DEMAND_CHECKS = 8                 # vidIQ demand lookups per build (5 credits each, cached 3 days)
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
                  + vidiq.breakout_videos("best tv shows ranked", purpose="countdown topics")
                  + vidiq.trending("top 10 movies ranked", purpose="countdown topics")):
            t = v.get("title") or ""
            if LISTY.search(t) and SCREEN.search(t) and t not in {x["title"] for x in videos}:
                videos.append(v)
    videos.sort(key=lambda v: -(v.get("views") or 0))
    return {**ctx, "month": month_titles[:25], "videos": videos[:20], "month_name": today.strftime("%B %Y")}


def _prompt(sig_: dict[str, Any], avoid: list[str]) -> str:
    vids = "\n".join(f"- \"{v['title']}\" — {v.get('views') or 0:,} views" + (f", breakout ×{round(v['breakout'])}" if v.get("breakout") else "")
                     for v in sig_["videos"]) or "- (none available)"
    return f"""You plan Top 5 / Top 10 / Top 15 COUNTDOWN videos for the YouTube channel "Screen Central"
(movies and TV). Today is {sig_['today']}; this month is {sig_['month_name']}.

Signals (real data):
- Releasing this month in the US: {', '.join(sig_['month']) or 'n/a'}
- Upcoming next weeks: {', '.join(sig_['upcoming']) or 'n/a'}
- Trending titles: {', '.join(sig_['trending']) or 'n/a'}
- TV airing now: {', '.join(sig_['tv_airing']) or 'n/a'}
- Countdown/list videos breaking out on YouTube right now:
{vids}

Propose 18 distinct countdown topics viewers are searching for NOW:
- 6 tied to this month (new releases, streaming arrivals, seasonal/holiday, anniversaries) → "group": "month"
- 6 riding what's breaking out on YouTube (same angle, our own take) → "group": "trend"
- 6 evergreen (genre bests, underrated, director/actor rankings) → "group": "evergreen"
Rules: every candidate must be a real MOVIE or TV SHOW (never scenes, theories, characters or moments).
Do not repeat or lightly reword these already-made topics: {', '.join(avoid[:40]) or 'none'}.

Return JSON array, each item:
{{"topic": "Top 10 ... (YouTube-style, no clickbait lies)",
  "keyword": "2-5 word search phrase people type (e.g. 'best horror movies 2026')",
  "group": "month|trend|evergreen", "why_now": "one sentence",
  "debate": 1-10, "best_format": "top5|top10|top15",
  "titles": [{{"title": "Real Title", "year": 2024}}, ... up to 15, strongest first]}}"""


def _resolve_all(titles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from .compilations import engine
    out = []
    for t in titles[:15]:
        if not isinstance(t, dict) or not t.get("title"):
            continue
        r = engine._resolve_title({"title": t["title"], "year": t.get("year")})
        if r:
            out.append({"title": r.get("title") or t["title"], "year": t.get("year"), "tmdb_id": r["tmdb_id"],
                        "media_type": r.get("media_type", "movie"), "poster": r.get("poster")})
    return out


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
                       "best_format": t.get("best_format") if t.get("best_format") in ("top5", "top10", "top15") else "top10",
                       "titles": real, "sizes": sizes})
    # Demand from vidIQ for the most promising ones (others show "not checked").
    prelim = sorted(topics, key=lambda x: (x["group"] != "evergreen", x["debate"], len(x["titles"])), reverse=True)
    for t in prelim[:DEMAND_CHECKS]:
        control.check()
        control.progress(f"vidIQ demand: {t['keyword']}")
        m = vidiq.keyword_metrics(t["keyword"], purpose="countdown topic demand")
        if m:
            t["demand"] = {k: m[k] for k in ("overall", "volume", "competition", "monthly_searches", "growth_pct")}
            t["keywords"] = [r["keyword"] for r in m["related"][:12]]
    for t in topics:
        d = t.get("demand")
        demand = d["overall"] if d else 50.0
        growth = min(max((d or {}).get("growth_pct", 0), -50), 100)
        timely = {"month": 100, "trend": 90, "evergreen": 45}[t["group"]]
        t["checks"] = {
            "new": {"status": "pass", "why": "Never made or skipped"},
            "titles": {"status": "pass" if 15 in t["sizes"] else "warn",
                       "why": f"{len(t['titles'])} real titles on TMDB — can make Top {', Top '.join(str(n) for n in t['sizes'])}"},
            "demand": ({"status": "pass" if demand >= 55 else "warn" if demand >= 35 else "fail",
                        "why": f"vidIQ: {d['monthly_searches']:,} searches/month, competition {d['competition']:.0f}/100, "
                               f"{'+' if d['growth_pct'] >= 0 else ''}{d['growth_pct']:.0f}% vs last month"}
                       if d else {"status": "warn", "why": "Not checked with vidIQ this round"}),
            "timely": {"status": "pass" if t["group"] != "evergreen" else "warn",
                       "why": {"month": "Tied to this month", "trend": "Riding what's breaking out on YouTube",
                               "evergreen": "Evergreen — not tied to this week"}[t["group"]]},
            "debate": {"status": "pass" if t["debate"] >= 7 else "warn", "why": f"Comment-debate potential {t['debate']}/10"},
        }
        t["score"] = round(0.40 * demand + 0.10 * (50 + growth / 2) + 0.25 * timely + 0.15 * t["debate"] * 10
                           + 0.10 * min(100, len(t["titles"]) / 15 * 100), 1)
    topics.sort(key=lambda x: -x["score"])
    data = {"built_at": datetime.datetime.now(UTC).isoformat(), "month": sig_["month_name"], "topics": topics,
            "videos": sig_["videos"][:12], "vidiq": vidiq.configured()}
    with SessionLocal() as s:
        row = s.get(AppSetting, KEY)
        if row:
            row.value = data
        else:
            s.add(AppSetting(key=KEY, value=data))
        s.commit()
    logbus.log("info", "countdown_topics_built", f"Countdown topics rebuilt: {len(topics)} topics "
                                                 f"({sum(1 for t in topics if t.get('demand'))} checked with vidIQ)")
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
            "videos": data.get("videos") or [], "building": bool(control.running("countdown_topics")),
            "skipped": [t for t in data.get("topics") or [] if t["sig"] in set(led["skipped"])],
            "vidiq": vidiq.configured()}


def find(topic_sig: str) -> dict[str, Any] | None:
    return next((t for t in stored().get("topics") or [] if t["sig"] == topic_sig), None)
