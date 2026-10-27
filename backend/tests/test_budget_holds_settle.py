"""Owner, 2026-10-27: "nothing should be eating the budget". Refused prompts, error answers, small
estimate overshoots and crashed calls no longer leave holds on the daily cap."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.app import costs, budget_reservations as br
from backend.app.db import SessionLocal
from backend.app.models import AppSetting, BudgetReservation, UsageEvent, WorkerLease

REFUSED = {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
           "usageMetadata": {"promptTokenCount": 217, "totalTokenCount": 217}}


@pytest.fixture(autouse=True)
def clean_budget():
    def wipe(s):
        s.query(BudgetReservation).delete(); s.query(UsageEvent).delete()
        s.query(WorkerLease).filter(WorkerLease.resource.like('budget:%')).delete(synchronize_session=False)
        row = s.get(AppSetting, 'costs')
        if row: s.delete(row)
    with SessionLocal() as s:
        wipe(s); s.add(AppSetting(key='costs', value={'daily_usd': 1, 'upload_post_plan': 'Free'})); s.commit()
    yield
    with SessionLocal() as s:
        wipe(s); s.commit()


def state(rid):
    with SessionLocal() as s:
        return s.get(BudgetReservation, rid).state


def test_refused_prompt_is_a_complete_receipt_at_its_input_cost():
    assert costs.no_output(REFUSED) and costs.no_output({"candidates": [{"finishReason": "SAFETY"}]})
    assert not costs.no_output({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]})
    with br.attempt('gemini', .1) as rid:
        costs.record_gemini('gemini-3.8-flash', REFUSED["usageMetadata"], no_output=costs.no_output(REFUSED))
    assert state(rid) == 'settled'
    with SessionLocal() as s:
        assert br.held_micros(s) == 0
        assert 0 < costs.today_spend(s) < .001                                # 217 input tokens


def test_error_answer_is_not_billed_and_releases_its_hold():
    with br.attempt('gemini', .1) as rid:
        br.not_billed()                                                      # HTTP 429/500 from the provider
    assert state(rid) == 'released'
    assert costs.daily_status()['held_usd'] == 0


def test_estimate_slightly_over_its_bound_settles_instead_of_stopping_everything():
    with br.attempt('gemini', .0001) as rid:      # #140 on 2026-10-26: $0.113 against a $0.10 bound
        costs.record_gemini('gemini-3.8-flash', {'promptTokenCount': 100, 'candidatesTokenCount': 1})
    assert state(rid) == 'settled'
    assert br.reserve('fixture', .01)


def _old(rid, state, minutes=60, lease_expired=True):
    with SessionLocal() as s:
        r = s.get(BudgetReservation, rid); r.state = state
        r.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        lease = s.get(WorkerLease, 'budget:' + rid)
        if lease and lease_expired: lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        s.commit()


def test_sweep_settles_old_refusals_on_their_own_day_and_frees_crashed_calls():
    # A refusal recorded by the old code: prompt-only usage, hold left "unknown" yesterday.
    with br.attempt('gemini', .1) as refused:
        costs.record_gemini('gemini-3.8-flash', REFUSED["usageMetadata"])
    assert state(refused) == 'unknown'
    _old(refused, 'unknown', minutes=60 * 30)
    never_sent = br.reserve('fixture', .1); _old(never_sent, 'reserved')
    crashed = br.reserve('fixture', .1); _old(crashed, 'dispatched')
    running = br.reserve('fixture', .1); _old(running, 'dispatched', lease_expired=False, minutes=1)
    count, _ = br.settle_stale()
    assert count == 3
    assert state(refused) == 'settled' and state(never_sent) == 'released'
    assert state(crashed) == 'unknown' and state(running) == 'dispatched'      # crash stays reconcilable
    with SessionLocal() as s:
        assert costs.today_spend(s) == 0                                     # charged on its own day
        ev = s.query(UsageEvent).filter_by(reservation_id=refused, cost_basis='input_tokens_no_output').one()
        assert 0 < ev.cost_usd < .001
