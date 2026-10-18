"""Interrupted and late billing evidence, entirely local with synthetic charges."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app import budget_reservations as br, costs, vidiq, worker_leases as leases
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import AppSetting, BudgetReceipt, BudgetReservation, CreditReservation, FinancialResolution, UsageEvent, WorkerLease


@pytest.fixture(autouse=True)
def clean_ledger():
    with SessionLocal() as s:
        s.query(UsageEvent).delete()
        for key in ('costs','vidiq_settings','vidiq_ledger'):
            row=s.get(AppSetting,key)
            if row:s.delete(row)
        s.add(AppSetting(key='costs',value={'daily_usd':100,'upload_post_plan':'Free'}));s.commit()
    yield
    with SessionLocal() as s:
        s.query(UsageEvent).delete()
        for key in ('costs','vidiq_settings','vidiq_ledger'):
            row=s.get(AppSetting,key)
            if row:s.delete(row)
        s.commit()


@pytest.fixture
def client():return TestClient(app,headers={'x-access-token':'test-token'})


def expire(key):
    with SessionLocal() as s:
        s.get(WorkerLease,key).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);s.commit()


def form(client,rid,kind='usd',actual=.2):
    hold=next(h for h in client.get('/api/financial/holds').json()['holds'] if h['id']==rid)
    return dict(request_key=str(uuid4()),reservation_id=rid,kind=kind,expected_version=hold['version'],actual=actual,
        receipt='synthetic-provider-receipt',rationale='I reviewed the exact request against the synthetic billing receipt.',reviewed=True,confirm_interrupted=False)


@pytest.mark.parametrize('phase',['reserved','dispatched'])
def test_orphan_hold_never_auto_releases_and_requires_interruption_review(client,phase):
    rid=br.reserve('fixture',.7)
    with SessionLocal() as s:s.get(BudgetReservation,rid).state=phase;s.commit()
    req=form(client,rid)
    assert client.post('/api/financial/resolve',json={**req,'confirm_interrupted':True}).status_code==409
    expire('budget:'+rid)
    for _ in range(2):
        hold=next(h for h in client.get('/api/financial/holds').json()['holds'] if h['id']==rid)
        assert hold['state']==phase and hold['orphan'] and hold['can_resolve']
        assert costs.daily_status()['held_usd']==.7
    assert client.post('/api/financial/resolve',json=req).status_code==409
    req['confirm_interrupted']=True
    assert client.post('/api/financial/resolve',json=req).status_code==200
    assert client.post('/api/financial/resolve',json=req).status_code==200
    assert client.post('/api/financial/resolve',json={**req,'actual':.3}).status_code==409
    assert costs.daily_status()['held_usd']==0
    with SessionLocal() as s:
        assert costs.today_spend(s)==pytest.approx(.2)
        assert s.query(FinancialResolution).filter_by(reservation_id=rid,kind='usd').count()==1


def test_late_usage_records_only_delta_and_flags_disagreement(client):
    with br.attempt('fixture',.7) as rid:pass
    req=form(client,rid)
    assert client.post('/api/financial/resolve',json=req).status_code==200
    token=br._active.set(rid)
    try:costs._record('fixture',.3,requests=1)
    finally:br._active.reset(token)
    with SessionLocal() as s:
        assert costs.today_spend(s)==pytest.approx(.3)
        assert s.get(BudgetReceipt,rid).amount_micros==300000
        assert s.get(BudgetReservation,rid).state=='breach'
    with pytest.raises(costs.BudgetExceeded,match='breach'):br.reserve('fixture',.01)
    # Replaying the old decision cannot clear the new breach.
    assert client.post('/api/financial/resolve',json=req).status_code==200
    with SessionLocal() as s:assert s.get(BudgetReservation,rid).state=='breach'
    second=form(client,rid,actual=.3)
    assert client.post('/api/financial/resolve',json=second).status_code==200
    with SessionLocal() as s:
        assert costs.today_spend(s)==pytest.approx(.3)
        assert s.query(FinancialResolution).filter_by(reservation_id=rid,kind='usd').count()==2
    token=br._active.set(rid)
    try:
        with pytest.raises(RuntimeError,match='already settled'):costs._record('fixture',.3,requests=1)
    finally:br._active.reset(token)


def test_credit_receipt_resolution_preserves_exact_owner_amount_on_late_estimate(client):
    rid=str(uuid4())
    with SessionLocal() as s:
        s.add(CreditReservation(id=rid,tool='vidiq_keyword_research',credits=5,state='dispatched'))
        leases.acquire(s,'credit:'+rid,'synthetic-worker');s.commit()
    req=form(client,rid,'credit',0);req['confirm_interrupted']=True
    assert client.post('/api/financial/resolve',json=req).status_code==409
    expire('credit:'+rid)
    assert client.post('/api/financial/resolve',json={**req,'actual':.5}).status_code==409
    assert client.post('/api/financial/resolve',json=req).status_code==200
    assert vidiq.usage()['held']==0 and vidiq.usage()['today']==0
    vidiq._charge('vidiq_keyword_research',5,rid)
    assert vidiq.usage()['today']==0  # Configured estimate cannot override a reviewed provider receipt.
    with SessionLocal() as s:
        kinds={r.kind for r in s.query(FinancialResolution).filter_by(reservation_id=rid)}
        assert kinds=={'credit','credit_estimate'}


def test_live_unbound_paid_request_cannot_be_reconciled(client):
    with br.attempt('fixture',.7) as rid:
        req=form(client,rid);req['confirm_interrupted']=True
        assert client.post('/api/financial/resolve',json=req).status_code==409
    assert client.post('/api/financial/resolve',json=req).status_code==409  # state version changed
    assert client.post('/api/financial/resolve',json=form(client,rid)).status_code==200


def test_competing_receipts_and_immutable_decisions(client):
    with br.attempt('fixture',.7) as rid:pass
    req=form(client,rid)
    with ThreadPoolExecutor(2) as pool:
        replies=list(pool.map(lambda d:client.post('/api/financial/resolve',json=d).status_code,
            [req,{**req,'request_key':str(uuid4()),'actual':.3}]))
    assert sorted(replies)==[200,409]
    with SessionLocal() as s:
        assert s.query(FinancialResolution).filter_by(reservation_id=rid).count()==1
        with pytest.raises(IntegrityError,match='append-only'):
            s.execute(text('DELETE FROM financial_resolutions WHERE reservation_id = :rid'),{'rid':rid});s.commit()
        s.rollback()
    assert TestClient(app).get('/api/financial/holds').status_code==401


def test_usage_accounting_survives_content_lease_loss():
    with SessionLocal() as s:
        handle=leases.acquire(s,'synthetic-content','fixture-owner');s.commit()
    with leases.bind(handle),br.attempt('gemini',.7) as rid:
        with leases.bind(None),SessionLocal() as s:
            leases.revoke(s,handle.resource);s.commit()
        costs.record_gemini('gemini-3.8-flash',{'promptTokenCount':100,'candidatesTokenCount':100})
    with SessionLocal() as s:
        assert s.get(BudgetReservation,rid).state=='settled'
        assert s.get(BudgetReceipt,rid).amount_micros>0
