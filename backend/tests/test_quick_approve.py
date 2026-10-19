"""One-tap Approve (owner, 2026-10-19): same schema-2 approval, minor findings get a standard decision,
serious findings still need the detailed form, the rights statement is written once."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.app.studio import duration_intent, export_acceptance as ea

FP = "f" * 64
CURRENT = {"fingerprint": FP, "checks": dict(ea.CHECKS),
           "findings": [{"id": "w1", "kind": "visual_repetition", "message": "Visual slot 4 repeats slot 2"},
                        {"id": "w2", "kind": "silence_span", "message": "Silent interval 10.0–14.0s"}]}


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setattr(ea, "view", lambda project, root: CURRENT)
    monkeypatch.setattr(duration_intent, "comparison", lambda project: None)


def _project(**quality):
    return SimpleNamespace(render={"quality": quality})


def test_one_tap_approval_is_a_complete_detailed_approval(session, fake):
    now = datetime.now(timezone.utc)
    approval = ea.quick_accept(session, _project(silence_spans=[{"start": 10, "end": 14}]), None,
                               ea.QuickIn(expected_fingerprint=FP, rights_rationale="Commentary on official trailers and posters."), now)
    assert approval["quick"] and set(approval["checks"]) == set(ea.CHECKS)
    assert set(approval["warning_decisions"]) == {"w1", "w2"}
    ea.require_view_acceptance(CURRENT, approval)          # what Send to queue / publishing checks


def test_rights_statement_is_written_once_then_reused(session, fake):
    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError, match="rights statement once"):
        ea.quick_accept(session, _project(), None, ea.QuickIn(expected_fingerprint=FP), now)
    ea.quick_accept(session, _project(), None,
                    ea.QuickIn(expected_fingerprint=FP, rights_rationale="Commentary on official trailers and posters."), now)
    session.commit()
    again = ea.quick_accept(session, _project(), None, ea.QuickIn(expected_fingerprint=FP), now)
    assert again["rights_rationale"] == "Commentary on official trailers and posters."


@pytest.mark.parametrize("quality,why", [
    ({"black_spans": [{"start": 30, "end": 34}]}, "black screen"),
    ({"silence_spans": [{"start": 60, "end": 70}]}, "silence"),
])
def test_serious_findings_still_need_the_detailed_form(session, fake, quality, why):
    with pytest.raises(RuntimeError, match=why):
        ea.quick_accept(session, _project(**quality), None,
                        ea.QuickIn(expected_fingerprint=FP, rights_rationale="Commentary on official trailers and posters."),
                        datetime.now(timezone.utc))


def test_far_off_length_needs_the_detailed_form(session, fake, monkeypatch):
    monkeypatch.setattr(duration_intent, "comparison", lambda p: {"target_seconds": 600, "actual_seconds": 200})
    assert ea.severe(_project()) == ["length 3.3 min against about 10.0 min requested"]


def test_stale_fingerprint_is_refused(session, fake):
    with pytest.raises(RuntimeError, match="changed"):
        ea.quick_accept(session, _project(), None,
                        ea.QuickIn(expected_fingerprint="0" * 64, rights_rationale="Commentary on official trailers and posters."),
                        datetime.now(timezone.utc))
