import concurrent.futures
from datetime import datetime, timedelta, timezone

import pytest

from backend.app import costs, budget_reservations as br
from backend.app.db import SessionLocal
from backend.app.models import AppSetting, BudgetReservation, UsageEvent


@pytest.fixture(autouse=True)
def clean_budget():
    with SessionLocal() as s:
        s.query(BudgetReservation).delete(); s.query(UsageEvent).delete()
        row=s.get(AppSetting,'costs')
        if row: s.delete(row)
        s.add(AppSetting(key='costs',value={'daily_usd':1,'upload_post_plan':'Free'})); s.commit()
    yield
    with SessionLocal() as s:
        s.query(BudgetReservation).delete(); s.query(UsageEvent).delete()
        row=s.get(AppSetting,'costs')
        if row: s.delete(row)
        s.commit()


def test_concurrent_sessions_cannot_over_reserve_daily_cap():
    def claim(_):
        try: return br.reserve('fixture',.4)
        except costs.DailyCapReached: return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: ids=list(pool.map(claim,range(8)))
    assert len([x for x in ids if x])==2
    with SessionLocal() as s: assert br.held_micros(s)==800000


def test_timeout_hold_survives_restart_and_calendar_reset():
    with pytest.raises(TimeoutError):
        with br.attempt('fixture',.7): raise TimeoutError('receipt lost')
    with SessionLocal() as s:
        r=s.query(BudgetReservation).one(); assert r.state=='unknown'
        r.created_at=datetime.now(timezone.utc)-timedelta(days=45); s.commit()
    with pytest.raises(costs.DailyCapReached): br.reserve('fixture',.4)
    assert costs.daily_status()['held_usd']==.7


def test_usage_receipt_and_settlement_commit_together_and_cannot_repeat():
    with br.attempt('gemini',.5) as rid:
        costs.record_gemini('gemini-3.8-flash',{'promptTokenCount':100,'candidatesTokenCount':100})
        with pytest.raises(RuntimeError,match='already settled'):
            costs.record_gemini('gemini-3.8-flash',{'promptTokenCount':100,'candidatesTokenCount':100})
    with SessionLocal() as s:
        r=s.get(BudgetReservation,rid); assert r.state=='settled' and r.actual_micros>0
        assert s.query(UsageEvent).count()==1 and br.held_micros(s)==0


def test_unknown_receipt_keeps_hold_and_bound_breach_stops_later_requests():
    with br.attempt('gemini',.5): costs.record_gemini('gemini-3.8-flash',{})
    with SessionLocal() as s: assert s.query(BudgetReservation).one().state=='unknown'
    with br.attempt('gemini',.001): costs.record_gemini('gemini-3.8-flash',{'promptTokenCount':100000,'candidatesTokenCount':1})
    with pytest.raises(costs.BudgetExceeded,match='breach'): br.reserve('fixture',.01)


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1])
def test_nonfinite_or_negative_bounds_rejected(value):
    with pytest.raises(ValueError): br.reserve('fixture',value)


def test_unknown_pricing_audio_and_grounding_fail_closed():
    for service,body,model in [('gemini',{'generationConfig':{'maxOutputTokens':100}},'unknown'),
                              ('gemini-tts',{},'gemini-3.8-flash-tts'),
                              ('gemini',{'tools':[{}]},'gemini-3.8-flash')]:
        with pytest.raises(costs.BudgetExceeded): br.request_bound(service,body,model)


def test_explicit_receipt_reconciliation_is_audited_and_cannot_double_settle():
    from backend.app.models import BudgetResolution
    with br.attempt('fixture',.7) as rid: pass
    br.resolve(rid,.2,'provider-receipt-123','Manually checked the provider invoice for this exact request.')
    with SessionLocal() as s:
        assert s.get(BudgetReservation,rid).state=='settled'
        assert s.get(BudgetResolution,rid).actual_micros==200000
        assert costs.today_spend(s)==pytest.approx(.2)
    with pytest.raises(ValueError): br.resolve(rid,.2,'provider-receipt-123','Do not settle the receipt a second time.')


def test_budget_mutex_serializes_separate_python_processes():
    import subprocess
    import sys
    program='''from backend.app import budget_reservations as br, costs
try:
 print(br.reserve("process-fixture", .6))
except costs.DailyCapReached:
 print("DENIED")
'''
    children=[subprocess.Popen([sys.executable,'-c',program],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
    results=[p.communicate(timeout=20) for p in children]
    assert all(p.returncode==0 for p in children), results
    assert sum(out.strip()!='DENIED' for out,err in results)==1
    with SessionLocal() as s: assert br.held_micros(s)==600000


def test_concurrent_today_extras_are_added_without_lost_updates(monkeypatch):
    import threading
    barrier=threading.Barrier(6)
    original=br.lock
    def synchronized_lock(s):
        barrier.wait(timeout=10)
        return original(s)
    monkeypatch.setattr(br,'lock',synchronized_lock)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _:costs.add_today_extra(1),range(6)))
    assert costs.daily_status()['extra_today_usd']==6
