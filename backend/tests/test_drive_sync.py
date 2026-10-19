"""Exercise Drive-to-queue imports with a real isolated DB and fake Drive pages."""
from backend.app import drive_sync
from backend.app.models import QueueItem
from scripts.e2e.fake_archive_drive import Reply


class ChannelDrive:
    def files(self):
        return self

    def get(self, **kwargs):
        assert kwargs['fileId'] == 'parent'
        return Reply(lambda: {'name': 'Movie Clips'})

    def list(self, **kwargs):
        assert kwargs['supportsAllDrives'] and kwargs['includeItemsFromAllDrives']
        if "mimeType = 'application/vnd.google-apps.folder'" in kwargs['q']:
            return Reply(lambda: {'files': [{'id': 'channel', 'name': 'Fixture channel'}]})
        assert "'channel' in parents" in kwargs['q']
        if kwargs.get('pageToken') is None:
            return Reply(lambda: {'files': [{'id': 'video-one', 'name': 'First #shorts [aaaaaaaaaaa].mp4',
                                             'mimeType': 'video/mp4'}], 'nextPageToken': 'second'})
        assert kwargs['pageToken'] == 'second'
        return Reply(lambda: {'files': [{'id': 'video-two', 'name': 'Second [bbbbbbbbbbb].mp4',
                                         'mimeType': 'video/mp4'}]})


def test_channel_sync_imports_all_pages_as_review_and_deduplicates(session, monkeypatch):
    monkeypatch.setattr(drive_sync, 'get_drive_service', ChannelDrive)
    monkeypatch.setattr(drive_sync, '_default_destination', lambda: ['Cinama:youtube'])

    result = drive_sync.sync_drive_to_queue('parent', 'Movie Clips', False, session)
    assert result['ok'] and result['added'] == 2 and result['total_scanned'] == 2
    rows = session.query(QueueItem).order_by(QueueItem.position).all()
    assert len(rows) == 2
    assert all(row.status == 'review' and row.scheduled_at is None for row in rows)
    assert all(row.accounts == ['Cinama:youtube'] for row in rows)
    assert all(row.source == 'Movie Clips / Fixture channel' for row in rows)
    assert {row.drive_link for row in rows} == {
        'https://drive.google.com/file/d/video-one/view',
        'https://drive.google.com/file/d/video-two/view',
    }
    ids = [row.id for row in rows]

    repeated = drive_sync.sync_drive_to_queue('parent', 'Movie Clips', False, session)
    assert repeated['added'] == 0 and repeated['skipped'] == 2
    assert [row.id for row in session.query(QueueItem).order_by(QueueItem.position)] == ids
