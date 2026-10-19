"""Synthetic provenance/history regressions; all retrieval is replaced locally."""
import copy
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from backend.app import db
from backend.app.main import app
from backend.app.models import ResearchClaimReview, ResearchSourceRecord, StudioProject
from backend.app.studio import fact_verification as fv

client = TestClient(app, headers={"x-access-token": "test-token"})


@pytest.fixture
def evidence(monkeypatch):
    content = "Synthetic Film opens in June according to this original synthetic source fixture."
    url = "https://apnews.com/article/lineage-fixture"
    monkeypatch.setattr(fv, "fetch", lambda u: dict(url=u, requested_url=u, text=content,
        sha256=hashlib.sha256(content.encode()).hexdigest()))
    facts = dict(title="Synthetic Film", primary_date="2026-06-01", media_type="movie", region="US")
    with db.SessionLocal() as s:
        p = StudioProject(tmdb_id=909180, title="Synthetic Film", facts=facts,
            research={"sources": [{"url": url}], "claims": [{"text": "Synthetic Film opens in June", "sources": [0]}]})
        s.add(p); s.commit(); pid = p.id
    base = f"/api/studio/projects/{pid}"
    response = client.post(base + "/source-records", json={"url": url})
    assert response.status_code == 200, response.text
    args = dict(record_id=response.json()["id"], excerpt=content, context=fv.context(facts),
        verdict="supported", rationale="The synthetic fixture explicitly makes this exact statement.", conflicts=[])
    return pid, base, facts, args


def review(base, args, **changes):
    return client.post(base + "/claims/0/review", json={**args, **changes})


def claim(response):
    assert response.status_code == 200, response.text
    return response.json()["research"]["claims"][0]


def test_later_contradictions_revoke_older_support_until_explicitly_resolved(evidence):
    pid, base, facts, args = evidence
    positive = claim(review(base, args))
    sources = [{"url": "https://apnews.com/article/lineage-fixture"}]
    assert fv.supported(positive, sources, facts, pid)
    negative = claim(review(base, args, verdict="contradicted", conflicts=["The source was superseded by a correction."]))
    assert not fv.supported(positive, sources, facts, pid)
    blocked = review(base, args)
    assert blocked.status_code == 422 and "earlier" in blocked.json()["detail"]
    old_id = negative["evidence"]["review_id"]
    assert review(base, args, resolved_review_ids=[old_id], resolution_rationale="Checked").status_code == 422
    assert review(base, args, resolved_review_ids=["unrelated"], resolution_rationale="I reviewed the later correction in full context.").status_code == 422
    resolved = claim(review(base, args, resolved_review_ids=[old_id],
        resolution_rationale="The revised source explains the earlier correction and confirms the exact June release context."))
    assert fv.supported(resolved, sources, facts, pid)
    forged = copy.deepcopy(resolved); forged["evidence"]["record_sha256"] = "0" * 64
    assert not fv.supported(forged, sources, facts, pid)
    history = client.get(base + "/source-records").json()
    assert len(history["reviews"]) == 3
    assert history["reviews"][-1]["resolved_review_ids"] == [old_id]
    claim(review(base, args, verdict="unresolved", rationale="A newer source may change the release; the conflict remains unresolved."))
    assert not fv.supported(resolved, sources, facts, pid)


def test_punctuation_edits_cannot_hide_a_prior_contradiction(evidence):
    pid, base, facts, args = evidence
    negative = claim(review(base, args, verdict="contradicted"))
    with db.SessionLocal() as s:
        p = s.get(StudioProject, pid)
        p.research = {**p.research, "claims": [{"text": "synthetic film opens in june.", "sources": [0]}]}
        s.commit()
    assert review(base, args).status_code == 422
    resolved = claim(review(base, args, resolved_review_ids=[negative["evidence"]["review_id"]],
        resolution_rationale="The exact earlier decision was reviewed against the source and resolved explicitly."))
    assert fv.supported(resolved, [{"url": "https://apnews.com/article/lineage-fixture"}], facts, pid)


@pytest.mark.parametrize("table", ["research_source_records", "research_claim_reviews"])
@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
def test_evidence_cannot_be_rewritten_even_with_raw_sql(evidence, table, action):
    _, base, _, args = evidence
    result = claim(review(base, args))
    rid = args["record_id"] if table == "research_source_records" else result["evidence"]["review_id"]
    statement = f"DELETE FROM {table} WHERE id=:id" if action == "DELETE" else f"UPDATE {table} SET project_id=-1 WHERE id=:id"
    with db.SessionLocal() as s:
        with pytest.raises(IntegrityError, match="append-only"):
            s.execute(text(statement), {"id": rid})
        s.rollback()
        assert s.execute(text(f"SELECT project_id FROM {table} WHERE id=:id"), {"id": rid}).scalar() > 0


def test_reused_project_id_and_unbound_legacy_records_cannot_inherit_support(evidence):
    pid, base, facts, args = evidence
    supported = claim(review(base, args))
    with db.SessionLocal() as s:
        p = s.get(StudioProject, pid)
        research = copy.deepcopy(p.research)
        stamp = p.created_at + timedelta(seconds=1)
        s.delete(p); s.commit()
        replacement = StudioProject(id=pid, tmdb_id=909181, title="Synthetic Film", facts=facts,
            created_at=stamp, research=research)
        s.add(replacement); s.commit()
        legacy = ResearchSourceRecord(id=str(uuid4()), project_id=pid, project_identity=None,
            url="https://apnews.com/article/lineage-fixture", requested_url="https://apnews.com/article/lineage-fixture",
            text=args["excerpt"], sha256=hashlib.sha256(args["excerpt"].encode()).hexdigest())
        s.add(legacy); s.commit(); legacy_id = legacy.id
    assert not fv.supported(supported, [{"url": "https://apnews.com/article/lineage-fixture"}], facts, pid)
    assert client.get(base + "/source-records").json()["records"] == []
    assert review(base, args).status_code == 422
    assert review(base, args, record_id=legacy_id).status_code == 422


def test_expired_retrieval_cannot_receive_a_new_supported_label(evidence, monkeypatch):
    _, base, _, args = evidence
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=8)
    monkeypatch.setattr(fv, "datetime", Later)
    response = review(base, args)
    assert response.status_code == 422 and "current source" in response.json()["detail"]
    assert client.get(base + "/source-records").json()["reviews"] == []


def test_additive_upgrade_preserves_unknown_identity_and_installs_existing_table_guards(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "old-evidence.sqlite"))
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE research_source_records (id TEXT PRIMARY KEY, project_id INTEGER)"))
        conn.execute(text("CREATE TABLE research_claim_reviews (id TEXT PRIMARY KEY, project_id INTEGER)"))
        conn.execute(text("INSERT INTO research_source_records VALUES ('old', 1)"))
        conn.execute(text("INSERT INTO research_claim_reviews VALUES ('old', 1)"))
    monkeypatch.setattr(db, "engine", engine)
    db._migrate(); db._migrate()
    assert {"project_identity", "resolved_review_ids", "resolution_rationale"}.issubset(
        {c["name"] for c in inspect(engine).get_columns("research_claim_reviews")})
    with engine.connect() as conn:
        assert conn.execute(text("SELECT project_identity FROM research_source_records")).scalar() is None
    for table in ("research_source_records", "research_claim_reviews"):
        for sql in (f"UPDATE {table} SET project_id=2", f"DELETE FROM {table}"):
            with pytest.raises(IntegrityError, match="append-only"), engine.begin() as conn:
                conn.execute(text(sql))
    engine.dispose()


def test_an_old_storyboard_context_cannot_override_the_current_project_context(evidence):
    pid, base, facts, args = evidence
    supported = claim(review(base, args))
    with db.SessionLocal() as s:
        p = s.get(StudioProject, pid)
        p.facts = {**p.facts, "region": "GB"}; s.commit()
    assert not fv.supported(supported, [{"url": "https://apnews.com/article/lineage-fixture"}], facts, pid)
