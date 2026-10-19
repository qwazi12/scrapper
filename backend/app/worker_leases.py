"""Cross-process leases. Generation tokens fence DB writes and paid dispatches."""
from __future__ import annotations
import contextlib
import contextvars
import datetime as dt
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import event, update, select, func
from sqlalchemy.orm import Session

from . import control
from .db import SessionLocal
from .models import WorkerLease

TTL = 75
UTC = dt.timezone.utc
_current = contextvars.ContextVar("worker_lease", default=None)


class LeaseLost(control.Cancelled, RuntimeError):
    pass


@dataclass(frozen=True)
class Handle:
    resource: str
    owner: str
    generation: int


def resource(kind, params):
    if kind == "channel_archive": return "archive:" + params["channel_id"]
    if kind in ("studio","drive_save","studio_restore"): return f"{kind}:{params['project_id']}"
    return kind + ':' + hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def clock(s):
    """Use the shared database clock for distributed ownership, after lock waits."""
    if s.get_bind().dialect.name == "postgresql":
        return s.scalar(select(func.clock_timestamp()))
    return dt.datetime.now(UTC)


def instant(s):
    # Evaluate predicates at execution time, including after row-lock waits.
    return func.clock_timestamp() if s.get_bind().dialect.name == "postgresql" else clock(s)


def acquire(s, key, owner, ttl=TTL):
    from .budget_reservations import lock
    lock(s)  # common short transaction ordering with budget dispatch/settlement
    now = instant(s)
    dialect = s.get_bind().dialect.name
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    s.execute(insert(WorkerLease).values(resource=key, owner=None, generation=0, expires_at=now)
              .on_conflict_do_nothing(index_elements=['resource']))
    won = s.execute(update(WorkerLease).where(WorkerLease.resource == key,
        WorkerLease.expires_at <= now).values(owner=owner, generation=WorkerLease.generation + 1,
                                              expires_at=now + dt.timedelta(seconds=ttl))).rowcount
    if won != 1: raise LeaseLost(f"Work is already leased: {key}")
    row = s.get(WorkerLease, key, populate_existing=True)
    return Handle(key, owner, row.generation)


def guard(s=None, handle=None):
    h = handle or _current.get()
    if not h: return
    if s is None:
        with SessionLocal() as own:
            guard(own, h); own.commit(); return
    now = instant(s)
    # Conditional UPDATE holds the lease row until the content transaction commits.
    n = s.execute(update(WorkerLease).where(WorkerLease.resource == h.resource, WorkerLease.owner == h.owner,
        WorkerLease.generation == h.generation, WorkerLease.expires_at > now)
        .values(generation=WorkerLease.generation)).rowcount
    if n != 1: raise LeaseLost(f"Worker lease expired or superseded: {h.resource} generation {h.generation}")


def renew(s, h, ttl=TTL):
    now=instant(s)
    return s.execute(update(WorkerLease).where(WorkerLease.resource == h.resource, WorkerLease.owner == h.owner,
        WorkerLease.generation == h.generation, WorkerLease.expires_at > now)
        .values(expires_at=now+dt.timedelta(seconds=ttl))).rowcount == 1


def release(s, h):
    s.execute(update(WorkerLease).where(WorkerLease.resource == h.resource, WorkerLease.owner == h.owner,
        WorkerLease.generation == h.generation).values(owner=None, expires_at=instant(s)))


@contextlib.contextmanager
def bind(h):
    token=_current.set(h)
    try: yield
    finally: _current.reset(token)


def current(): return _current.get()


@event.listens_for(Session, 'before_flush')
def fence_content_writes(s, flush_context, instances):
    # Usage must still be recorded if a response arrives after lease loss.
    exempt={'UsageEvent','BudgetMutex','BudgetReservation','BudgetReceipt','FinancialResolution','BudgetResolution','FileUse','WorkerLease','ResumableJob','CreditReservation','LogEntry'}
    if any(type(o).__name__ not in exempt for o in (*s.new,*s.dirty,*s.deleted)):
        guard(s)
        from .file_use import assert_current
        assert_current(s)


def revoke(s, key):
    """Owner stop fences every old process immediately, including its helpers."""
    s.execute(update(WorkerLease).where(WorkerLease.resource==key).values(
        owner=None, generation=WorkerLease.generation+1, expires_at=instant(s)))


@event.listens_for(Session, 'do_orm_execute')
def fence_bulk_content_writes(state):
    if not (state.is_update or state.is_delete or state.is_insert): return
    name = getattr(getattr(state.statement,'table',None),'name',None)
    if name not in {'worker_leases','resumable_jobs','budget_mutex','budget_reservations','budget_receipts','financial_resolutions','budget_resolutions','file_uses','usage_events','credit_reservations','log_entries'}:
        guard(state.session)
        from .file_use import assert_current
        assert_current(state.session)


@contextlib.contextmanager
def keepalive(h):
    """Renew only while this work invocation is alive, including web-only mode."""
    import threading
    stopped=threading.Event()
    def beat():
        while not stopped.wait(20):
            try:
                with SessionLocal() as s:
                    alive=renew(s,h); s.commit()
                if not alive: return
            except Exception:
                # DB writes/paid dispatch still fail closed if renewal cannot recover.
                pass
    thread=threading.Thread(target=beat,daemon=True,name='lease-renew-'+h.resource)
    thread.start()
    try: yield
    finally:
        stopped.set(); thread.join(timeout=1)


@contextlib.contextmanager
def scope(key):
    from .resume import BOOT
    with SessionLocal() as s:
        h=acquire(s,key,BOOT); s.commit()
    try:
        with bind(h), keepalive(h): yield h
    finally:
        with SessionLocal() as s:
            release(s,h); s.commit()


def live(key):
    with SessionLocal() as s:
        row=s.get(WorkerLease,key)
        if not row: return False
        expiry=row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
        return expiry>clock(s)
