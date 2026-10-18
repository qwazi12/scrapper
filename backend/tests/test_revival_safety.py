"""Safety regressions for Phase 0 defects. All providers are synthetic."""
import hashlib
import io
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app import auth, drive_sync
from backend.app.db import SessionLocal
from backend.app.models import StudioProject
from backend.app.studio import gemini, research_gate, runner, sources, stage_script, tmdb


@pytest.mark.parametrize("verdicts", [None, {}, [], [{"i": 1, "verdict": "maybe"}],
    [{"i": True, "verdict": "ok"}], [{"i": 1, "verdict": "ok"}, {"i": 1, "verdict": "ok"}]])
def test_incomplete_or_malformed_verdicts_fail_closed(verdicts):
    checked, issues = research_gate.checked_sentences([{"text": "A fact", "refs": ["F1"]}], verdicts, {"F": ["A fact"]})
    assert issues and checked[0]["check"] == "unchecked"


@pytest.mark.parametrize("refs", [[], ["F999"], [-1], [True], "F1"])
def test_invalid_fact_refs_block(refs):
    _, issues = research_gate.checked_sentences([{"text": "fact", "refs": refs}], [{"i": 1, "verdict": "ok"}], {"F": ["fact"]})
    assert issues


def test_typed_opinion_and_supported_fact():
    checked, issues = research_gate.checked_sentences(
        [{"text": "Exciting!"}, {"text": "A fact"}],
        [{"i": 1, "verdict": "ok", "kind": "opinion", "refs": []},
         {"i": 2, "verdict": "ok", "kind": "fact", "refs": ["F1"]}], {"F": ["A fact"]})
    assert not issues and all(x["check"] == "ok" for x in checked)


def test_dune_pattinson_role_swap_is_blocked_even_with_model_approval():
    facts = {"title": "Dune: Part Three", "cast": [{"actor": "Robert Pattinson", "character": "Scytale"}]}
    assert research_gate.structured_conflicts([{"text": "Robert Pattinson plays Feyd-Rautha."}], facts)
    assert not research_gate.structured_conflicts([{"text": "Robert Pattinson plays Scytale."}], facts)


def test_source_domain_alone_and_python_negative_indices_are_not_evidence():
    srcs = [{"url": "https://apnews.com/article/test", "tier": "trusted"}]
    for refs in ([0], [-1], [True], ["0"], [900]):
        assert not sources.usable({"text": "invented fact", "sources": refs}, srcs)
    b = stage_script.storyboard({"title": "X"}, {"sources": srcs, "claims": [{"text": "invented", "sources": [-1]}]}, [])
    assert b["R"] == [] and b["skipped_findings"] == 1


def test_evidence_requires_exact_record_excerpt_date_and_url():
    record = "Official press record: Screen Central synthetic release."
    url = "https://apnews.com/article/test"
    evidence = {"url": url, "retrieved_text": record, "excerpt": record,
                "record_sha256": hashlib.sha256(record.encode()).hexdigest(),
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "entity": "synthetic",
                "verdict": "supported", "confidence": "high", "conflicts": []}
    claim = {"text": record, "sources": [0], "evidence": evidence}
    assert sources.usable(claim, [{"url": url}])
    evidence["excerpt"] = "This was never retrieved"
    assert not sources.usable(claim, [{"url": url}])


def test_screenplay_and_original_author_are_distinct(monkeypatch):
    monkeypatch.setattr(tmdb, "get", lambda *a, **k: {"title": "Dune: Part Three", "credits": {"crew": [
        {"department": "Writing", "job": "Screenplay", "name": "Brian K. Vaughan"},
        {"department": "Writing", "job": "Novel", "name": "Frank Herbert"}]}})
    facts = tmdb.fact_sheet("movie", 1)
    assert facts["writers"] == ["Brian K. Vaughan"]
    board = stage_script.storyboard(facts, None, None)
    assert "Novel: Frank Herbert" in board["F"] and "Screenplay: Brian K. Vaughan" in board["F"]
    assert not any(x.startswith("Written by:") for x in board["F"])


def test_backup_requires_owner_header_even_with_media_or_path_signature(tmp_path, monkeypatch):
    from backend.app import backup
    from backend.app.main import app
    name = "scrapper_synthetic.db.gz"
    (tmp_path / name).write_bytes(b"private fixture")
    monkeypatch.setattr(backup, "get_local_backups_dir", lambda: tmp_path)
    c = TestClient(app)
    path = f"/api/backup/download/{name}"
    assert c.get(path, params={"g": auth.media_pass()["pass"]}).status_code == 401
    assert c.get(path + "?" + auth.sign_url(path)).status_code == 401
    assert c.get(path, headers={"x-access-token": "test-token"}).content == b"private fixture"


def test_drive_traversal_collision_and_partial_download(tmp_path, monkeypatch):
    import googleapiclient.http
    service = MagicMock()
    service.files().get().execute.return_value = {"name": "../../owner.mp4"}
    monkeypatch.setattr(drive_sync, "get_drive_service", lambda: service)
    class Download:
        def __init__(self, fh, request): self.fh = fh
        def next_chunk(self): self.fh.write(b"synthetic"); return None, True
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", Download)
    first = drive_sync.download_drive_file("https://drive.google.com/file/d/abcdefghijklmno/view", str(tmp_path))
    second = drive_sync.download_drive_file("https://drive.google.com/file/d/abcdefghijklmno/view", str(tmp_path))
    assert first != second and first.parent == tmp_path and first.read_bytes() == b"synthetic"
    assert not list(tmp_path.glob("*.part")) and not (tmp_path.parent / "owner.mp4").exists()
    class Failed(Download):
        def next_chunk(self): self.fh.write(b"partial"); raise OSError("interrupted")
    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", Failed)
    with pytest.raises(OSError): drive_sync.download_drive_file("https://drive.google.com/file/d/abcdefghijklmno/view", str(tmp_path))
    assert set(tmp_path.iterdir()) == {first, second}


def test_missing_checks_persist_blocked_draft_and_do_not_keep_stale_render(monkeypatch):
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=999, title="Synthetic", facts={"title": "Synthetic"}, render={"file": "old.mp4"})
        s.add(p); s.commit(); pid = p.id
    answers = iter([{"sentences": [{"paragraph": 1, "text": "Invented fact.", "refs": ["F1"]}]}, []])
    monkeypatch.setattr(gemini, "ask_json", lambda *a, **k: next(answers))
    with pytest.raises(gemini.GeminiError, match="Research blocked"):
        runner.run_one(pid, "script")
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.script["sentences"][0]["text"] == "Invented fact."
        assert p.script["check_issues"] and p.render is None and p.stage_status == "error"
    with pytest.raises(RuntimeError, match="Research blocked"):
        runner.run_one(pid, "plan")


def test_saved_edits_cannot_reuse_check_and_plan_swap_invalidates_render():
    from backend.app.main import app
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=999, title="Synthetic", script={"sentences": [
            {"paragraph": 1, "text": "Original.", "check": "ok", "refs": ["F1"]}]}, render={"file": "old.mp4"})
        s.add(p); s.commit(); pid = p.id
    c = TestClient(app, headers={"x-access-token": "test-token"})
    r = c.patch(f"/api/studio/projects/{pid}", json={"script": {"sentences": [
        {"paragraph": 1, "text": "Owner edit.", "check": "ok", "refs": ["F1"]}]}})
    assert r.status_code == 200
    assert not r.json()["script_ready"] and r.json()["script"]["sentences"][0]["check"] == "unchecked"
    assert r.json()["render"] is None


def test_suggested_repair_is_not_approved_without_its_own_verdict(monkeypatch):
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=999, title="Synthetic", facts={"title": "Synthetic"})
        s.add(p); s.commit(); pid = p.id
    answers = iter([
        {"sentences": [{"paragraph": 1, "text": "Original false claim.", "refs": ["F1"]}]},
        [{"i": 1, "verdict": "unsupported", "fix": "Still false replacement."}],
        [{"i": 1, "verdict": "unsupported", "reason": "Replacement is unsupported too"}],
    ])
    monkeypatch.setattr(gemini, "ask_json", lambda *a, **k: next(answers))
    with pytest.raises(gemini.GeminiError, match="Replacement is unsupported too"):
        runner.run_one(pid, "script")
    with SessionLocal() as s:
        sc = s.get(StudioProject, pid).script
        assert sc["sentences"][0]["check"] == "unchecked"
        assert sc["sentences"][0]["original"] == "Original false claim."


def test_upstream_stage_failure_keeps_owner_words_but_invalidates_old_approval(monkeypatch):
    with SessionLocal() as s:
        p = StudioProject(tmdb_id=999, title="Synthetic", script={"sentences": [
            {"paragraph": 1, "text": "Owner's original words.", "check": "ok", "refs": ["F1"]}]},
            plan=[{"slot": 1}], render={"file": "previous.mp4"})
        s.add(p); s.commit(); pid = p.id
    def fail(project_id): raise RuntimeError("Synthetic research outage")
    monkeypatch.setitem(runner.STAGES, "gather", fail)
    with pytest.raises(RuntimeError): runner.run_one(pid, "gather")
    with SessionLocal() as s:
        p = s.get(StudioProject, pid)
        assert p.script["sentences"][0]["text"] == "Owner's original words."
        assert research_gate.script_issues(p.script) and p.plan is None and p.render is None


def test_new_countdowns_reject_partial_and_duplicate_lists():
    from backend.app.models import CountdownProject
    from backend.app.studio import countdown
    from fastapi import HTTPException
    entries = [{"rank": i, "title": f"Synthetic {i}", "tmdb_id": i, "media_type": "movie"} for i in range(1, 6)]
    with SessionLocal() as s:
        c = CountdownProject(topic="Top 5", fmt="top5", ranking={"contract_version": 1, "entries": entries[:2]})
        s.add(c); s.commit()
        with pytest.raises(HTTPException, match="exactly 5"):
            countdown.confirm_titles(s, c)
        c.ranking = {"contract_version": 1, "entries": entries[:4] + [entries[0]]}
        with pytest.raises(HTTPException, match="Duplicate"):
            countdown.confirm_titles(s, c)


def test_countdown_requires_actual_inspection_before_release():
    from backend.app.models import CountdownProject
    from backend.app.studio import countdown
    from fastapi import HTTPException
    with SessionLocal() as s:
        c = CountdownProject(topic="Synthetic", fmt="top5", render={"file": "not-real.mp4"})
        s.add(c); s.commit()
        with pytest.raises(HTTPException): countdown.require_release(s, c)
