"""Shared file readers and exclusive file mutations, serialized across workers.

Paths include descendants: pinning a directory protects everything under it.
Claims expire only after a dead worker stops renewing. Ownership is rechecked
before each controlled subprocess/chunk and before destructive mutations.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete

from . import worker_leases as leases
from .db import SessionLocal
from .models import FileUse, WorkerLease

_active=ContextVar('file_uses',default=())


class FileBusy(HTTPException):
    def __init__(self):super().__init__(409,'Media is in use by another render, transfer or cleanup; retry when it finishes')


def overlap(a,b):
    a,b=Path(a),Path(b)
    return a==b or a.is_relative_to(b) or b.is_relative_to(a)


def assert_current(s=None):
    for handle in _active.get():leases.guard(s,handle)


@contextmanager
def suspend():
    """Receipt accounting and claim cleanup must survive content lease loss."""
    token=_active.set(())
    try:yield
    finally:_active.reset(token)


@contextmanager
def pin(paths,*,exclusive=False,session=None):
    normalized=sorted({str(Path(p).resolve()) for p in paths if p})
    if not normalized:
        yield None;return
    from .budget_reservations import lock
    from .resume import BOOT
    key='files:'+uuid4().hex
    with SessionLocal() as s:
        lock(s)
        now=datetime.now(timezone.utc)
        expired=[r.resource for r in s.query(FileUse).outerjoin(WorkerLease,WorkerLease.resource==FileUse.resource)
                 .filter((WorkerLease.expires_at<=now)|(WorkerLease.resource.is_(None)))]
        if expired:
            s.execute(delete(FileUse).where(FileUse.resource.in_(expired)))
            s.execute(delete(WorkerLease).where(WorkerLease.resource.in_(expired)))
        mine={h.resource for h in _active.get()}
        for use in s.query(FileUse).join(WorkerLease,WorkerLease.resource==FileUse.resource).filter(WorkerLease.expires_at>now):
            if use.resource in mine:continue
            if (exclusive or use.exclusive) and any(overlap(a,b) for a in normalized for b in use.paths):
                raise FileBusy()
        handle=leases.acquire(s,key,BOOT)
        s.add(FileUse(resource=key,paths=normalized,exclusive=exclusive));s.commit()
    token=_active.set((*_active.get(),handle))
    try:
        with leases.keepalive(handle):
            assert_current()
            yield handle
    except BaseException:
        if session is not None:session.rollback()
        raise
    finally:
        _active.reset(token)
        with leases.bind(None),SessionLocal() as s:
            s.execute(delete(FileUse).where(FileUse.resource==key))
            leases.release(s,handle)
            s.execute(delete(WorkerLease).where(WorkerLease.resource==key,WorkerLease.owner.is_(None)))
            s.commit()
