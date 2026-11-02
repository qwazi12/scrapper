"""Conditional public-copy edits; offline synthetic DB, no scheduler or providers."""
import asyncio
import datetime as dt
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.db import SessionLocal
from backend.app.models import QueueItem, PublicationEvent, UndoEntry, MetadataMutationReceipt
from backend.app import pending_metadata as pm, publication_journal as journal, worker_leases as leases
from backend.app.social import queue_manager as qm, upload_post


@pytest.fixture
def client(monkeypatch):
    from backend.app.main import app
    monkeypatch.setattr(qm, "wake", lambda: pytest.fail("Metadata endpoint must not wake poster"))
    return TestClient(app, headers={"x-access-token": "test-token"})


@pytest.fixture
def row(session):
    now = dt.datetime.now(dt.timezone.utc)
    item = QueueItem(title="download_tracking_001", description="Original caption", tags="#original",
        status="review", accounts=["Dramarama:facebook"], pipeline="DramaRama", source="archive / source-id",
        drive_link="https://drive.google.com/file/d/synthetic-metadata-only/view",
        scheduled_at=now + dt.timedelta(days=1), pinned_at=now + dt.timedelta(days=1),
        post_next_at=now, research={"original_identity": "synthetic"}, notes="Keep these notes")
    session.add(item); session.commit(); session.refresh(item)
    return item.id


def token(client, qid):
    result = client.get(f"/api/pending-metadata/{qid}")
    assert result.status_code == 200, result.text
    return result.json()["fingerprint"]


def patch(client, qid, expected, **changes):
    return client.patch(f"/api/pending-metadata/{qid}", json={"operation_id": str(uuid4()), "expected_fingerprint": expected, **changes})


def snapshot(qid):
    with SessionLocal() as s:
        row = s.get(QueueItem, qid)
        return {c.key: getattr(row, c.key) for c in QueueItem.__table__.columns}


def test_safe_draft_edit_only_changes_public_copy_and_records_narrow_undo(client, row):
    before = snapshot(row)
    expected = token(client, row)
    result = patch(client, row, expected, title="A Secret That Changes Everything", description="A verified scene caption", tags="#DramaRama")
    assert result.status_code == 200, result.text
    assert result.json()["fingerprint"] != expected
    after = snapshot(row)
    assert {k: v for k, v in before.items() if k not in pm.PUBLIC_FIELDS} == {k: v for k, v in after.items() if k not in pm.PUBLIC_FIELDS}
    assert after["title"] == "A Secret That Changes Everything"
    with SessionLocal() as s:
        entry = s.query(UndoEntry).order_by(UndoEntry.id.desc()).first()
        assert entry.payload["fields"] == sorted(pm.PUBLIC_FIELDS)


@pytest.mark.parametrize("field,value", [("title", "Newer title"), ("description", "Newer caption"),
    ("tags", "#newer"), ("status", "ready"), ("accounts", ["Other:youtube"]),
    ("pipeline", "Another brand"), ("scheduled_at", None), ("pinned_at", None), ("research", {"approval": "changed"})])
def test_stale_metadata_approval_schedule_or_brand_rejects(client, row, field, value):
    expected = token(client, row)
    with SessionLocal() as s:
        setattr(s.get(QueueItem, row), field, value); s.commit()
    before = snapshot(row)
    result = patch(client, row, expected, title="Must not replace")
    assert result.status_code == 409, result.text
    assert snapshot(row) == before


@pytest.mark.parametrize("status", ["posting", "posted", "archived", "deleted", "retry", "error", "unknown"])
def test_nonpending_and_publication_states_reject(client, row, status):
    expected = token(client, row)
    with SessionLocal() as s:
        s.get(QueueItem, row).status = status; s.commit()
    assert patch(client, row, expected, title="Must not replace").status_code == 409


@pytest.mark.parametrize("field,value", [("publish_requests", [{"state": "unknown"}]),
    ("publish_requests", [{"state": "accepted", "request_id": "synthetic"}]),
    ("publish_requests", [{"state": "intent"}]), ("publish_requests", [{"state": "rejected"}]),
    ("outstand_post_id", "legacy-published"), ("catchup", {"done": [["account", "url"]]}),
    ("published_at", dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc))])
def test_pending_with_any_submission_evidence_rejects(client, row, field, value):
    expected = token(client, row)
    with SessionLocal() as s:
        setattr(s.get(QueueItem, row), field, value); s.commit()
    assert patch(client, row, expected, title="Must not replace").status_code == 409


def test_retained_history_rejects_even_if_submission_fields_were_cleared(client, row):
    expected = token(client, row)
    with SessionLocal() as s:
        item = s.get(QueueItem, row)
        journal.append(s, row, "synthetic-attempt", "batch_intent", {"state": "intent"}); s.commit()
    assert patch(client, row, expected, title="Must not replace").status_code == 409


@pytest.mark.parametrize("extra", [{"status": "ready"}, {"scheduled_at": None}, {"accounts": []},
    {"pipeline": "Other"}, {"video_path": "other.mp4"}, {"publish_requests": []}, {"title": None},
    {"title": 123}, {"title": "  "}, {}])
def test_whitelist_rejects_protected_null_or_empty_edits(client, row, extra):
    expected = token(client, row)
    before = snapshot(row)
    assert patch(client, row, expected, **extra).status_code == 422
    assert snapshot(row) == before


def test_deleted_and_reused_identity_reject(client, row):
    expected = token(client, row)
    with SessionLocal() as s:
        item = s.get(QueueItem, row); s.delete(item); s.commit()
    assert patch(client, row, expected, title="No resurrection").status_code == 404
    with SessionLocal() as s:
        s.add(QueueItem(id=row, title="Different item", status="review")); s.commit()
    assert patch(client, row, expected, title="No old identity").status_code == 409


def test_publisher_owns_lease_before_transport_metadata_rejects(client, row):
    expected = token(client, row)
    before = snapshot(row)
    with leases.scope(f"publication:{row}"):
        result = patch(client, row, expected, title="Racing edit")
    assert result.status_code == 409, result.text
    assert snapshot(row) == before


def test_metadata_owner_blocks_publisher_before_any_preparation(client, row, monkeypatch):
    entered = []
    original = leases.scope
    async def never(*args, **kw):
        pytest.fail("Publisher must not prepare media while metadata owns lease")
    monkeypatch.setattr(qm, "_publish_queue_item_owned", never)
    def overlap(*args, **kw):
        item = original(*args, **kw)
        class Overlap:
            def __enter__(self):
                handle = item.__enter__()
                with SessionLocal() as other, pytest.raises(upload_post.UploadPostError) as err:
                    asyncio.run(qm.publish_queue_item(row, other))
                assert err.value.status_code == 409
                entered.append(True)
                return handle
            def __exit__(self, *exc):
                return item.__exit__(*exc)
        return Overlap()
    # Only wrap the outer route acquisition, not nested publisher acquisition.
    monkeypatch.setattr(pm.worker_leases, "scope", lambda *a, **kw: overlap(*a, **kw) if kw.get("session") else original(*a, **kw))
    result = patch(client, row, token(client, row), title="Safe leased edit")
    assert result.status_code == 200, result.text
    assert entered


def test_ready_scheduled_edit_preserves_approval_and_times(client, row):
    with SessionLocal() as s:
        s.get(QueueItem, row).status = "ready"; s.commit()
    before = snapshot(row)
    result = patch(client, row, token(client, row), title="Ready title")
    assert result.status_code == 200, result.text
    after = snapshot(row)
    for key in ("status", "scheduled_at", "pinned_at", "post_next_at", "accounts", "pipeline", "research"):
        assert after[key] == before[key]


def test_native_export_copy_guard_is_preserved(client, row):
    with SessionLocal() as s:
        s.get(QueueItem, row).research = {"native_export_id": "synthetic-native"}; s.commit()
    assert patch(client, row, token(client, row), title="Unreviewed treatment").status_code == 409



def test_ready_route_is_absent_even_with_client_review_flags(client, row):
    before = snapshot(row)
    result = client.post(f"/api/pending-metadata/{row}/ready", json={
        "expected_fingerprint": token(client, row), "metadata_reviewed": True, "media_reviewed": True})
    assert result.status_code == 404
    assert snapshot(row) == before
    from backend.app.main import app
    assert "/api/pending-metadata/{item_id}/ready" not in app.openapi()["paths"]


def test_controlled_studio_copy_gate_preserved(client, row):
    with SessionLocal() as s:
        s.get(QueueItem, row).source = "LongForm Studio"; s.commit()
    assert patch(client, row, token(client, row), title="Bypass source").status_code == 409


@pytest.mark.parametrize('field,value', [
    ('publish_requests', [{'state': 'completed', 'profile': 'Dramarama',
        'platforms': ['facebook', 'instagram'], 'request_id': 'synthetic-completed',
        'results': [{'platform': 'facebook', 'success': True}, {'platform': 'instagram', 'success': True}]}]),
    ('catchup', {'done': [['Dramarama:facebook', 'synthetic-result-1'], ['Dramarama:instagram', 'synthetic-result-2']]}),
])
def test_ready_with_completed_partial_platform_results_and_no_published_at_rejects(client, row, field, value):
    # Inventory regression: status Ready + published_at=None is not never-submitted.
    expected = token(client, row)
    with SessionLocal() as s:
        item = s.get(QueueItem, row)
        item.status, item.published_at = 'ready', None
        setattr(item, field, value)
        s.commit()
    before = snapshot(row)
    assert before['status'] == 'ready' and before['published_at'] is None
    result = patch(client, row, expected, title='Must not rewrite partially delivered copy')
    assert result.status_code == 409
    assert 'never-submitted' in result.json()['detail']
    assert client.get(f'/api/pending-metadata/{row}').status_code == 409
    assert snapshot(row) == before


def request_body(client, row, **changes):
    return {"operation_id": str(uuid4()), "expected_fingerprint": token(client, row), **changes}


def count_receipts(operation_id):
    with SessionLocal() as s:
        return s.query(MetadataMutationReceipt).filter_by(operation_id=operation_id).count()


def test_identical_retry_returns_saved_snapshot_once_and_retains_before_after(client, row):
    body = request_body(client, row, title="Verified new public title")
    first = client.patch(f"/api/pending-metadata/{row}", json=body)
    assert first.status_code == 200, first.text
    before_replay = snapshot(row)
    with SessionLocal() as s:
        undo_count = s.query(UndoEntry).count()
    retry = client.patch(f"/api/pending-metadata/{row}", json=body)
    assert retry.status_code == 200 and retry.json()["replayed"] is True
    assert {k: v for k, v in first.json().items() if k != 'replayed'} == {k: v for k, v in retry.json().items() if k != 'replayed'}
    assert snapshot(row) == before_replay
    assert count_receipts(body['operation_id']) == 1
    with SessionLocal() as s:
        assert s.query(UndoEntry).count() == undo_count
    receipt = client.get(f"/api/pending-metadata/receipts/{body['operation_id']}").json()
    assert receipt['scope'] == 'pending_public_metadata_only'
    assert receipt['before']['title'] == 'download_tracking_001'
    assert receipt['after']['title'] == body['title']
    assert receipt['before']['protected_fields'] == receipt['after']['protected_fields']
    assert receipt['response']['replayed'] is True


def test_operation_id_reuse_for_other_payload_or_row_rejects(client, row):
    body = request_body(client, row, title="Original operation")
    assert client.patch(f"/api/pending-metadata/{row}", json=body).status_code == 200
    assert client.patch(f"/api/pending-metadata/{row}", json={**body, 'title': 'Different operation'}).status_code == 409
    assert client.patch(f"/api/pending-metadata/{row+1000}", json=body).status_code == 409
    assert count_receipts(body['operation_id']) == 1
    assert snapshot(row)['title'] == body['title']


def test_replay_after_posting_or_identity_reuse_is_historical_and_does_not_mutate(client, row):
    body = request_body(client, row, title="Prior successful edit")
    first = client.patch(f"/api/pending-metadata/{row}", json=body)
    assert first.status_code == 200
    old_identity = first.json()['queue_identity']
    with SessionLocal() as s:
        current = s.get(QueueItem, row)
        current.status = 'posting'
        current.publish_requests = [{'state': 'accepted', 'request_id': 'synthetic'}]
        s.commit()
    before = snapshot(row)
    with leases.scope(f'publication:{row}'):
        replay = client.patch(f"/api/pending-metadata/{row}", json=body)
    assert replay.status_code == 200 and replay.json()['response_state'] == 'saved_snapshot'
    assert replay.json()['queue_identity'] == old_identity
    assert snapshot(row) == before
    with SessionLocal() as s:
        # Respect existing unresolved-delivery deletion guards: settle first.
        current = s.get(QueueItem, row)
        current.status = 'archived'
        current.publish_requests = [{'state': 'completed', 'request_id': 'synthetic'}]
        s.commit()
        # Delete only the settled mutable row; its immutable receipt survives.
        s.delete(current); s.commit()
        s.add(QueueItem(id=row, title='Different replacement', status='review')); s.commit()
    replacement = snapshot(row)
    replay = client.patch(f"/api/pending-metadata/{row}", json=body)
    assert replay.status_code == 200 and replay.json()['replayed']
    assert replay.json()['queue_identity'] == old_identity
    assert snapshot(row) == replacement


def test_response_lost_after_commit_recovers_original_result_without_second_edit(client, row, monkeypatch):
    from sqlalchemy.orm import Session
    original = Session.commit
    lost = []
    def commit_and_lose_response(s):
        is_operation = any(isinstance(obj, MetadataMutationReceipt) for obj in s.new)
        original(s)
        if is_operation and not lost:
            lost.append(True)
            raise OSError('Synthetic response lost after database commit')
    body = request_body(client, row, title="Committed despite lost response")
    monkeypatch.setattr(Session, 'commit', commit_and_lose_response)
    with pytest.raises(OSError, match='response lost'):
        client.patch(f"/api/pending-metadata/{row}", json=body)
    assert snapshot(row)['title'] == body['title']
    assert count_receipts(body['operation_id']) == 1
    replay = client.patch(f"/api/pending-metadata/{row}", json=body)
    assert replay.status_code == 200 and replay.json()['replayed']
    assert count_receipts(body['operation_id']) == 1


def test_failure_before_commit_rolls_back_metadata_receipt_and_undo_then_retry_succeeds(client, row, monkeypatch):
    from sqlalchemy.orm import Session
    original = Session.commit
    body = request_body(client, row, title="Should be atomic")
    before = snapshot(row)
    with SessionLocal() as s:
        undo_count = s.query(UndoEntry).count()
    def fail_operation(s):
        if any(isinstance(obj, MetadataMutationReceipt) for obj in s.new):
            raise OSError('Synthetic database commit unavailable')
        return original(s)
    with monkeypatch.context() as m:
        m.setattr(Session, 'commit', fail_operation)
        with pytest.raises(OSError, match='commit unavailable'):
            client.patch(f"/api/pending-metadata/{row}", json=body)
    assert snapshot(row) == before
    assert count_receipts(body['operation_id']) == 0
    assert client.get(f"/api/pending-metadata/receipts/{body['operation_id']}").status_code == 404
    with SessionLocal() as s:
        assert s.query(UndoEntry).count() == undo_count
    assert client.patch(f"/api/pending-metadata/{row}", json=body).status_code == 200
    assert count_receipts(body['operation_id']) == 1


def test_inflight_operation_receipt_missing_then_unchanged_retry_can_succeed(client, row):
    body = request_body(client, row, title="Retry after lease release")
    with leases.scope(f'publication:{row}'):
        result = client.patch(f"/api/pending-metadata/{row}", json=body)
        assert result.status_code == 409
        assert client.get(f"/api/pending-metadata/receipts/{body['operation_id']}").status_code == 404
    assert client.patch(f"/api/pending-metadata/{row}", json=body).status_code == 200
    assert count_receipts(body['operation_id']) == 1


def test_noop_has_audit_receipt_but_no_undo_or_metadata_markers(client, row):
    before = snapshot(row)
    with SessionLocal() as s:
        undo_count = s.query(UndoEntry).count()
    body = request_body(client, row, title=before['title'])
    result = client.patch(f"/api/pending-metadata/{row}", json=body)
    assert result.status_code == 200 and result.json()['changed_fields'] == []
    assert result.json()['fingerprint'] == body['expected_fingerprint']
    assert snapshot(row) == before
    assert count_receipts(body['operation_id']) == 1
    with SessionLocal() as s:
        assert s.query(UndoEntry).count() == undo_count


@pytest.mark.parametrize('sql', ['UPDATE metadata_mutation_receipts SET request_hash=\'tampered\' WHERE operation_id=:op',
                                  'DELETE FROM metadata_mutation_receipts WHERE operation_id=:op'])
def test_receipts_are_immutable_at_database_boundary(client, row, sql):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    body = request_body(client, row, title="Audit retention")
    assert client.patch(f"/api/pending-metadata/{row}", json=body).status_code == 200
    with SessionLocal() as s, pytest.raises(IntegrityError, match='append-only'):
        s.execute(text(sql), {'op': body['operation_id']})
    assert count_receipts(body['operation_id']) == 1


def test_protected_reference_projection_does_not_expose_signed_links(client, row):
    with SessionLocal() as s:
        item = s.get(QueueItem, row)
        item.media_url = 'https://example.test/private?secret=synthetic-private-value'
        s.commit()
    response = client.get(f'/api/pending-metadata/{row}')
    assert response.status_code == 200
    assert response.json()['schema_version'] == pm.SCHEMA_VERSION
    assert 'synthetic-private-value' not in response.text
    assert response.json()['protected_fields']['media_url']['sha256']


def test_source_binding_revision_change_conflicts_with_server_fingerprint(client, row):
    from backend.app.models import QueueSourceBinding
    expected = token(client, row)
    with SessionLocal() as s:
        item = s.get(QueueItem, row)
        s.add(QueueSourceBinding(id='synthetic-binding', queue_id=row,
            queue_identity=journal.identity(item), revision=1, data={})); s.commit()
    before = snapshot(row)
    result = patch(client, row, expected, title='Stale source approval must reject')
    assert result.status_code == 409 and 'changed' in result.json()['detail']
    assert snapshot(row) == before


def test_recheck_existing_receipt_after_lease_acquisition_does_not_deadlock_or_repeat(client, row, monkeypatch):
    body = request_body(client, row, title='Already committed operation')
    first = client.patch(f'/api/pending-metadata/{row}', json=body)
    assert first.status_code == 200
    original = pm._replay
    calls = []
    def miss_first_read(*args):
        calls.append(True)
        return None if len(calls) == 1 else original(*args)
    monkeypatch.setattr(pm, '_replay', miss_first_read)
    result = client.patch(f'/api/pending-metadata/{row}', json=body)
    assert result.status_code == 200 and result.json()['replayed']
    assert len(calls) == 2 and count_receipts(body['operation_id']) == 1


def test_receipt_insert_failure_rolls_back_the_metadata_edit(client, row, monkeypatch):
    from sqlalchemy import event
    from backend.app.db import engine
    before = snapshot(row)
    body = request_body(client, row, title='Cannot save without its receipt')
    def reject_receipt(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('INSERT INTO METADATA_MUTATION_RECEIPTS'):
            raise RuntimeError('Synthetic receipt storage unavailable')
    event.listen(engine, 'before_cursor_execute', reject_receipt)
    try:
        with pytest.raises(RuntimeError, match='receipt storage'):
            client.patch(f'/api/pending-metadata/{row}', json=body)
    finally:
        event.remove(engine, 'before_cursor_execute', reject_receipt)
    assert snapshot(row) == before
    assert count_receipts(body['operation_id']) == 0


def test_receipt_table_adds_to_existing_schema_without_changing_queue_data(tmp_path):
    from sqlalchemy import create_engine, inspect, select, text
    from sqlalchemy.exc import IntegrityError
    from backend.app.db import Base
    isolated = create_engine(f'sqlite:///{tmp_path / "existing-schema.db"}')
    receipt_table = MetadataMutationReceipt.__table__
    Base.metadata.create_all(isolated, tables=[t for t in Base.metadata.sorted_tables if t is not receipt_table])
    with isolated.begin() as conn:
        conn.execute(QueueItem.__table__.insert().values(id=700, title='Preserved prior row', status='ready'))
        before = dict(conn.execute(select(QueueItem.__table__)).mappings().one())
    assert receipt_table.name not in inspect(isolated).get_table_names()
    Base.metadata.create_all(isolated)  # Same additive create_all used by normal init_db.
    with isolated.begin() as conn:
        after = dict(conn.execute(select(QueueItem.__table__)).mappings().one())
        assert after == before
        conn.execute(receipt_table.insert().values(operation_id=str(uuid4()), queue_id=700,
            queue_identity='synthetic-identity', request_hash='synthetic-request', data={}))
    with isolated.begin() as conn, pytest.raises(IntegrityError, match='append-only'):
        conn.execute(text('DELETE FROM metadata_mutation_receipts'))
    isolated.dispose()
