"""Durable integer budget holds, serialized across processes and DB sessions.

Uncertain dispatched calls retain their full hold across day/month boundaries.
These enforce declared request bounds against locally tracked estimated costs;
provider invoices and incorrectly declared price/usage bounds remain external.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import uuid
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import func, update

from .db import SessionLocal
from .models import BudgetMutex, BudgetReservation

_active = contextvars.ContextVar("budget_reservation", default=None)
HELD = ("reserved", "dispatched", "unknown")


def micros(usd):
    value = Decimal(str(usd))
    if not value.is_finite() or value < 0 or value > 1_000_000:
        raise ValueError("Cost bound must be finite, non-negative and at most $1,000,000")
    return int((value * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


def lock(s):
    # Upsert plus UPDATE serializes SQLite writers and takes a row lock on PG.
    dialect = s.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError("Atomic budget reservations require SQLite or PostgreSQL")
    s.execute(insert(BudgetMutex).values(id=1, revision=0).on_conflict_do_nothing(index_elements=["id"]))
    s.execute(update(BudgetMutex).where(BudgetMutex.id == 1).values(revision=BudgetMutex.revision + 1))


def held_micros(s):
    return int(s.query(func.coalesce(func.sum(BudgetReservation.reserved_micros), 0)).filter(
        BudgetReservation.state.in_(HELD)).scalar())


def reserve(service: str, upper_usd) -> str:
    from . import costs, resume
    resume.assert_current_lease()
    amount = micros(upper_usd)
    with SessionLocal() as s:
        lock(s)
        cfg = costs.settings_dict(s)
        if s.query(BudgetReservation).filter_by(state="breach").first():
            raise costs.BudgetExceeded("Unresolved request budget breach")
        holds = held_micros(s)
        monthly = micros(costs.month_spend(s) + costs.fixed_monthly(cfg))
        if cfg.get("hard_stop") and cfg.get("budget_usd") and monthly + holds + amount > micros(cfg["budget_usd"]):
            raise costs.BudgetExceeded("Monthly budget cannot cover this attempt and unsettled holds")
        cap = costs.effective_cap(cfg)
        if cap and micros(costs.today_spend(s)) + holds + amount > micros(cap):
            raise costs.DailyCapReached("Daily cap cannot cover this attempt and unsettled holds; unknown charges remain reserved")
        op, ref = costs._op.get()
        row = BudgetReservation(id=str(uuid.uuid4()), service=service, operation=op, ref=ref,
                                reserved_micros=amount, state="reserved")
        s.add(row); s.commit(); return row.id


def transition(rid, before, after):
    with SessionLocal() as s:
        lock(s)
        n = s.execute(update(BudgetReservation).where(BudgetReservation.id == rid,
            BudgetReservation.state.in_(before)).values(state=after)).rowcount
        s.commit(); return n == 1


@contextlib.contextmanager
def attempt(service: str, upper_usd):
    from . import resume
    if _active.get(): raise RuntimeError("Nested paid attempts must reserve independently")
    rid = reserve(service, upper_usd)
    try:
        resume.assert_current_lease()
    except BaseException:
        transition(rid, ("reserved",), "released"); raise
    transition(rid, ("reserved",), "dispatched")
    token = _active.set(rid)
    try:
        yield rid
    finally:
        _active.reset(token)
        # Success without a durable usage receipt is also uncertain.
        transition(rid, ("dispatched",), "unknown")


def settle_record(s, event):
    """Called in the SAME transaction as the UsageEvent insert; never swallow failure."""
    rid = _active.get()
    if not rid: return
    row = s.get(BudgetReservation, rid)
    if not row or row.state not in ("dispatched", "unknown"):
        raise RuntimeError("Budget receipt already settled or missing")
    row.actual_micros = micros(event.cost_usd)
    # Actual usage exceeding the declared bound is recorded, but remaining
    # requests fail closed until this breached contract is investigated.
    row.state = "breach" if row.actual_micros > row.reserved_micros else "settled"


def request_bound(service: str, body: dict, model: str | None = None) -> float:
    """Known text token bound or explicit operator-supplied provider contract.

    Unknown models, image/audio accounting and grounding fan-out require a
    declared upper USD bound in costs.request_bounds. No free-tier credit is
    deducted from an estimate while concurrent attempts may consume it.
    """
    from . import costs
    cfg = costs.settings_dict()
    declared = (cfg.get("request_bounds") or {}).get(service + (":" + model if model else ""))
    if declared is not None:
        value = micros(declared)
        if value <= 0: raise costs.BudgetExceeded("Declared paid request bound must be positive")
        return value / 1e6
    encoded = json.dumps(body, ensure_ascii=False)
    generation = body.get("generationConfig") or {}
    output = generation.get("maxOutputTokens")
    if (service != "gemini" or model not in cfg["gemini"] or 'inline_data' in encoded
            or 'inlineData' in encoded or body.get("tools") or type(output) is not int or not 1 <= output <= 65536):
        raise costs.BudgetExceeded(f"No bounded cost contract for {service}:{model or 'default'}; configure costs.request_bounds before paid calls")
    pin, pout = costs.gemini_price(model, cfg)
    # UTF-8 bytes plus JSON overhead safely overcounts ordinary text tokens.
    return micros(len(encoded.encode()) / 1e6 * pin + output / 1e6 * pout) / 1e6


async def post_json_async(client, url, *, body, model, service="gemini", **kwargs):
    from . import costs
    with attempt(service, request_bound(service, body, model)):
        res = await client.post(url, json=body, **kwargs)
        if res.is_success:
            data = res.json()
            if service == "gemini": costs.record_gemini(model, data.get("usageMetadata") or {}, grounded="tools" in body)
            else: costs.record_gemini_tts(model, data.get("usageMetadata") or data.get("usage") or {}, 0, 0)
        return res


def resolve(rid, actual_usd, receipt, rationale):
    """Explicit receipt reconciliation; no automatic expiry or midnight release."""
    from . import costs
    from .models import BudgetResolution, UsageEvent
    amount=micros(actual_usd)
    with SessionLocal() as s:
        lock(s)
        row=s.get(BudgetReservation,rid)
        if not row or row.state not in ("unknown","breach") or s.get(BudgetResolution,rid):
            raise ValueError("Reservation is not awaiting reconciliation or was already resolved")
        if len(receipt.strip()) < 5 or len(rationale.strip()) < 20:
            raise ValueError("Provide a provider receipt reference and a substantive reconciliation rationale")
        previous=row.actual_micros or 0
        # Breaches already recorded their charge. Unknown attempts record zero
        # until a complete usage receipt or this explicit reconciliation.
        adjustment=amount-previous
        s.add(UsageEvent(service=row.service,operation=row.operation,ref=row.ref,
                         month=costs.month_key(),cost_usd=adjustment/1e6,requests=0))
        s.add(BudgetResolution(reservation_id=rid,actual_micros=amount,receipt=receipt,rationale=rationale))
        row.actual_micros,row.state=amount,"settled"
        s.commit()
