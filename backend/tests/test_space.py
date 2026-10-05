"""Disk guard: at 80% back up first, save renders to Drive, then free until < 70%."""

import datetime as dt

import pytest

from backend.app import space


@pytest.fixture()
def fake(monkeypatch):
    from backend.app import backup, cleanup
    from backend.app.studio import archive, drive_store
    calls = []
    usage = iter([85.0, 75.0, 72.0, 68.0, 68.0, 68.0])   # start, before footage, before motion, before backups …
    monkeypatch.setattr(space, "usage_pct", lambda: next(usage))
    monkeypatch.setattr(backup, "create_backup", lambda tag="manual", upload_to_drive=True: calls.append("backup") or {"filename": "b.gz"})
    monkeypatch.setattr(drive_store, "save", lambda pid: calls.append(f"drive:{pid}"))
    monkeypatch.setattr(archive, "free_footage_sweep", lambda: calls.append("footage") or [])
    monkeypatch.setattr(cleanup, "prune_lru_cache", lambda d, n, target_ratio=0.8: calls.append("motion") or {})
    monkeypatch.setattr(backup, "prune_local_backups", lambda keep=7: calls.append("backups") or [])
    monkeypatch.setattr(space, "_purge_expired", lambda: calls.append("clips") or {})
    return calls


def test_backs_up_first_then_frees_until_under_70(fake, session):
    from backend.app.models import StudioProject
    p = StudioProject(tmdb_id=500, title="Unsaved", render={"file": "r.mp4", "rendered_at": "r2"},
                      drive={"status": "saved", "rendered_at": "r1"})
    session.add(p)
    session.commit()
    did = space.run()
    assert fake[0] == "backup"                                 # nothing before the backup
    assert f"drive:{p.id}" in fake and fake.index(f"drive:{p.id}") < fake.index("footage")
    assert fake[-2:] == ["footage", "motion"] and "backups" not in fake   # stopped once under 70%
    assert did["after_pct"] == 68.0
    session.delete(p)
    session.commit()


def test_no_backup_no_deleting(fake, monkeypatch):
    from backend.app import backup

    def boom(tag="manual", upload_to_drive=True):
        raise RuntimeError("disk read error")

    monkeypatch.setattr(backup, "create_backup", boom)
    did = space.run()
    assert "backup_error" in did and "footage" not in fake


def test_only_at_80_percent_and_at_most_hourly(monkeypatch):
    started = []
    monkeypatch.setattr(space.control, "start_thread", lambda *a, **k: started.append(a) or "job")
    monkeypatch.setattr(space.settings, "disk_free_at_percent", 80.0)
    space.state["last_run"] = None
    monkeypatch.setattr(space, "usage_pct", lambda: 79.0)
    assert not space.maybe_free() and started == []
    monkeypatch.setattr(space, "usage_pct", lambda: 81.0)
    now = dt.datetime.now(dt.timezone.utc)
    assert space.maybe_free(now) and len(started) == 1
    space._lock.release()                                      # the fake thread never ran
    assert not space.maybe_free(now + dt.timedelta(minutes=30))  # cooldown
    space.state["last_run"] = None
