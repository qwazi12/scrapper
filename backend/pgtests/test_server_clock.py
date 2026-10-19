"""Skewed application hosts cannot steal or extend distributed ownership."""
from types import SimpleNamespace
import datetime

import pytest
from sqlalchemy import text

from backend.app import worker_leases as leases
from backend.app.db import SessionLocal
from backend.app.models import WorkerLease


@pytest.mark.parametrize('days', [-3650, 3650])
def test_lease_predicates_ignore_host_clock(monkeypatch, days):
    class Skewed(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime.now(tz) + datetime.timedelta(days=days)
    monkeypatch.setattr(leases, 'dt', SimpleNamespace(datetime=Skewed, timedelta=datetime.timedelta))
    with SessionLocal() as s:
        original=leases.acquire(s,'clock-fixture','first',ttl=60);s.commit()
        skew=abs((s.get(WorkerLease,'clock-fixture').expires_at-leases.clock(s)).total_seconds()-60)
        assert skew<2
    with pytest.raises(leases.LeaseLost):
        with SessionLocal() as s: leases.acquire(s,'clock-fixture','second')
    with SessionLocal() as s:
        leases.guard(s,original)
        assert leases.renew(s,original)
        s.commit()
    with SessionLocal() as s:
        s.execute(text("UPDATE worker_leases SET expires_at=clock_timestamp()-interval '1 second' WHERE resource='clock-fixture'"));s.commit()
    with SessionLocal() as s:
        assert not leases.renew(s,original)
    with pytest.raises(leases.LeaseLost): leases.guard(handle=original)
    with SessionLocal() as s:
        new=leases.acquire(s,'clock-fixture','next');s.commit()
        assert new.generation==original.generation+1
