"""vidIQ client (MCP over HTTP) — topic demand, keywords, tags/descriptions, trends.

Owner (2026-10-13): "use vidIQ to its fullest… topics, tags and descriptions…
optimize and pull insights… find trends." Plan: Boost, 2,000 AI credits a month.

- Talks to vidIQ's MCP endpoint (JSON-RPC over streamable HTTP) with the API key
  from Railway (VIDIQ_API_KEY). Optional: without the key everything else works.
- Credits are guarded: a daily cap (default 60 ≈ 2,000/month) and a monthly cap
  (default 1,800, leaving room for your own use in vidIQ). Free tools (balance,
  trend categories, tool list, …) don't count. Each paid call is logged.
- Results are cached (default 24 h) so the same question never costs twice.
- If vidIQ fails, it backs off for 30 minutes; callers get None and carry on.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import threading
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import logbus
from .auth import require_token
from .config import settings
from .db import SessionLocal
from .models import AppSetting

logger = logging.getLogger("scrapper.vidiq")
UTC = datetime.timezone.utc

DEFAULTS = {"enabled": True, "daily_credits": 100, "monthly_credits": 1850, "cache_hours": 24}
COST_DEFAULT = 5                      # vidIQ: "most research tools cost 5 credits per call"
# Free per vidIQ (balance, your channels, jobs, bookmarks, competitor list, trend categories,
# voices, imports, feedback). Exact names from tools/list on 2026-10-13.
FREE = {"vidiq_balance", "vidiq_user_channels", "vidiq_jobs_list", "vidiq_job_poll", "vidiq_bookmarks_list",
        "vidiq_bookmarks_save", "vidiq_bookmarks_remove", "vidiq_list_competitors", "vidiq_update_competitors",
        "vidiq_trend_categories", "vidiq_voiceover_list_voices", "vidiq_video_upload", "vidiq_submit_feedback"}
# Known heavier tools (estimates err high; vidiq_balance shows the true balance).
COSTS = {"vidiq_video_watch": 25, "vidiq_watch_shortform_content": 10, "vidiq_generate_thumbnail": 22,
         "vidiq_refine_thumbnail": 22, "vidiq_voiceover_generate": 14, "vidiq_generate_video": 50,
         "vidiq_generate_music": 20, "vidiq_compose": 20, "vidiq_generate_clips": 30, "vidiq_generate_script": 10}
BACKOFF = 30 * 60
CACHE_MAX = 400

_lock = threading.Lock()
_session: dict[str, Any] = {"id": None, "at": 0.0}
_state: dict[str, Any] = {"down_until": 0.0, "last_error": None, "tools": None, "tools_at": 0.0}


class VidIQError(Exception):
    pass


class CreditCapReached(VidIQError):
    pass


def configured() -> bool:
    return bool(settings.vidiq_api_key)


# --- settings + credit ledger ---------------------------------------------------------
def settings_value(s=None) -> dict[str, Any]:
    own = s is None
    s = s or SessionLocal()
    try:
        row = s.get(AppSetting, "vidiq_settings")
        return {**DEFAULTS, **((row.value or {}) if row else {})}
    finally:
        if own:
            s.close()


def _ledger(s) -> dict[str, Any]:
    row = s.get(AppSetting, "vidiq_ledger")
    return dict(row.value or {}) if row else {}


def _day() -> str:
    from . import costs
    return datetime.datetime.now(UTC).astimezone(costs.day_tz()).date().isoformat()


def usage(s=None) -> dict[str, Any]:
    own = s is None
    s = s or SessionLocal()
    try:
        led = _ledger(s)
        day, month = _day(), _day()[:7]
        return {"today": int((led.get("days") or {}).get(day, 0)),
                "month": sum(int(v) for k, v in (led.get("days") or {}).items() if k.startswith(month)),
                "calls_today": int((led.get("calls") or {}).get(day, 0))}
    finally:
        if own:
            s.close()


def _charge(tool: str, credits: int) -> None:
    with SessionLocal() as s:
        led = _ledger(s)
        day = _day()
        days = dict(led.get("days") or {})
        calls = dict(led.get("calls") or {})
        days[day] = int(days.get(day, 0)) + credits
        calls[day] = int(calls.get(day, 0)) + 1
        keep = sorted(days)[-62:]                         # ~2 months of history
        led = {"days": {k: days[k] for k in keep}, "calls": {k: calls.get(k, 0) for k in keep}}
        row = s.get(AppSetting, "vidiq_ledger")
        if row:
            row.value = led
        else:
            s.add(AppSetting(key="vidiq_ledger", value=led))
        s.commit()


def cost_of(tool: str) -> int:
    if tool in FREE:
        return 0
    return COSTS.get(tool, COST_DEFAULT)


def _check_credits(tool: str) -> int:
    c = cost_of(tool)
    if not c:
        return 0
    cfg = settings_value()
    u = usage()
    if u["today"] + c > int(cfg["daily_credits"]):
        raise CreditCapReached(f"vidIQ daily credit cap reached ({u['today']} of {cfg['daily_credits']})")
    if u["month"] + c > int(cfg["monthly_credits"]):
        raise CreditCapReached(f"vidIQ monthly credit cap reached ({u['month']} of {cfg['monthly_credits']})")
    return c


# --- cache --------------------------------------------------------------------------------
def _key(tool: str, args: dict) -> str:
    return hashlib.sha1(f"{tool}:{json.dumps(args, sort_keys=True)}".encode()).hexdigest()[:20]


def _cache_get(k: str, hours: float) -> Any:
    with SessionLocal() as s:
        row = s.get(AppSetting, "vidiq_cache")
        hit = ((row.value or {}) if row else {}).get(k)
    if hit and time.time() - hit.get("t", 0) < hours * 3600:
        return hit.get("v")
    return None


def _cache_put(k: str, v: Any) -> None:
    with SessionLocal() as s:
        row = s.get(AppSetting, "vidiq_cache")
        data = dict((row.value or {}) if row else {})
        data[k] = {"t": time.time(), "v": v}
        if len(data) > CACHE_MAX:
            for old in sorted(data, key=lambda x: data[x].get("t", 0))[: len(data) - CACHE_MAX]:
                data.pop(old, None)
        if row:
            row.value = data
        else:
            s.add(AppSetting(key="vidiq_cache", value=data))
        s.commit()


# --- MCP over HTTP ----------------------------------------------------------------------------
def _headers() -> dict[str, str]:
    h = {"Authorization": f"Bearer {settings.vidiq_api_key}", "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    if _session["id"]:
        h["Mcp-Session-Id"] = _session["id"]
    return h


def _parse(r: httpx.Response) -> dict[str, Any]:
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        last = None
        for line in r.text.splitlines():
            if line.startswith("data:"):
                try:
                    last = json.loads(line[5:].strip())
                except ValueError:
                    continue
        if last is None:
            raise VidIQError("empty event stream from vidIQ")
        return last
    return r.json()


def _rpc(method: str, params: dict | None = None, notify: bool = False, timeout: float = 60) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        body["id"] = int(time.time() * 1000) % 10_000_000
    r = httpx.post(settings.vidiq_mcp_url, headers=_headers(), json=body, timeout=timeout)
    if r.status_code in (401, 403):
        raise VidIQError(f"vidIQ refused the API key ({r.status_code}) — check VIDIQ_API_KEY in Railway")
    if r.status_code == 404 and _session["id"]:
        _session["id"] = None                              # session expired: start a new one
        raise VidIQError("session expired")
    if r.status_code >= 400:
        raise VidIQError(f"vidIQ HTTP {r.status_code}: {r.text[:200]}")
    sid = r.headers.get("mcp-session-id")
    if sid:
        _session["id"] = sid
    if notify:
        return None
    msg = _parse(r)
    if "error" in msg:
        raise VidIQError(f"vidIQ error: {msg['error'].get('message') if isinstance(msg['error'], dict) else msg['error']}")
    return msg.get("result")


def _ensure_session() -> None:
    if _session["id"] and time.time() - _session["at"] < 20 * 60:
        return
    _session["id"] = None
    _rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "scrapper", "version": "1.0"}})
    _session["at"] = time.time()
    try:
        _rpc("notifications/initialized", notify=True)
    except VidIQError:
        pass


def _call(method: str, params: dict | None = None) -> Any:
    if not configured():
        raise VidIQError("VIDIQ_API_KEY isn't set in Railway")
    if time.time() < _state["down_until"]:
        raise VidIQError(f"vidIQ is backing off after an error: {_state['last_error']}")
    with _lock:
        for attempt in range(2):
            try:
                _ensure_session()
                out = _rpc(method, params)
                _state["last_error"] = None
                return out
            except VidIQError as exc:
                if str(exc) == "session expired" and attempt == 0:
                    continue
                _fail(str(exc))
                raise
            except httpx.HTTPError as exc:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                _fail(f"network: {exc}")
                raise VidIQError(f"vidIQ unreachable: {exc}") from exc
    raise VidIQError("vidIQ call failed")


def _fail(why: str) -> None:
    _state["last_error"] = why[:300]
    if "refused the API key" in why or "HTTP 5" in why or "unreachable" in why or "network" in why:
        _state["down_until"] = time.time() + BACKOFF
    logbus.log("error", "vidiq_error", f"vidIQ: {why[:200]}")


def tools(refresh: bool = False) -> list[dict[str, Any]]:
    """vidIQ's tool list (free)."""
    if _state["tools"] is not None and not refresh and time.time() - _state["tools_at"] < 6 * 3600:
        return _state["tools"]
    res = _call("tools/list", {}) or {}
    _state["tools"] = res.get("tools") or []
    _state["tools_at"] = time.time()
    return _state["tools"]


def _content(result: Any) -> Any:
    """MCP tool result → structured content if given, else the joined text (JSON-decoded when possible)."""
    if not isinstance(result, dict):
        return result
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
    text = "\n".join(texts).strip()
    try:
        return json.loads(text)
    except ValueError:
        return text


def call(tool: str, args: dict | None = None, *, cache_hours: float | None = None, purpose: str = "") -> Any:
    """Run a vidIQ tool with credit guard + cache. Raises VidIQError / CreditCapReached."""
    args = args or {}
    cfg = settings_value()
    if not cfg["enabled"]:
        raise VidIQError("vidIQ is switched off in Settings")
    hours = float(cfg["cache_hours"] if cache_hours is None else cache_hours)
    k = _key(tool, args)
    if hours > 0:
        hit = _cache_get(k, hours)
        if hit is not None:
            return hit
    credits = _check_credits(tool)
    result = _call("tools/call", {"name": tool, "arguments": args})
    if isinstance(result, dict) and result.get("isError"):
        raise VidIQError(f"{tool}: {str(_content(result))[:200]}")
    out = _content(result)
    if credits:
        _charge(tool, credits)
    logbus.log("info", "vidiq_call", f"vidIQ {tool}" + (f" ({purpose})" if purpose else "") + f" — {credits} credits")
    if hours > 0:
        _cache_put(k, out)
    return out


def safe(tool: str, args: dict | None = None, **kw) -> Any:
    """call(), but None instead of an error — for optional enrichment."""
    try:
        return call(tool, args, **kw)
    except VidIQError as exc:
        logger.info("vidIQ %s skipped: %s", tool, exc)
        return None


def find_tool(*words: str) -> str | None:
    """First tool whose name contains every word (tool names are vidIQ's, discovered at runtime)."""
    try:
        names = [t.get("name", "") for t in tools()]
    except VidIQError:
        return None
    for n in names:
        if all(w in n.lower() for w in words):
            return n
    return None


# --- typed helpers (Scrapper's uses) ----------------------------------------------------------
def _embedded_json(text: Any) -> Any:
    """Some vidIQ tools answer in markdown with a 'JSON:' block; pull the JSON out."""
    if not isinstance(text, str):
        return text
    i = text.find("{")
    while i != -1:
        try:
            return json.loads(text[i:])
        except ValueError:
            j = text.rfind("}")
            try:
                return json.loads(text[i:j + 1])
            except ValueError:
                i = text.find("{", i + 1)
    return None


def keyword_metrics(keyword: str, *, related: bool = True, purpose: str = "", cache_hours: float = 72) -> dict[str, Any] | None:
    """Search demand for a phrase (5 credits, cached 3 days): volume/competition/overall 0–100,
    monthly searches, growth %, and related keywords (good tags). None if unavailable."""
    raw = safe("vidiq_keyword_research", {"mode": "research", "keyword": keyword[:120], "includeRelated": related,
                                          "country": "US", "topMarketsLimit": 1},
               cache_hours=cache_hours, purpose=purpose or f"demand: {keyword[:40]}")
    data = _embedded_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("seedKeyword"), dict):
        return None
    seed = data["seedKeyword"]
    rel = [r for r in data.get("relatedKeywords") or [] if isinstance(r, dict) and r.get("keyword")]
    return {"keyword": seed.get("keyword") or keyword, "volume": round(float(seed.get("volume") or 0), 1),
            "competition": round(float(seed.get("competition") or 0), 1), "overall": round(float(seed.get("overall") or 0), 1),
            "monthly_searches": int(seed.get("estimatedMonthlySearch") or 0),
            "growth_pct": round(float(seed.get("searchDemandGrowthPct") or 0), 1),
            "related": [{"keyword": r["keyword"], "overall": round(float(r.get("overall") or 0), 1),
                         "monthly_searches": int(r.get("estimatedMonthlySearch") or 0)}
                        for r in sorted(rel, key=lambda r: -(r.get("overall") or 0))[:20]]}


def breakout_videos(keyword: str, *, purpose: str = "") -> list[dict[str, Any]]:
    """Long-form videos far outperforming their channel this month (5 credits, cached 1 day)."""
    raw = safe("vidiq_outliers", {"keyword": keyword, "contentType": "long", "publishedWithin": "thisMonth",
                                  "language": "en", "sort": "breakoutScore", "limit": 25},
               cache_hours=24, purpose=purpose or f"breakouts: {keyword}")
    vids = (raw or {}).get("videos") if isinstance(raw, dict) else None
    return [{"title": v.get("videoTitle"), "views": v.get("viewCount"), "breakout": v.get("breakoutScore"),
             "channel": v.get("channelTitle"), "id": v.get("videoId")} for v in vids or [] if v.get("videoTitle")]


def trending(query: str, *, purpose: str = "") -> list[dict[str, Any]]:
    """Long-form videos gaining views fastest right now (5 credits, cached 1 day)."""
    raw = safe("vidiq_trending_videos", {"videoFormat": "long", "titleQuery": query, "videoTitleLanguage": "en",
                                         "sortBy": "vph", "limit": 25}, cache_hours=24, purpose=purpose or f"trending: {query}")
    vids = (raw or {}).get("videos") if isinstance(raw, dict) else None
    return [{"title": v.get("videoTitle"), "views": v.get("viewCount"), "vph": round(float(v.get("vph") or 0)),
             "channel": v.get("channelTitle"), "id": v.get("videoId")} for v in vids or [] if v.get("videoTitle")]


# Film/TV categories vidIQ's "rising" mode accepts (from availableTopics, 2026-10-13).
RISING_TOPICS = ["trailers", "thrillers", "action thrillers", "science fiction", "mysteries", "korean dramas",
                 "romantic comedies", "anime", "comic books", "dramedy"]


def rising(topic: str, *, purpose: str = "") -> list[dict[str, Any]]:
    """Searches climbing fastest this week in a category (5 credits, cached 1 day)."""
    raw = safe("vidiq_keyword_research", {"mode": "rising", "topic": topic, "period": "week", "language": "en", "limit": 25},
               cache_hours=24, purpose=purpose or f"rising: {topic}")
    data = _embedded_json(raw)
    rows = (data or {}).get("risingKeywords") if isinstance(data, dict) else None
    return [{"keyword": r["keyword"], "monthly_searches": int(r.get("estimatedMonthlySearch") or 0),
             "change_pct": round(float(r.get("periodPercentChange") or 0))} for r in rows or [] if r.get("keyword")]


def questions(keyword: str, *, purpose: str = "") -> list[dict[str, Any]]:
    """What people ask about a subject, by search volume (5 credits, cached 7 days)."""
    raw = safe("vidiq_keyword_research", {"mode": "questions", "keyword": keyword[:120], "limit": 15, "sort": "volume",
                                          "language": "en"}, cache_hours=168, purpose=purpose or f"questions: {keyword[:40]}")
    data = _embedded_json(raw)
    rows = (data or {}).get("questions") if isinstance(data, dict) else None
    return [{"question": r["question"], "monthly_searches": int(r.get("estimatedMonthlySearch") or 0)}
            for r in rows or [] if r.get("question")]


def competitor_videos(query: str, *, purpose: str = "") -> list[dict[str, Any]]:
    """Top long-form YouTube videos already made for this query, by views (5 credits, cached 3 days)."""
    raw = safe("vidiq_youtube_search", {"query": query[:120], "type": ["video"], "order": "viewCount", "limit": 15,
                                        "videoDuration": "medium"}, cache_hours=72, purpose=purpose or f"competitors: {query[:40]}")
    rows = (raw or {}).get("results") if isinstance(raw, dict) else None
    return [{"title": r.get("title"), "views": r.get("viewCount"), "channel": r.get("channelTitle"),
             "published": (r.get("publishedAt") or "")[:10], "id": r.get("id")}
            for r in rows or [] if r.get("title") and "#shorts" not in (r.get("title") or "").lower()]


def score_title(title: str, *, purpose: str = "") -> dict[str, Any] | None:
    """Click-through score 0–100 for a long-form title (5 credits, cached 3 days)."""
    raw = safe("vidiq_score_title", {"title": title[:100], "type": "long"}, cache_hours=72,
               purpose=purpose or "title score")
    data = raw if isinstance(raw, dict) else _embedded_json(raw)
    return data if isinstance(data, dict) else ({"text": raw} if raw else None)


def status() -> dict[str, Any]:
    cfg = settings_value()
    d: dict[str, Any] = {"configured": configured(), "settings": cfg, "usage": usage(),
                         "last_error": _state["last_error"],
                         "backing_off_until": _state["down_until"] if time.time() < _state["down_until"] else None}
    if configured() and cfg["enabled"]:
        try:
            d["tools"] = [{"name": t.get("name"), "description": (t.get("description") or "")[:200],
                           "credits": cost_of(t.get("name", ""))} for t in tools()]
            d["connected"] = True
        except VidIQError as exc:
            d["connected"] = False
            d["error"] = str(exc)
    return d


# --- API ------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/vidiq", dependencies=[Depends(require_token)])


class VidIQSettingsIn(BaseModel):
    enabled: bool | None = None
    daily_credits: int | None = None
    monthly_credits: int | None = None
    cache_hours: float | None = None


@router.get("/status")
def get_status() -> dict[str, Any]:
    return status()


@router.get("/tools")
def get_tools(refresh: bool = False) -> list[dict[str, Any]]:
    """Full tool list with input schemas (free) — what Scrapper can ask vidIQ."""
    try:
        return tools(refresh=refresh)
    except VidIQError as exc:
        raise HTTPException(502, str(exc))


@router.put("/settings")
def put_settings(req: VidIQSettingsIn) -> dict[str, Any]:
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    for k in ("daily_credits", "monthly_credits"):
        if k in patch and not 0 <= int(patch[k]) <= 5000:
            raise HTTPException(400, f"{k} must be 0–5000")
    with SessionLocal() as s:
        row = s.get(AppSetting, "vidiq_settings")
        val = {**DEFAULTS, **((row.value or {}) if row else {}), **patch}
        if row:
            row.value = val
        else:
            s.add(AppSetting(key="vidiq_settings", value=val))
        s.commit()
    logbus.log("info", "vidiq_settings", f"vidIQ settings: {patch}")
    return status()


class CallIn(BaseModel):
    tool: str
    args: dict[str, Any] = {}
    cache_hours: float | None = None


@router.post("/call")
def run_tool(req: CallIn) -> dict[str, Any]:
    """Run one vidIQ tool (credit-capped, cached, logged) — for checks and research."""
    if not req.tool.startswith("vidiq_"):
        raise HTTPException(400, "unknown tool")
    try:
        return {"tool": req.tool, "credits": cost_of(req.tool),
                "result": call(req.tool, req.args, cache_hours=req.cache_hours, purpose="manual")}
    except CreditCapReached as exc:
        raise HTTPException(429, str(exc))
    except VidIQError as exc:
        raise HTTPException(502, str(exc))
