"""Late edits must reject before transport while cleanup stays with its owner."""
import asyncio
from pathlib import Path

import pytest

from backend.app.db import SessionLocal
from backend.app.models import QueueItem,FileUse,PublicationEvent
from backend.app.social import queue_manager as qm,upload_post
from backend.app.studio import media_quality


@pytest.mark.parametrize('temporary',[False,True],ids=['retained-local','temporary-drive-copy'])
def test_edit_during_artifact_hash_rejects_cleanly_and_only_removes_owned_temp(tmp_path,monkeypatch,temporary):
    path=tmp_path/'original-fixture.mp4';path.write_bytes(b'original synthetic bytes; transport must never start')
    with SessionLocal() as s:
        row=QueueItem(title='Original owner title',description='Private original caption',tags='',status='ready',
            accounts=['default:youtube'],video_path=None if temporary else str(path),
            drive_link='https://drive.google.com/file/d/synthetic-only/view' if temporary else None,research={})
        s.add(row);s.commit();qid=row.id
    if temporary:
        from backend.app import drive_sync
        monkeypatch.setattr(drive_sync,'download_drive_file',lambda link:path)
    original_hash=media_quality.file_hash;edits=[]
    def hash_with_late_edit(file):
        if not edits:
            with SessionLocal() as other:
                newer=other.get(QueueItem,qid);newer.title='Newer owner title before durable intent';other.commit()
            edits.append(qid)
        return original_hash(file)
    async def no_transport(*args,**kwargs):raise AssertionError('CAS rejection must precede any provider request')
    monkeypatch.setattr(media_quality,'file_hash',hash_with_late_edit)
    monkeypatch.setattr(qm,'submit_upload',no_transport)
    monkeypatch.setattr(qm,'needs_auto_seo',lambda item:False)
    with SessionLocal() as s,pytest.raises(upload_post.UploadPostError,match='changed') as error:
        asyncio.run(qm.publish_queue_item(qid,s))
    assert error.value.status_code==409
    assert path.exists() is not temporary
    with SessionLocal() as s:
        saved=s.get(QueueItem,qid)
        assert saved.title=='Newer owner title before durable intent' and saved.status=='ready' and not saved.publish_requests
        assert not any(str(path.resolve()) in use.paths for use in s.query(FileUse))
        assert not s.query(PublicationEvent).filter_by(queue_id=qid).all()
