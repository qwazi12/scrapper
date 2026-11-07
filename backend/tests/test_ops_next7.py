"""Next-7-days per-platform export (owner, 2026-11-07, INSTR_SCRAPPER_20261107T1415Z). Read-only; no provider calls."""
import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app import ops_next7
from backend.app.main import app
from backend.app.models import QueueItem
from backend.app.social import queue_manager as qm, upload_post as up
from backend.tests.test_dramarama_schedule import ep

ET = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc


def et(d, h):
    return datetime.datetime(2026, 11, d, h, tzinfo=ET).astimezone(UTC)


def rows(out):
    return [(r["item"].id, r["platform"], r["slot"].astimezone(ET).strftime("%d %H:%M"), r["basis"]) for r in out]


@pytest.fixture
def schedule(session):
    cfg = qm.load_schedule(session)

    def use(pipelines):
        qm.save_schedule(session, {**cfg, "timezone": "America/New_York", "pipelines": pipelines})
    yield use
    qm.save_schedule(session, None)


def add(s, **kw):
    it = QueueItem(title="t", description="d", video_path="video.mp4", status="ready", **kw)
    s.add(it)
    s.commit()
    return it


def test_dramarama_platforms_follow_their_own_sequences(session, schedule):
    schedule({"DramaRama": {"posts_per_day": 4, "platforms": {
        "facebook": ["10:00", "13:00", "17:00", "20:00"], "x": ["11:00", "18:00"], "youtube": ["13:00"]}}})
    owes_x = ep(session, "Alpha", 1, accounts=("Dramarama:x",),
                catchup={"done": [["Dramarama:facebook", "u"], ["Dramarama:youtube", "y"]]})
    fresh = ep(session, "Alpha", 2)
    now = et(3, 7)
    qm.plan_schedule(session, now)
    got = rows(ops_next7.plan(session, now, {"Dramarama": ["facebook", "x", "youtube"]}))
    assert (owes_x.id, "x", "03 11:00", "platform sequence") in got           # X on its own Ep. 1 sequence
    assert (fresh.id, "facebook", "03 10:00", "platform sequence") in got
    assert not any(r[0] == fresh.id and r[1] in ("x", "youtube") for r in got)  # X/YouTube wait for Facebook's EP2
    assert not any(r[0] == owes_x.id and r[1] != "x" for r in got)             # delivered platforms never listed


def test_other_platform_times_take_the_next_free_hour_one_item_each(session, schedule):
    schedule({"The ICK Room": {"platforms": {"facebook": ["10:00", "13:00"], "x": ["11:00"]}}})
    a, b = (add(session, pipeline="The ICK Room", source="The ICK Room / t", accounts=["TheIckRoom:*"]) for _ in range(2))
    c = add(session, pipeline="The ICK Room", source="The ICK Room / t", accounts=["TheIckRoom:x"],
            catchup={"done": [["TheIckRoom:facebook", "u"]]})
    held = add(session, pipeline="The ICK Room", source="The ICK Room / t", accounts=["TheIckRoom:x"],
               catchup={"done": [["TheIckRoom:facebook", "u"]], "held": {"TheIckRoom:x": "X needs H.264"}})
    now = et(3, 7)
    qm.plan_schedule(session, now)
    got = rows(ops_next7.plan(session, now, {"TheIckRoom": ["facebook", "x"]}))
    x = [(i, slot) for i, net, slot, _ in got if net == "x"]
    assert x[0] == (c.id, "03 11:00")                                          # the X catch-up row has X's time
    assert (a.id, "facebook", "03 10:00", "row slot") in got and (b.id, "facebook", "03 13:00", "row slot") in got
    assert (a.id, "04 11:00") in x and (b.id, "05 11:00") in x                # one X post per hour, posting order
    assert all(slot.endswith(":00") for *_, slot, _ in got)
    assert not any(r[0] == held.id for r in got)                              # a held platform has no slot


def test_endpoint_rows(session, schedule, monkeypatch):
    schedule({"The ICK Room": {"platforms": {"facebook": ["10:00"]}}})

    async def list_accounts():
        return [{"id": "TheIckRoom:facebook", "profile": "TheIckRoom"}]
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    it = add(session, pipeline="The ICK Room", source="The ICK Room / t", accounts=["TheIckRoom:*"],
             drive_link="https://drive.google.com/file/d/abc/view", platform_copy={"facebook": {"description": "FB words"}})
    it.scheduled_at = datetime.datetime.now(UTC) + datetime.timedelta(hours=2)
    session.commit()
    r = TestClient(app, headers={"x-access-token": "test-token"}).get("/api/ops/next7")
    assert r.status_code == 200
    (row,) = [x for x in r.json()["rows"] if x["queue_id"] == it.id]
    assert row["brand"] == "ICK" and row["platform"] == "facebook" and row["status"] == "Ready"
    assert row["caption"] == "d" and row["drive_link"].endswith("/d/abc/view")
