import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.app.models import QueueItem
from backend.app.social import metadata, outstand, queue_manager as qm

ET = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc


def et(y, mo, d, h, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=ET).astimezone(UTC)


def add(s, n, source="Movie Clips / @VynixAE", status="ready", **kw):
    items = []
    for _ in range(n):
        it = QueueItem(pipeline=source.split(" / ")[-1], source=source, title="t",
                       status=status, **kw)
        s.add(it)
        items.append(it)
    s.commit()
    for it in items:
        if it.position is None:
            it.position = it.id
    s.commit()
    return items


# --- slots ------------------------------------------------------------------
def test_slots_are_every_2h_8am_to_10pm_eastern():
    slots = qm.slots_after(et(2026, 10, 1, 7, 30), 9)
    hours = [s.astimezone(ET).hour for s in slots]
    assert hours == [8, 10, 12, 14, 16, 18, 20, 22, 8]
    assert slots[-1].astimezone(ET).day == 2


def test_slots_skip_the_current_slot_and_overnight():
    slots = qm.slots_after(et(2026, 10, 1, 22, 0), 2)  # exactly at the last slot
    assert [s.astimezone(ET).hour for s in slots] == [8, 10]


# --- planner ----------------------------------------------------------------
def test_plan_assigns_one_slot_per_pipeline_in_position_order(session):
    a = add(session, 3, "Movie Clips / @VynixAE")
    b = add(session, 2, "The ICK Room")
    a[0].position, a[2].position = a[2].position, a[0].position  # reorder
    session.commit()
    now = et(2026, 10, 1, 9, 0)
    qm.plan_schedule(session, now)
    hrs = lambda it: it.scheduled_at.astimezone(ET).hour
    assert [hrs(a[2]), hrs(a[1]), hrs(a[0])] == [10, 12, 14]
    assert [hrs(x) for x in b] == [10, 12]  # independent pipeline, same slots


def test_plan_ignores_review_items(session):
    (r,) = add(session, 1, status="review")
    qm.plan_schedule(session, et(2026, 10, 1, 9))
    assert r.scheduled_at is None


def test_due_item_keeps_its_slot(session):
    (it,) = add(session, 1)
    slot = et(2026, 10, 1, 10)
    it.scheduled_at = slot
    session.commit()
    qm.plan_schedule(session, slot + datetime.timedelta(minutes=1))
    assert qm._aware(it.scheduled_at) == slot


def test_stale_slot_is_replanned_not_backfilled(session):
    (it,) = add(session, 1)
    it.scheduled_at = et(2026, 9, 28, 10)  # server was down for days
    session.commit()
    now = et(2026, 10, 1, 9)
    qm.plan_schedule(session, now)
    assert qm._aware(it.scheduled_at) == et(2026, 10, 1, 10)


def test_tick_posts_one_per_pipeline(session, monkeypatch):
    a = add(session, 2, "Movie Clips / @VynixAE")
    b = add(session, 1, "The ICK Room")
    now = datetime.datetime.now(UTC)
    for it in a + b:
        it.scheduled_at = now - datetime.timedelta(minutes=1)
    session.commit()
    posted = []

    async def fake_publish(item_id, s):
        posted.append(item_id)
        s.get(QueueItem, item_id).status = "posted"
        s.commit()

    monkeypatch.setattr(qm, "publish_queue_item", fake_publish)
    monkeypatch.setattr(qm, "sweep_archive", lambda s, now: 0)
    qm.run_scheduler_tick()
    assert sorted(posted) == sorted([a[0].id, b[0].id])


# --- archive sweep ----------------------------------------------------------
def test_sweep_trashes_drive_file_and_deletes_after_4_days(session, monkeypatch):
    now = datetime.datetime.now(UTC)
    old = add(session, 1, status="posted", drive_link="https://drive.google.com/file/d/OLD/view",
              published_at=now - datetime.timedelta(days=4, minutes=1))[0]
    fresh = add(session, 1, status="posted", drive_link="https://drive.google.com/file/d/NEW/view",
                published_at=now - datetime.timedelta(days=3))[0]
    trashed = []
    import backend.app.drive_sync as ds
    monkeypatch.setattr(ds, "trash_drive_file", lambda link: trashed.append(link) or True)
    old_id, fresh_id = old.id, fresh.id
    assert qm.sweep_archive(session, now) == 1
    assert trashed == ["https://drive.google.com/file/d/OLD/view"]
    assert session.get(QueueItem, old_id) is None
    assert session.get(QueueItem, fresh_id) is not None


def test_sweep_keeps_row_when_drive_trash_fails(session, monkeypatch):
    now = datetime.datetime.now(UTC)
    it = add(session, 1, status="archived", drive_link="https://drive.google.com/file/d/X/view",
             published_at=now - datetime.timedelta(days=10))[0]
    import backend.app.drive_sync as ds

    def boom(link):
        raise RuntimeError("drive down")

    monkeypatch.setattr(ds, "trash_drive_file", boom)
    assert qm.sweep_archive(session, now) == 0
    assert session.get(QueueItem, it.id) is not None


# --- API ----------------------------------------------------------------------
@pytest.fixture()
def client():
    from backend.app.main import app
    return TestClient(app, headers={"x-access-token": "test-token"})


def test_shuffle_persists_order_and_keeps_notes(session, client):
    items = add(session, 6, notes="keep me")
    before = [it.id for it in items]
    for _ in range(5):  # random can return the same order; try a few times
        r = client.post("/api/queue/shuffle", json={"mode": "random"})
        assert r.status_code == 200
        order = [i["id"] for i in client.get("/api/queue").json()]
        if order != before:
            break
    assert sorted(order) == sorted(before) and order != before
    assert all(i["notes"] == "keep me" for i in client.get("/api/queue").json())


def test_list_is_in_posting_order(session, client):
    a, b = add(session, 2)
    a.position, b.position = 20, 10
    session.commit()
    assert [i["id"] for i in client.get("/api/queue").json()] == [b.id, a.id]


def test_bulk_edit_changes_only_sent_fields(session, client):
    a, b = add(session, 2, status="review")
    r = client.post("/api/queue/bulk-action",
                    json={"ids": [a.id, b.id], "action": "edit", "tags": "#new"})
    assert r.status_code == 200
    got = {i["id"]: i for i in client.get("/api/queue").json()}
    assert got[a.id]["tags"] == "#new" and got[a.id]["title"] == "t"


def test_bulk_posted_starts_archive_clock(session, client):
    (a,) = add(session, 1, status="ready")
    client.post("/api/queue/bulk-action", json={"ids": [a.id], "action": "posted"})
    session.refresh(a)
    assert a.published_at is not None


def test_generate_ai_reports_missing_key_instead_of_template(session, client):
    (a,) = add(session, 1)
    r = client.post(f"/api/queue/{a.id}/generate-ai")
    assert r.status_code == 502 and "GEMINI_API_KEY" in r.json()["detail"]


def test_schedule_endpoint(session, client):
    add(session, 2, "Movie Clips / @VynixAE")
    d = client.get("/api/schedule").json()
    assert d["slots_per_day"] == 8 and d["interval_hours"] == 2
    assert d["pipelines"]["Movie Clips"]["ready"] == 2


# --- helpers -------------------------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("This job interview is insane 😂.mp4", "This_job_interview_is_insane.mp4"),
    ("#fyp #movie clip?.mov", "fyp_movie_clip.mov"),
    ("😂😂.mp4", "video.mp4"),
])
def test_safe_upload_name(name, expected):
    assert outstand.safe_upload_name(name) == expected


def test_metadata_non_strict_still_falls_back():
    res = asyncio.run(metadata.generate_social_metadata(["a"]))
    assert res["model"] == "template"


def test_bulk_edit_pipeline_moves_schedule_group(session, client):
    (a,) = add(session, 1, "Movie Clips / @VynixAE")
    client.post("/api/queue/bulk-action", json={"ids": [a.id], "action": "edit", "pipeline": "The ICK Room"})
    session.refresh(a)
    assert a.source == "The ICK Room / @VynixAE"
    assert qm.pipeline_group(a) == "The ICK Room"


# --- Outstand result reconciliation -----------------------------------------
def _post(*accts):
    return {"socialAccounts": [dict(id=i, network="youtube", username=u, status=st, error=e)
                               for i, u, st, e in accts]}


def test_pending_post_stays_posting(session):
    (it,) = add(session, 1, status="posting")
    assert not qm.apply_post_result(it, _post(("A", "@a", "pending", None)), datetime.datetime.now(UTC))
    assert it.status == "posting" and it.published_at is None


def test_all_published_marks_posted_and_starts_clock(session):
    (it,) = add(session, 1, status="posting")
    now = datetime.datetime.now(UTC)
    assert qm.apply_post_result(it, _post(("A", "@a", "published", None)), now)
    assert it.status == "posted" and it.published_at == now


def test_partial_failure_retries_only_failed_accounts(session):
    (it,) = add(session, 1, status="posting", accounts=["A", "B"])
    qm.apply_post_result(it, _post(("A", "@a", "published", None),
                                   ("B", "@b", "failed", "429 Quota exceeded")),
                         datetime.datetime.now(UTC))
    assert it.status == "retry" and it.accounts == ["B"] and it.published_at is None
    assert "quota" in it.notes


def test_reconcile_uses_outstand_status(session, monkeypatch):
    (it,) = add(session, 1, status="posting", outstand_post_id="P1")

    async def fake_get(pid):
        assert pid == "P1"
        return _post(("A", "@a", "failed", "boom"))

    monkeypatch.setattr(outstand, "get_post", fake_get)
    assert qm.reconcile_posting(session, datetime.datetime.now(UTC)) == 1
    assert it.status == "retry"
