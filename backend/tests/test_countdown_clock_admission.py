"""Eligibility age must not depend on a narrator/worker's application clock."""
from datetime import datetime,timedelta,timezone
import pytest
from fastapi import HTTPException
from backend.app.db import SessionLocal
from backend.app.models import CountdownProject
from backend.app.studio import countdown
from backend.tests.test_countdown_eligibility import reviewable  # shared actual current source fixture


def test_skewed_app_clock_cannot_admit_a_database_expired_category_proof(reviewable,monkeypatch):
    from backend.app.worker_leases import clock
    with SessionLocal() as s:
        c=s.get(CountdownProject,reviewable);expired=clock(s)-timedelta(days=8)
        c.ranking={**c.ranking,'eligibility':{**c.ranking['eligibility'],'checked_at':expired.isoformat()}};s.commit()
        monkeypatch.setattr(countdown,'_now',lambda:expired+timedelta(minutes=1))
        with pytest.raises(HTTPException,match='expired'):countdown.require_current_eligibility(s,c)


def test_naive_proof_timestamp_cannot_inherit_the_local_machine_timezone(reviewable):
    with SessionLocal() as s:
        c=s.get(CountdownProject,reviewable)
        c.ranking={**c.ranking,'eligibility':{**c.ranking['eligibility'],'checked_at':datetime.now().isoformat()}};s.commit()
        with pytest.raises(HTTPException,match='expired'):countdown.require_current_eligibility(s,c)


def test_explicit_checked_time_controls_the_calendar_window():
    from backend.app.studio.countdown_eligibility import verify
    moment=datetime(2040,12,31,23,59,tzinfo=timezone.utc)
    proof=verify([],'Top 5 Movies this year','US',checked_at=moment)
    assert proof['criteria']['year_min']==2040 and proof['criteria']['year_max']==2040
    assert proof['checked_at']==moment.isoformat()
    with pytest.raises(ValueError,match='timezone'):verify([],'Top 5 Movies this year','US',checked_at=moment.replace(tzinfo=None))


def test_direct_child_does_not_voice_with_expired_parent_when_app_clock_is_skewed(reviewable,monkeypatch):
    from backend.app.studio import runner
    from backend.app.worker_leases import clock
    with SessionLocal() as s:
        c=s.get(CountdownProject,reviewable);pid=c.segment_ids[0];expired=clock(s)-timedelta(days=8)
        c.ranking={**c.ranking,'eligibility':{**c.ranking['eligibility'],'checked_at':expired.isoformat()}};s.commit()
    monkeypatch.setattr(countdown,'_now',lambda:expired+timedelta(minutes=1))
    calls=[];monkeypatch.setitem(runner.STAGES,'plan',lambda pid:calls.append(pid))
    with pytest.raises(HTTPException,match='expired'):runner.run_one(pid,'plan')
    assert not calls
