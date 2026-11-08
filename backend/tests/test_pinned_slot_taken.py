"""ICK Facebook 2026-11-08: 15 hours Wed-Sat had a pinned row and a planned row together. A pinned row's hour is
taken for the rest of its pipeline: the planner gives the other rows the next free slots (max 1 per hour)."""
from backend.app.social import queue_manager as qm
from backend.tests.test_queue import ET, add, et, reset_schedule  # noqa: F401 — fixture


def test_planned_rows_never_share_a_pinned_rows_slot(session, reset_schedule):
    qm.save_schedule(session, {**qm.load_schedule(session), "timezone": "America/New_York", "pipelines": {
        "The ICK Room": {"posts_per_day": 4, "platforms": {"facebook": ["10:00", "13:00", "17:00", "20:00"]}}}})
    pinned = add(session, 1, source="The ICK Room", accounts=["TheIckRoom:facebook"])[0]
    pinned.pinned_at = et(2026, 11, 12, 10)
    session.commit()
    rest = add(session, 6, source="The ICK Room", accounts=["TheIckRoom:facebook"])
    qm.plan_schedule(session, et(2026, 11, 12, 7))
    hours = [qm._aware(it.scheduled_at) for it in [pinned, *rest]]
    assert len(hours) == len(set(hours))                                   # max 1 per hour
    assert qm._aware(pinned.scheduled_at) == et(2026, 11, 12, 10)
    assert [h.astimezone(ET).strftime("%d %H:%M") for h in hours[1:]] == [
        "12 13:00", "12 17:00", "12 20:00", "13 10:00", "13 13:00", "13 17:00"]
