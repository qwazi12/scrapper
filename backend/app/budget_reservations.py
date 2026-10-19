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
        asof=costs.clock(s)
        cfg = costs.settings_dict(s)
        if s.query(BudgetReservation).filter_by(state="breach").first():
            raise costs.BudgetExceeded("Unresolved request budget breach")
        holds = held_micros(s)
        monthly = micros(costs.month_spend(s,costs.month_key(asof)) + costs.fixed_monthly(cfg))
        if cfg.get("hard_stop") and cfg.get("budget_usd") and monthly + holds + amount > micros(cfg["budget_usd"]):
            raise costs.BudgetExceeded("Monthly budget cannot cover this attempt and unsettled holds")
        cap = costs.effective_cap(cfg,asof)
        if cap and micros(costs.today_spend(s,asof)) + holds + amount > micros(cap):
            raise costs.DailyCapReached("Daily cap cannot cover this attempt and unsettled holds; unknown charges remain reserved")
        op, ref = costs._op.get()
        row = BudgetReservation(id=str(uuid.uuid4()), service=service, operation=op, ref=ref,
                                reserved_micros=amount, state="reserved",created_at=asof)
        s.add(row)
        from .worker_leases import acquire
        from .resume import BOOT
        acquire(s,'budget:'+row.id,BOOT)
        s.commit(); return row.id


def transition(rid, before, after):
    with SessionLocal() as s:
        lock(s)
        n = s.execute(update(BudgetReservation).where(BudgetReservation.id == rid,
            BudgetReservation.state.in_(before)).values(state=after)).rowcount
        s.commit(); return n == 1


@contextlib.contextmanager
def attempt(service: str, upper_usd):
    from . import resume, worker_leases as leases
    from .models import WorkerLease
    if _active.get(): raise RuntimeError("Nested paid attempts must reserve independently")
    rid = reserve(service, upper_usd)
    with SessionLocal() as s:
        row=s.get(WorkerLease,'budget:'+rid)
        handle=leases.Handle(row.resource,row.owner,row.generation)
    try:
        with leases.keepalive(handle):
            try:
                resume.assert_current_lease()
            except BaseException:
                transition(rid, ("reserved",), "released"); raise
            with SessionLocal() as s:
                lock(s); leases.guard(s,handle)
                if s.get(BudgetReservation,rid).state!='reserved':
                    raise RuntimeError('Budget hold changed before dispatch')
                s.get(BudgetReservation,rid).state='dispatched';s.commit()
            token = _active.set(rid)
            try:
                yield rid
            finally:
                _active.reset(token)
                transition(rid, ("dispatched",), "unknown")
    finally:
        with SessionLocal() as s:
            leases.release(s,handle);s.commit()


def settle_record(s, event):
    """Keep a configured usage estimate separate from owner-attested billing."""
    from .models import BudgetReceipt, BudgetResolution
    rid = _active.get()
    if not rid: return
    row = s.get(BudgetReservation, rid)
    if not row or s.get(BudgetReceipt,rid):
        raise RuntimeError("Budget receipt already settled or missing")
    amount=micros(event.cost_usd)
    if row.state not in ('dispatched','unknown'):
        if row.state!='settled' or not s.get(BudgetResolution,rid):
            raise RuntimeError("Budget receipt already settled or missing")
        # Usage multiplied by configured rates is an estimate, not an invoice.
        # Preserve the owner's billed amount and the late estimate independently.
        previous=row.actual_micros or 0
        event.cost_usd=0.0
        event.cost_basis='estimate_after_attestation'
        row.state='breach' if amount!=previous else 'settled'
    else:
        row.state='breach' if amount>row.reserved_micros else 'settled'
        row.actual_micros=amount
    s.add(BudgetReceipt(reservation_id=rid,amount_micros=amount,usage=dict(service=event.service,
        model=event.model,input_tokens=event.input_tokens,output_tokens=event.output_tokens,
        chars=event.chars,requests=event.requests,cost_basis="configured_usage_estimate")))


def request_bound(service: str, body: dict, model: str | None = None) -> float:
    """Known text token bound or explicit operator-supplied provider contract.

    Unknown models, image/audio accounting and grounding fan-out require a
    declared upper USD bound in costs.request_bounds. No free-tier credit is
    deducted from an estimate while concurrent attempts may consume it.
    """
    from . import costs
    with SessionLocal() as s:
        cfg = costs.settings_dict(s)
        price_at=costs.clock(s)
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
    pin, pout = costs.gemini_price(model, cfg,price_at)
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


def resolve(rid, actual_usd, receipt, rationale, *, confirm_interrupted=False, expected_version=None, request_key=None):
    """Explicit provider evidence; lease expiry alone never releases a hold."""
    from . import costs
    from .models import BudgetResolution, UsageEvent
    from .financial_reconciliation import require_resolution,save_decision,duplicate
    amount=micros(actual_usd)
    with SessionLocal() as s:
        lock(s)
        if duplicate(s,request_key,'usd',rid,amount,receipt,rationale):return
        row=s.get(BudgetReservation,rid)
        require_resolution(s,'usd',row,confirm_interrupted,expected_version)
        save_decision(s,'usd',rid,amount,receipt,rationale,request_key)
        previous=row.actual_micros or 0
        s.add(UsageEvent(service=row.service,operation=row.operation,ref=row.ref,
                         month=costs.month_key(costs.clock(s)),created_at=costs.clock(s),cost_usd=(amount-previous)/1e6,requests=0,
                         cost_basis='owner_attested_adjustment',reservation_id=rid))
        if not s.get(BudgetResolution,rid):
            s.add(BudgetResolution(reservation_id=rid,actual_micros=amount,receipt=receipt,rationale=rationale))
        row.actual_micros,row.state=amount,'settled'
        s.commit()
