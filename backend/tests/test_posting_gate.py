"""POSTING GATE + SHEET CONTROL (owner, 2026-11-07): with POSTING_GATE=sheet a due row posts only when the Posting
Review sheet approves it (or its Decision is blank 2 h before the slot); anything else waits with its status unchanged."""
import datetime
import types

from fastapi.testclient import TestClient

from backend.app import backup, posting_gate, posting_review
from backend.app.main import app
from backend.app.social import queue_manager as qm, upload_post as up
from backend.tests.test_queue import ET, UTC, add, et, reset_schedule  # noqa: F401 — fixture


def test_two_rows_only_the_approved_one_posts(session, reset_schedule, monkeypatch, tmp_path):
    class Clock(datetime.datetime):
        at = None

        @classmethod
        def now(cls, tz=None):
            return cls.at.astimezone(tz) if tz else cls.at.replace(tzinfo=None)
    monkeypatch.setattr(qm, "datetime", types.SimpleNamespace(**{**vars(datetime), "datetime": Clock}))
    monkeypatch.setattr(qm, "SessionLocal", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)
    monkeypatch.setattr(backup, "maybe_run_nightly_backup", lambda: None)
    monkeypatch.setattr(qm, "_dest_cache", {"at": 0.0, "profiles": None})
    sent = []

    async def list_accounts():
        return [{"id": "Cinama:youtube", "profile": "Cinama"}]

    async def upload_video(path, *, profile, platforms, **kw):
        sent.append(Clock.at.astimezone(ET).strftime("%H:%M"))
        return f"req{len(sent)}"
    monkeypatch.setattr(up, "list_accounts", list_accounts)
    monkeypatch.setattr(up, "upload_video", upload_video)
    cfg = qm.load_schedule(session)
    qm.save_schedule(session, {**cfg, "timezone": "America/New_York",
                               "pipelines": {"Movie Clips": {"platforms": {"youtube": ["08:00", "10:00"]}}}})
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    approved, unlisted = add(session, 2, source="Movie Clips / test", accounts=["Cinama:*"], video_path=str(video))
    monkeypatch.setenv("POSTING_GATE", "sheet")
    monkeypatch.setattr(posting_gate, "sheet_rows", lambda: {str(approved.id): {"Decision": "Approve"}})
    slot = et(2026, 11, 7, 8)
    for minutes in (-1, 0, 5, 120, 125):
        Clock.at = slot + datetime.timedelta(minutes=minutes)
        qm.run_scheduler_tick()
    session.refresh(approved)
    session.refresh(unlisted)
    assert sent == ["08:00"]                                   # the approved row at its slot; the unlisted one never
    assert (approved.status, unlisted.status) == ("posting", "ready")   # the unlisted row waits, status unchanged
    monkeypatch.setenv("POSTING_GATE", "off")                  # the switch off: today's behaviour again
    while len(sent) < 2 and Clock.at < slot + datetime.timedelta(days=2):
        Clock.at += datetime.timedelta(minutes=5)
        qm.run_scheduler_tick()
    session.refresh(unlisted)
    assert unlisted.status == "posting" and len(sent) == 2


def test_gate_decisions(monkeypatch):
    now = datetime.datetime(2026, 11, 7, 12, tzinfo=UTC)
    rows = {"1": {"Decision": "Approve"}, "2": {"Decision": "Skip"}, "3": {"Decision": "Edit"}, "4": {"Decision": ""}}
    monkeypatch.setenv("POSTING_GATE", "sheet")
    monkeypatch.setattr(posting_gate, "sheet_rows", lambda: rows)
    hour = datetime.timedelta(hours=1)
    assert posting_gate.check(1, now, now) is None
    assert "Skip" in posting_gate.check(2, now, now) and "Edit" in posting_gate.check(3, now, now)
    assert posting_gate.check(4, now + hour, now) is None                # blank, slot within 2 h
    assert posting_gate.check(4, now + 3 * hour, now)                     # blank, slot further out
    assert posting_gate.check(5, now, now) == "not in the Posting Review sheet"

    def unreadable():
        raise RuntimeError("Posting Review sheet unreadable: down")
    monkeypatch.setattr(posting_gate, "sheet_rows", unreadable)
    assert "unreadable" in posting_gate.check(1, now, now)               # fails closed
    monkeypatch.setenv("POSTING_GATE", "off")
    assert posting_gate.check(1, now, now) is None


def test_apply_copy_sets_the_sheet_words_on_a_studio_bound_row(session, monkeypatch):
    (it,) = add(session, 1, source="LongForm Studio", accounts=["Cinama:*"])
    row = {"Queue ID": str(it.id), "Decision": "Approve", "Proposed YouTube title": "T",
           "Proposed YouTube description": "Hook.\nNotice.\n#A #B", "Proposed Facebook caption": "FB words.\nWhy?\n#A #B",
           "Proposed Instagram caption": "Same as Facebook; tags: #A", "Proposed X post": "X words? #A #B"}
    monkeypatch.setattr(posting_review, "read", lambda: {"Screen Central": [row]})
    client = TestClient(app, headers={"x-access-token": "test-token"})
    assert client.patch(f"/api/queue/{it.id}", json={"platform_copy": {"x": {"text": "y"}}}).status_code == 409   # still locked
    r = client.post(f"/api/ops/posting-review/{it.id}/apply-copy")
    assert r.status_code == 200, r.text
    assert r.json()["platform_copy"] == {"youtube": {"title": "T", "description": "Hook.\nNotice."},
                                         "facebook": {"caption": "FB words.\nWhy?"}, "instagram": {"caption": "FB words.\nWhy?"},
                                         "x": {"text": "X words?"}}
    row["Decision"] = "Edit"
    assert client.post(f"/api/ops/posting-review/{it.id}/apply-copy").status_code == 409


def test_two_review_rows_never_share_a_slot(session, reset_schedule):
    """INSTR_SCRAPPER_20261107T1520Z #2: LongForm #4825 + #4830 both held Nov 8 20:00 ET (Review)."""
    cfg = qm.load_schedule(session)
    qm.save_schedule(session, {**cfg, "timezone": "America/New_York",
                               "pipelines": {"LongForm": {"platforms": {"youtube": ["20:00"]}}}})
    a, b, c = add(session, 3, source="LongForm", status="review", accounts=["Cinama:*"])
    a.scheduled_at = b.scheduled_at = et(2026, 11, 8, 20)
    c.scheduled_at = et(2026, 11, 9, 20)
    session.commit()
    qm.spread_review_slots(session, et(2026, 11, 7, 19))
    hours = [it.scheduled_at.replace(tzinfo=UTC).astimezone(ET).strftime("%d %H:%M") for it in (a, b, c)]
    assert hours == ["08 20:00", "10 20:00", "09 20:00"]      # first keeps it, others keep theirs, the clash moves on
