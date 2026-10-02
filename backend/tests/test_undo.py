"""Undo: snapshot before a change, restore as-is (manhwa model)."""

import pytest
from fastapi.testclient import TestClient

from backend.app import undo
from backend.app.db import SessionLocal
from backend.app.models import QueueItem, StudioProject, UndoEntry


@pytest.fixture()
def client():
    from backend.app.main import app
    with SessionLocal() as s:
        s.query(UndoEntry).delete()
        s.query(QueueItem).delete()
        s.commit()
    return TestClient(app, headers={"x-access-token": "test-token"})


def _items(n=3):
    with SessionLocal() as s:
        rows = [QueueItem(title=f"t{i}", status="review", tags="#a", position=i + 1, accounts=[]) for i in range(n)]
        s.add_all(rows)
        s.commit()
        return [r.id for r in rows]


def _get(i):
    with SessionLocal() as s:
        return s.get(QueueItem, i)


def test_undo_single_edit(client):
    (a,) = _items(1)
    client.patch(f"/api/queue/{a}", json={"title": "changed"})
    assert client.get("/api/undo", params={"scope": "queue"}).json()["stack"][0]["label"].startswith("Edit #")
    assert client.post("/api/undo", json={"scope": "queue"}).json()["undone"].startswith("Edit #")
    assert _get(a).title == "t0"


def test_undo_bulk_delete_reinserts_same_ids(client):
    ids = _items(3)
    client.post("/api/queue/bulk-action", json={"ids": ids, "action": "delete"})
    assert _get(ids[0]) is None
    client.post("/api/undo", json={"scope": "queue"})
    assert [_get(i).title for i in ids] == ["t0", "t1", "t2"]


def test_undo_mass_edit_and_status(client):
    ids = _items(2)
    client.post("/api/queue/bulk-action", json={"ids": ids, "action": "edit", "tags": "#new"})
    client.post("/api/queue/bulk-action", json={"ids": ids, "action": "approve"})
    client.post("/api/undo", json={"scope": "queue"})          # undo approve
    assert _get(ids[0]).status == "review" and _get(ids[0]).tags == "#new"
    client.post("/api/undo", json={"scope": "queue"})          # undo mass edit
    assert _get(ids[0]).tags == "#a"


def test_undo_shuffle_restores_order(client):
    ids = _items(6)
    before = [_get(i).position for i in ids]
    for _ in range(5):
        client.post("/api/queue/shuffle", json={"mode": "random"})
        if [_get(i).position for i in ids] != before:
            break
    while client.get("/api/undo", params={"scope": "queue"}).json()["stack"]:
        client.post("/api/undo", json={"scope": "queue"})
    assert [_get(i).position for i in ids] == before


def test_undo_create_removes_row(client):
    r = client.post("/api/queue", json={"title": "new one", "pipeline": "Movie Clips"}).json()
    client.post("/api/undo", json={"scope": "queue"})
    assert _get(r["id"]) is None


def test_nothing_to_undo_is_400(client):
    assert client.post("/api/undo", json={"scope": "queue"}).status_code == 400
    assert client.post("/api/undo", json={"scope": "nope"}).status_code == 400


def test_undo_schedule_change(client):
    client.put("/api/schedule/config", json={"timezone": "UTC", "start_hour": 1, "end_hour": 3, "interval_hours": 1})
    assert client.get("/api/schedule").json()["start_hour"] == 1
    client.post("/api/undo", json={"scope": "settings"})
    assert client.get("/api/schedule").json()["start_hour"] == 8


def test_undo_studio_script_edit(client):
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=1, title="x", script={"sentences": [{"paragraph": 1, "text": "Original."}]},
                          plan=[{"slot": 1}])
        s.add(p)
        s.commit()
        pid = p.id
    client.patch(f"/api/studio/projects/{pid}", json={"script": {"sentences": [{"paragraph": 1, "text": "Edited."}]}})
    with SessionLocal() as s:
        assert s.get(StudioProject, pid).plan is None  # stale plan cleared by the edit
    client.post("/api/undo", json={"scope": f"studio:{pid}"})
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.script["sentences"][0]["text"] == "Original." and p.plan == [{"slot": 1}]


def test_undo_history_is_capped(client):
    (a,) = _items(1)
    for i in range(undo.KEEP + 5):
        client.patch(f"/api/queue/{a}", json={"notes": str(i)})
    with SessionLocal() as s:
        assert s.query(UndoEntry).filter(UndoEntry.scope == "queue").count() == undo.KEEP
