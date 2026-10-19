"""No provider contact: retained delivery bytes, journal history and remote safety."""
import datetime as dt
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app import auth, delivery_archive as archive, drive_sync, file_refs, file_use, worker_leases
from backend.app.config import settings
from backend.app.db import SessionLocal
from backend.app.main import app
from backend.app.models import DeliveryArchive, PublicationEvent, QueueItem, RemoteCopyEvidence
from backend.app.social import queue_manager

client = TestClient(app, headers={"x-access-token": "test-token"})


@pytest.fixture
def settled(session):
    root = settings.data_path / "delivery-original"; root.mkdir(exist_ok=True)
    video = root / "original.mp4"; video.write_bytes(b"original synthetic delivered bytes")
    image = root / "original.png"; image.write_bytes(b"original synthetic image bytes")
    item = QueueItem(title="Original delivered words", description="Original description", status="archived",
        video_path=str(video), media_files=[str(image)], media_kind="images",
        drive_link="https://drive.google.com/file/d/retained-synthetic-drive-file-id/view",
        published_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8),
        publish_requests=[{"state": "completed", "request_id": "synthetic-only", "provider_status": {
            "status": "completed", "results": [{"platform": "youtube", "success": True, "post_url": "https://example.invalid/synthetic-publication"}]}}])
    session.add(item); session.commit()
    return item, video, image


def test_sweep_preserves_exact_media_and_delivery_lineage_without_any_remote_trash(session, settled, monkeypatch):
    item, video, image = settled; qid = item.id
    monkeypatch.setattr(drive_sync, "get_drive_service", lambda: pytest.fail("Remote account must not be contacted"))
    assert queue_manager.sweep_archive(session, dt.datetime.now(dt.timezone.utc)) == 1
    row = session.query(DeliveryArchive).filter_by(queue_id=qid).one()
    assert session.get(QueueItem, qid) is None
    assert row.manifest["snapshot"]["title"] == "Original delivered words"
    assert row.manifest["snapshot"]["publish_requests"][0]["request_id"] == "synthetic-only"
    assert session.query(PublicationEvent).filter_by(queue_id=qid, kind="queue_archived").count() == 1
    assert session.query(RemoteCopyEvidence).filter_by(action="retained_queue_reference").count() > 0
    original = video.read_bytes(); original_image = image.read_bytes()
    video.unlink(); image.unlink()
    assert client.get(f"/api/delivery-history/{row.id}/file/0").content == original
    assert client.get(f"/api/delivery-history/{row.id}/file/1").content == original_image
    assert file_refs.protected(session, row.manifest["assets"][0]["blob"])
    assert client.get('/api/delivery-history').json()['deliveries'][0]['queue_id'] == qid


def test_deleted_queue_identity_cannot_be_confused_with_a_later_reused_id(session, settled):
    item, _, _ = settled; qid = item.id
    session.delete(item); session.commit()
    row = session.query(DeliveryArchive).filter_by(queue_id=qid).one()
    session.add(QueueItem(id=qid, title="Different later owner content", status="review")); session.commit()
    assert row.manifest["snapshot"]["title"] == "Original delivered words"
    assert archive.identity(session.get(QueueItem, qid)) != row.queue_identity
    assert client.get(f"/api/delivery-history/{row.id}").json()['manifest']['queue_identity'] == row.queue_identity


def test_pending_submission_or_late_receipt_does_not_get_swept_or_lost(session, settled):
    item, _, _ = settled
    item.publish_requests = [{"state": "unknown", "client_request_id": "synthetic-unknown"}]; session.commit()
    assert queue_manager.sweep_archive(session, dt.datetime.now(dt.timezone.utc)) == 0
    assert session.get(QueueItem, item.id) is not None
    assert session.query(DeliveryArchive).filter_by(queue_id=item.id).count() == 0


def test_active_exclusive_file_writer_keeps_queue_row_and_no_partial_history(session, settled):
    item, video, _ = settled; qid = item.id
    with file_use.pin([video], exclusive=True):
        with ThreadPoolExecutor(1) as pool:
            def sweep():
                with SessionLocal() as other:
                    return queue_manager.sweep_archive(other, dt.datetime.now(dt.timezone.utc))
            assert pool.submit(sweep).result(timeout=10) == 0
    assert session.get(QueueItem, qid) is not None
    assert session.query(DeliveryArchive).filter_by(queue_id=qid).count() == 0


def test_history_copy_failure_keeps_the_original_and_queue_row(session, settled, monkeypatch):
    item, video, _ = settled; qid = item.id
    def fail(*args): raise HTTPException(409, "Synthetic disk pressure")
    monkeypatch.setattr(archive, "_blob", fail)
    assert queue_manager.sweep_archive(session, dt.datetime.now(dt.timezone.utc)) == 0
    assert session.get(QueueItem, qid) is not None and video.exists()
    assert session.query(DeliveryArchive).filter_by(queue_id=qid).count() == 0


def test_raw_sql_cannot_rewrite_delivery_or_remote_copy_evidence(session, settled):
    item, _, _ = settled
    archive_id = archive.retain(session, item); session.commit()
    record = session.query(RemoteCopyEvidence).filter_by(action="retained_queue_reference").first()
    for table, key in [('delivery_archives', archive_id), ('remote_copy_evidence', record.id)]:
        for statement in (f"UPDATE {table} SET id='changed' WHERE id=:id", f"DELETE FROM {table} WHERE id=:id"):
            with pytest.raises(IntegrityError, match="append-only"):
                session.execute(text(statement), {"id": key})
            session.rollback()


def test_media_pass_cannot_read_private_delivery_metadata_and_tampered_bytes_are_rejected(session, settled):
    item, _, _ = settled
    eid = archive.retain(session, item); session.commit()
    token = auth.media_pass()["pass"]
    anonymous = TestClient(app)
    assert anonymous.get(f'/api/delivery-history/{eid}?g={token}').status_code == 401
    assert anonymous.get(f'/api/delivery-history?g={token}').status_code == 401
    assert anonymous.get(f'/api/delivery-history/{eid}/file/0?g={token}').status_code == 200
    row = session.get(DeliveryArchive, eid)
    blob = Path(row.manifest['assets'][0]['blob']); original = blob.read_bytes()
    try:
        blob.write_bytes(b'corrupted immutable blob fixture')
        assert client.get(f'/api/delivery-history/{eid}/file/0').status_code == 409
    finally:
        blob.write_bytes(original)


def test_remote_transfer_receipt_survives_local_worker_ownership_loss(session):
    handle = worker_leases.acquire(session, 'studio:synthetic-transfer-receipt', 'synthetic-owner'); session.commit()
    worker_leases.revoke(session, handle.resource); session.commit()
    transfer = {'id': 'synthetic-retained-remote-file', 'webViewLink': 'https://example.invalid/retained-remote',
        'verification': {'sha256': hashlib.sha256(b'original bytes').hexdigest(), 'remote_md5': 'synthetic-md5',
                         'remote_file_id': 'synthetic-retained-remote-file', 'bytes': 14}}
    with worker_leases.bind(handle):
        archive.record_transfer('StudioProject:original-creation-identity', 991, transfer, 'video')
    row = session.query(RemoteCopyEvidence).filter_by(file_id=transfer['id'], action='verified_transfer_receipt').one()
    assert row.evidence['verification'] == transfer['verification']


def test_legacy_remote_trash_fails_before_contacting_any_account(monkeypatch):
    monkeypatch.setattr(drive_sync, 'get_drive_service', lambda: pytest.fail('No account contact'))
    with pytest.raises(ValueError, match='Automatic Drive trash is disabled'):
        drive_sync.trash_drive_file('synthetic-remote-id-to-keep')


def test_remote_creation_id_is_kept_when_verification_fails_after_worker_expiry(session, tmp_path):
    from backend.app.studio import drive_store
    file = tmp_path / 'remote-uncertain.mp4'; file.write_bytes(b'original synthetic uncertain-transfer bytes')
    handle = worker_leases.acquire(session, 'synthetic:transfer-expiry', 'synthetic-expired-owner'); session.commit()
    class Service:
        def files(self): return self
        def create(self, **kwargs): return self
        def next_chunk(self):
            with SessionLocal() as other:
                worker_leases.revoke(other, handle.resource); other.commit()
            return None, {'id': 'synthetic-unverified-remote-creation', 'webViewLink': 'https://example.invalid/unverified-copy'}
        def get(self, **kwargs): return self
        def execute(self): return {'size': '1', 'md5Checksum': 'wrong'}
    with file_use.pin([file]), worker_leases.bind(handle), archive.transfer_scope('SyntheticTransfer:failed-verification', 991):
        with pytest.raises(drive_store.DriveStoreError, match='remote checksum'):
            drive_store._upload(Service(), file, 'synthetic-folder', file.name)
    row = session.query(RemoteCopyEvidence).filter_by(resource='SyntheticTransfer:failed-verification', action='remote_creation_receipt').one()
    assert row.file_id == 'synthetic-unverified-remote-creation'
    assert row.evidence['expected_sha256'] == hashlib.sha256(file.read_bytes()).hexdigest()
    assert row.evidence['verification_recorded'] is False
    assert session.query(RemoteCopyEvidence).filter_by(resource='SyntheticTransfer:failed-verification', action='verified_transfer_receipt').count() == 0
    assert file.read_bytes() == b'original synthetic uncertain-transfer bytes'


def test_retained_media_type_uses_bytes_after_content_addressed_paths_lose_extensions(tmp_path):
    file = tmp_path / 'a'  # Hash storage has no extension; a filename is not authority.
    file.write_bytes(b'\x00\x00\x00\x20ftypisom' + b'\x00' * 20)
    assert archive.media_type(file) == 'video/mp4'
    file.write_bytes(b'<svg><script>synthetic attack</script></svg>')
    assert archive.media_type(file) == 'application/octet-stream'


def test_age_and_folder_membership_cannot_authorize_deleting_remote_database_backups(monkeypatch):
    from backend.app import backup
    monkeypatch.setattr(drive_sync, 'get_drive_service', lambda: pytest.fail('No remote delete or account read is authorized'))
    assert backup.prune_drive_backups('synthetic-folder-containing-owner-files', keep_days=30) == []
