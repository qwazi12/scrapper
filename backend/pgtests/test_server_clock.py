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


@pytest.mark.parametrize('days',[-3650,3650])
def test_budget_periods_usage_receipts_credit_days_and_job_heartbeat_ignore_host_skew(monkeypatch,days):
    from backend.app import costs,budget_reservations as br,resume,vidiq
    from backend.app.models import AppSetting,UsageEvent,ResumableJob,BudgetReservation
    class Skewed(datetime.datetime):
        @classmethod
        def now(cls,tz=None):return datetime.datetime.now(tz)+datetime.timedelta(days=days)
    fake=SimpleNamespace(datetime=Skewed,timedelta=datetime.timedelta,timezone=datetime.timezone)
    monkeypatch.setattr(costs,'datetime',fake);monkeypatch.setattr(resume,'datetime',fake)
    monkeypatch.setattr(vidiq,'datetime',fake)
    with SessionLocal() as s:
        s.query(UsageEvent).delete()
        for key in ('costs','vidiq_ledger'):
            row=s.get(AppSetting,key)
            if row:s.delete(row)
        now=leases.clock(s)
        s.add(AppSetting(key='costs',value={'daily_usd':1,'upload_post_plan':'Free'}))
        s.add(UsageEvent(service='synthetic-clock',cost_usd=.8,month=costs.month_key(now),created_at=now));s.commit()
    with pytest.raises(costs.DailyCapReached):br.reserve('synthetic-clock',.3)
    with br.attempt('synthetic-clock',.1):costs._record('synthetic-clock',.1)
    with SessionLocal() as s:
        event=s.query(UsageEvent).order_by(UsageEvent.id.desc()).first()
        assert event.month==now.strftime('%Y-%m') and abs((event.created_at-now).total_seconds())<5
        reservation=s.query(BudgetReservation).one()
        assert abs((reservation.created_at-now).total_seconds())<5
        assert costs.today_spend(s)==pytest.approx(.9)
        assert costs.month_spend(s)==pytest.approx(.9)
        assert costs.daily_status(s)['spent_usd']==pytest.approx(.9)
        vidiq._update_ledger(s,3);s.commit()
        assert list(s.get(AppSetting,'vidiq_ledger').value['days'])==[costs.today_key(now)]
    rid=resume.begin('synthetic-clock','Shared clock test',{'case':days})
    resume.checkpoint(rid,stage='original')
    resume._heartbeat_once()
    with SessionLocal() as s:
        row=s.get(ResumableJob,rid)
        assert abs((row.created_at-now).total_seconds())<5
        assert abs((row.updated_at-now).total_seconds())<5
    resume.finish(rid,'done')
    with SessionLocal() as s:
        row=s.get(AppSetting,'costs');row.value={'daily_usd':0,'upload_post_plan':'Free','budget_usd':1,'hard_stop':True};s.commit()
    with pytest.raises(costs.BudgetExceeded):br.reserve('synthetic-clock',.2)


def test_future_host_cannot_reconcile_live_financial_owner_or_ignore_exclusive_file(monkeypatch,tmp_path):
    from fastapi import HTTPException
    from backend.app import financial_reconciliation as financial,delivery_archive, budget_reservations as br
    from backend.app.models import BudgetReservation,FileUse
    class Future(datetime.datetime):
        @classmethod
        def now(cls,tz=None):return datetime.datetime.now(tz)+datetime.timedelta(days=3650)
    monkeypatch.setattr(financial,'datetime',Future)
    monkeypatch.setattr(delivery_archive,'dt',SimpleNamespace(datetime=Future,timezone=datetime.timezone))
    with SessionLocal() as s:
        now=leases.clock(s)
        hold=BudgetReservation(id='clock-ownership',service='synthetic',operation='synthetic',reserved_micros=1,state='reserved',created_at=now)
        s.add(hold);leases.acquire(s,'budget:'+hold.id,'original')
        leases.acquire(s,'files:clock-exclusive','original')
        s.add(FileUse(resource='files:clock-exclusive',paths=[str(tmp_path.resolve())],exclusive=True));s.commit()
        assert financial.ownership(s,'usd',hold)==(True,True,False)
    with SessionLocal() as s:
        br.lock(s)
        with pytest.raises(HTTPException,match='409'):delivery_archive._readable(s,[tmp_path/'original.mp4'])
