"""Cost tracker: every paid call is recorded with its usage and estimated cost.

Prices (official pages, read 2026-10-02 — editable in Settings, kept in
app_settings "costs"):
- Gemini 3.8 / 3.7 Flash: $0.75 in / $3.75 out per 1M tokens through
  2026-12-31, then $1.50 / $7.50 (ai.google.dev/gemini-api/docs/pricing).
  Thinking tokens bill as output. Gemini 3.5 Flash-Lite: $0.30 / $2.50.
- Grounding with Google Search: 5,000 requests/month free (shared across
  Gemini 3.x), then $14 per 1,000.
- Google Text-to-Speech Chirp 3 HD: 1M characters/month free, then $30 per 1M
  (cloud.google.com/text-to-speech/pricing). Cached sentences cost nothing.
- Upload-Post: flat subscription (Free $0 = 10 uploads/month; paid plans
  unlimited) — uploads are counted, the plan fee is a fixed monthly cost.
- TMDB, IMDb, Google Drive: free (calls are counted, $0).

Budget: a monthly budget (variable + fixed costs). With hard stop on, paid
calls are refused once it is reached (BudgetExceeded) instead of overspending.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime
import logging
from typing import Any, Iterator

from sqlalchemy import func

from .db import SessionLocal
from .models import AppSetting, UsageEvent

logger = logging.getLogger("scrapper.costs")

UTC = datetime.timezone.utc

DEFAULTS: dict[str, Any] = {
    "budget_usd": 0.0,             # 0 = no budget set
    "hard_stop": False,
    "upload_post_plan": "Basic",   # Free | Basic | Professional | Advanced | Business
    "fixed_costs": [],             # [{"name": "Railway", "usd": 5}]
    "gemini": {                    # $ per 1M tokens
        "gemini-3.8-flash": {"in": 0.75, "out": 3.75, "in_2027": 1.50, "out_2027": 7.50},
        "gemini-3.7-flash": {"in": 0.75, "out": 3.75, "in_2027": 1.50, "out_2027": 7.50},
        "gemini-3.5-flash-lite": {"in": 0.30, "out": 2.50},
        "gemini-3.5-flash": {"in": 1.50, "out": 9.00},
    },
    "grounding_free_per_month": 5000,
    "grounding_usd_per_1000": 14.0,
    "tts_free_chars_per_month": 1_000_000,
    "tts_usd_per_million_chars": 30.0,
}
UPLOAD_POST_PLANS = {"Free": (0, 10), "Basic": (24, None), "Professional": (50, None),
                     "Advanced": (147, None), "Business": (438, None)}  # (usd/month, uploads/month)

_op: contextvars.ContextVar[tuple[str, str | None]] = contextvars.ContextVar("cost_op", default=("other", None))


class BudgetExceeded(Exception):
    pass


@contextlib.contextmanager
def operation(name: str, ref: str | None = None) -> Iterator[None]:
    """Label the paid calls made inside (e.g. "studio:script", ref "studio:1")."""
    tok = _op.set((name, ref))
    try:
        yield
    finally:
        _op.reset(tok)


def settings_dict(s=None) -> dict[str, Any]:
    own = s is None
    s = s or SessionLocal()
    try:
        row = s.get(AppSetting, "costs")
        merged = {**DEFAULTS, **((row.value or {}) if row else {})}
        merged["gemini"] = {**DEFAULTS["gemini"], **((row.value or {}).get("gemini", {}) if row else {})}
        return merged
    finally:
        if own:
            s.close()


def month_key(t: datetime.datetime | None = None) -> str:
    return (t or datetime.datetime.now(UTC)).strftime("%Y-%m")


def _month_sum(s, column, service: str, month: str) -> float:
    return float(s.query(func.coalesce(func.sum(column), 0)).filter(
        UsageEvent.service == service, UsageEvent.month == month).scalar() or 0)


def fixed_monthly(cfg: dict) -> float:
    plan_fee = UPLOAD_POST_PLANS.get(cfg.get("upload_post_plan", "Basic"), (0, None))[0]
    return float(plan_fee) + sum(float(x.get("usd") or 0) for x in cfg.get("fixed_costs", []))


def month_spend(s, month: str | None = None) -> float:
    month = month or month_key()
    return float(s.query(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).filter(UsageEvent.month == month).scalar() or 0)


def check_budget() -> None:
    """Call before a paid request. Raises BudgetExceeded when the hard stop is on
    and this month's spend (variable + fixed) has reached the budget."""
    with SessionLocal() as s:
        cfg = settings_dict(s)
        if not cfg.get("hard_stop") or not cfg.get("budget_usd"):
            return
        total = month_spend(s) + fixed_monthly(cfg)
        if total >= float(cfg["budget_usd"]):
            raise BudgetExceeded(
                f"Monthly budget reached (${total:.2f} of ${float(cfg['budget_usd']):.2f}). "
                "Raise the budget or turn off the hard stop in Settings → Spending.")


def _record(service: str, cost: float, **fields) -> None:
    op, ref = _op.get()
    try:
        with SessionLocal() as s:
            s.add(UsageEvent(service=service, operation=fields.pop("operation", None) or op,
                             ref=fields.pop("ref", None) or ref, cost_usd=round(cost, 6),
                             month=month_key(), **fields))
            s.commit()
    except Exception as exc:  # never let accounting break the work itself
        logger.error("cost record failed: %s", exc)


def gemini_price(model: str, cfg: dict, now: datetime.datetime | None = None) -> tuple[float, float]:
    p = cfg["gemini"].get(model) or cfg["gemini"].get("gemini-3.8-flash")
    if (now or datetime.datetime.now(UTC)).year >= 2027 and "in_2027" in p:
        return p["in_2027"], p["out_2027"]
    return p["in"], p["out"]


def record_gemini(model: str, usage: dict, grounded: bool = False) -> float:
    inp = int(usage.get("promptTokenCount") or 0)
    out = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    with SessionLocal() as s:
        cfg = settings_dict(s)
        pin, pout = gemini_price(model, cfg)
        cost = inp / 1e6 * pin + out / 1e6 * pout
        if grounded:
            used = _month_sum(s, UsageEvent.requests, "gemini-search", month_key())
            if used >= cfg["grounding_free_per_month"]:
                cost += cfg["grounding_usd_per_1000"] / 1000
    _record("gemini", cost, model=model, input_tokens=inp, output_tokens=out, requests=1)
    if grounded:
        _record("gemini-search", 0.0, model=model, requests=1)
    return cost


def record_tts(chars: int, voice: str) -> float:
    with SessionLocal() as s:
        cfg = settings_dict(s)
        used = _month_sum(s, UsageEvent.chars, "tts", month_key())
    free_left = max(0, cfg["tts_free_chars_per_month"] - used)
    billable = max(0, chars - free_left)
    cost = billable / 1e6 * cfg["tts_usd_per_million_chars"]
    _record("tts", cost, model=voice, chars=chars, requests=1)
    return cost


def record_free(service: str, requests: int = 1, model: str | None = None) -> None:
    """Free services (TMDB, IMDb, Drive) and flat-fee Upload-Post: counted, $0."""
    _record(service, 0.0, model=model, requests=requests)


def summary(month: str | None = None) -> dict[str, Any]:
    month = month or month_key()
    with SessionLocal() as s:
        cfg = settings_dict(s)
        q = s.query(UsageEvent).filter(UsageEvent.month == month)
        by_service: dict[str, dict] = {}
        by_op: dict[str, dict] = {}
        by_day: dict[str, float] = {}
        by_ref: dict[str, float] = {}
        for e in q.all():
            sv = by_service.setdefault(e.service, {"cost": 0.0, "requests": 0, "input_tokens": 0,
                                                    "output_tokens": 0, "chars": 0})
            sv["cost"] += e.cost_usd or 0
            sv["requests"] += e.requests or 0
            sv["input_tokens"] += e.input_tokens or 0
            sv["output_tokens"] += e.output_tokens or 0
            sv["chars"] += e.chars or 0
            op = by_op.setdefault(e.operation or "other", {"cost": 0.0, "requests": 0})
            op["cost"] += e.cost_usd or 0
            op["requests"] += e.requests or 0
            day = (e.created_at or datetime.datetime.now(UTC)).strftime("%Y-%m-%d")
            by_day[day] = by_day.get(day, 0) + (e.cost_usd or 0)
            if e.ref:
                by_ref[e.ref] = by_ref.get(e.ref, 0) + (e.cost_usd or 0)
        variable = sum(v["cost"] for v in by_service.values())
        fixed = fixed_monthly(cfg)
        plan_fee, plan_uploads = UPLOAD_POST_PLANS.get(cfg["upload_post_plan"], (0, None))
        uploads = by_service.get("upload-post", {}).get("requests", 0)
        budget = float(cfg.get("budget_usd") or 0)
        total = variable + fixed
        return {
            "month": month,
            "variable_usd": round(variable, 4),
            "fixed_usd": round(fixed, 2),
            "total_usd": round(total, 4),
            "budget_usd": budget,
            "budget_used_pct": round(total / budget * 100, 1) if budget else None,
            "hard_stop": bool(cfg.get("hard_stop")),
            "by_service": {k: {**v, "cost": round(v["cost"], 4)} for k, v in sorted(by_service.items())},
            "by_operation": sorted(({"operation": k, **{**v, "cost": round(v["cost"], 4)}} for k, v in by_op.items()),
                                   key=lambda x: -x["cost"])[:15],
            "by_day": [{"day": d, "cost": round(c, 4)} for d, c in sorted(by_day.items())],
            "by_ref": {k: round(v, 4) for k, v in by_ref.items()},
            "free_tiers": {
                "search_requests": {"used": by_service.get("gemini-search", {}).get("requests", 0),
                                    "free": cfg["grounding_free_per_month"]},
                "tts_chars": {"used": by_service.get("tts", {}).get("chars", 0),
                              "free": cfg["tts_free_chars_per_month"]},
                "uploads": {"used": uploads, "plan": cfg["upload_post_plan"], "limit": plan_uploads,
                            "plan_usd": plan_fee},
            },
            "settings": cfg,
        }


def ref_cost(ref: str) -> float:
    with SessionLocal() as s:
        return float(s.query(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).filter(UsageEvent.ref == ref).scalar() or 0)
