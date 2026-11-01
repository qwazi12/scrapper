"""Source identity, crash recovery and inventory coverage; external services mocked."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import archive_memory as archive
from scripts.inventory_tiktok_drama import coverage, episode
from scripts import inventory_tiktok_drama as drama_inventory

UID = '7641470747051525128'
SEC = 'MS4wLjABAAAA' + 'a' * 65
HANDLE = 'test_drama'
VID = '7702925760046042388'
SERIES = {'id': '7702915133831566344', 'title': 'Test drama', 'episodes': 2}


def inventory(count=1):
    return {'platform': 'tiktok', 'channel_id': SEC, 'account_id': UID,
            'handle': HANDLE, 'complete': True, 'webpage_url': 'https://www.tiktok.com/@' + HANDLE,
            'items': [{'id': str(int(VID) + i), 'title': 'Episode', 'series_id': SERIES['id'],
                       'series_title': SERIES['title'], 'episode_number': i + 1}
                      for i in range(count)]}


def job(tmp_path, count=1):
    source = tmp_path / 'inventory.json'
    source.write_text(json.dumps(inventory(count)))
    args = SimpleNamespace(inventory=source, state_dir=tmp_path / 'state',
                           parent_id='holding-parent', folder_name=HANDLE, concurrency=2,
                           max_video_bytes=128 * 1024 ** 2, ytdlp_python='/venv/bin/python',
                           brave_profile=None)
    return archive.Archive(args)


@pytest.mark.parametrize('change', [
    {'complete': False}, {'platform': 'other'}, {'account_id': 'not-an-id'},
    {'channel_id': "unsafe' query"}, {'handle': '../escape'},
    {'items': [{'id': VID, 'title': 'A'}, {'id': VID, 'title': 'B'}]},
])
def test_invalid_source_refused_before_any_cloud_request(change):
    source = inventory()
    source.update(change)
    with pytest.raises(ValueError):
        archive.source_contract(source)


def test_canonical_url_reconstructed_instead_of_trusting_inventory_url():
    source = inventory()
    entry = {**source['items'][0], 'webpage_url': 'https://unrelated.example/secret'}
    row = archive.source_row(source, entry, archive.source_contract(source))
    assert row['original_url'] == f'https://www.tiktok.com/@{HANDLE}/video/{VID}'
    assert row['series_id'] == SERIES['id']


def test_reserved_drive_id_recovers_when_full_snapshot_was_not_written(tmp_path):
    first = job(tmp_path, 1001)
    first.patch(VID, drive_file_id='reserved-before-cloud-write', status='uploading')
    assert 'drive_file_id' not in json.loads((first.root / 'ledger.json').read_text())['items'][VID]
    assert json.loads((first.rows_root / (VID + '.json')).read_text())['row']['drive_file_id']
    first.lock_file.close()  # model process exit after durable per-video receipt
    second = archive.Archive(first.args)
    assert second.state['items'][VID]['drive_file_id'] == 'reserved-before-cloud-write'
    assert second.state['items'][VID]['status'] == 'uploading'
    second.lock_file.close()


def test_completed_cloud_id_is_reconciled_without_downloading(tmp_path, monkeypatch):
    task = job(tmp_path)
    task.state['folder_id'] = 'source-folder'
    task.media_folder = MagicMock(return_value='series-folder')
    task.patch(VID, drive_file_id='same-id', file_size=3, md5='md5', sha256='sha')
    monkeypatch.setattr(archive, 'get_drive_service', MagicMock())
    monkeypatch.setattr(archive, 'remote', lambda *_: {
        'id': 'same-id', 'parents': ['series-folder'], 'appProperties': task.props(VID),
        'size': '3', 'md5Checksum': 'md5', 'sha256Checksum': 'sha'})
    task.metadata = MagicMock(side_effect=AssertionError('must not download'))
    task.process(VID)
    assert task.state['items'][VID]['status'] == 'uploaded'
    assert task.state['items'][VID]['drive_parent_id'] == 'series-folder'
    task.metadata.assert_not_called()
    task.lock_file.close()


def test_same_title_series_use_different_durable_destinations(tmp_path, monkeypatch):
    task = job(tmp_path)
    task.state['folder_id'] = 'source-folder'
    service = MagicMock()
    service.files.return_value.list.return_value.execute.return_value = {'files': []}
    service.files.return_value.generateIds.return_value.execute.side_effect = [
        {'ids': ['series-folder-A']}, {'ids': ['series-folder-B']}]
    def remote(_service, fid):
        entries = task.state.get('series_folders', {})
        sid = next(s for s,e in entries.items() if e['folder_id'] == fid)
        # Reservation is already on disk when first checking/creating the folder.
        assert json.loads((task.root / 'ledger.json').read_text())['series_folders'][sid]['folder_id'] == fid
        return {'parents': ['source-folder'], 'mimeType': 'application/vnd.google-apps.folder',
                'appProperties': {'scrapper_archive_channel': SEC, 'scrapper_archive_series': sid}}
    monkeypatch.setattr(archive, 'remote', remote)
    row = task.state['items'][VID]
    assert task.media_folder(service, row) == 'series-folder-A'
    other = {**row, 'series_id': '7702915133831566345'}
    assert task.media_folder(service, other) == 'series-folder-B'
    assert task.media_folder(service, row) == 'series-folder-A'
    assert service.files.return_value.generateIds.return_value.execute.call_count == 2
    task.lock_file.close()


def test_restricted_episode_skipped_without_source_request(tmp_path, monkeypatch):
    task = job(tmp_path)
    task.patch(VID, restricted=True)
    monkeypatch.setattr(archive, 'get_drive_service', MagicMock())
    task.metadata = MagicMock(side_effect=AssertionError('must not unlock'))
    task.process(VID)
    assert task.counts()['skipped'] == 1 and task.counts()['remaining'] == 0
    assert not task.stop.is_set()
    task.metadata.assert_not_called()
    task.lock_file.close()


def test_guarded_client_and_source_identity_checked(tmp_path, monkeypatch):
    task = job(tmp_path)
    info = {'id': VID, 'channel_id': SEC, 'uploader_id': UID, 'uploader': HANDLE,
            'duration': 90}
    run = MagicMock(return_value=SimpleNamespace(returncode=0, stdout=json.dumps(info)))
    monkeypatch.setattr(archive.subprocess, 'run', run)
    assert task.metadata(task.state['items'][VID])['id'] == VID
    command = run.call_args.args[0]
    assert command[0] == '/venv/bin/python' and command[1].endswith('/tiktok_access.py')
    assert 'b[ext=mp4]/b' in command and '--no-cache-dir' in command
    info['uploader_id'] = '7650000000000000000'
    run.return_value.stdout = json.dumps(info)
    with pytest.raises(ValueError, match='uploader identity'):
        task.metadata(task.state['items'][VID])
    task.lock_file.close()


def test_episode_inventory_whitelists_metadata_and_checks_coverage():
    item = {'id': VID, 'desc': 'Episode one', 'author': {
        'id': UID, 'secUid': SEC, 'uniqueId': HANDLE, 'private_data': 'omit'},
        'dramaInfo': {'dramaID': SERIES['id'], 'authorUID': UID,
                      'DramaVideoData': {'EpisodeNumber': 1, 'IsPreview': True}},
        'video': {'duration': 90, 'playAddr': 'https://cdn.example/signed-secret'}}
    row = episode(item, SERIES, UID, SEC, HANDLE)
    assert 'signed-secret' not in json.dumps(row) and 'private_data' not in json.dumps(row)
    assert row['source_preview_flag'] is True
    assert coverage(SERIES, [row]) == [2]
    with pytest.raises(ValueError, match='Duplicate'):
        coverage(SERIES, [row, row])
    item['author']['id'] = 'wrong-source'
    with pytest.raises(ValueError, match='identity'):
        episode(item, SERIES, UID, SEC, HANDLE)


def test_inventory_paginates_recovers_and_reuses_completed_series(tmp_path, monkeypatch):
    """Fake API/network/time, real durable metadata writes; no live extraction."""
    calls, sleeps = [], []
    def item(vid, number):
        return {'id': vid, 'desc': f'Episode {number}',
                'author': {'id': UID, 'secUid': SEC, 'uniqueId': HANDLE},
                'dramaInfo': {'dramaID': SERIES['id'], 'authorUID': UID,
                              'DramaVideoData': {'EpisodeNumber': number}},
                'video': {'duration': 90}}
    def fetch(url, _name, *, query, headers):
        calls.append((url, query, headers))
        if 'user/drama_list' in url:
            return {'statusCode': 0, 'hasMore': False, 'dramaList': [{
                'dramaID': SERIES['id'], 'dramaName': SERIES['title'], 'numVideos': 2}]}
        count = sum('episode/item_list' in c[0] for c in calls)
        if count == 1:
            return {'statusCode': 10101}  # public ServerErrorNot500
        if query['cursor'] == '0':
            return {'statusCode': 0, 'hasMore': True, 'cursor': '1', 'itemList': [item(VID, 1)]}
        assert query['cursor'] == '1'
        return {'statusCode': 0, 'hasMore': False, 'cursor': '2',
                'itemList': [item(str(int(VID) + 1), 2)]}
    downloader = MagicMock()
    downloader.__enter__.return_value.get_info_extractor.return_value._download_json.side_effect = fetch
    monkeypatch.setitem(sys.modules, 'tiktok_access', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'yt_dlp', SimpleNamespace(YoutubeDL=lambda _: downloader))
    monkeypatch.setattr(drama_inventory.time, 'sleep', sleeps.append)
    monkeypatch.setattr(sys, 'argv', ['inventory', '--state-dir', str(tmp_path),
        '--handle', HANDLE, '--uid', UID, '--sec-uid', SEC, '--brave-profile', '/test-owner-profile'])
    drama_inventory.main()
    result = json.loads((tmp_path / 'inventory.json').read_text())
    assert result['complete'] and result['all_episode_slots_present']
    assert [i['episode_number'] for i in result['items']] == [1, 2]
    assert result['listed_episode_slots'] == 2 and 60 in sleeps
    assert all(c[2]['Referer'] == 'https://www.tiktok.com/@' + HANDLE for c in calls)
    calls.clear()
    drama_inventory.main()
    assert calls == []  # completed series checkpoints do not enumerate again


def test_inventory_verification_status_stops_without_auto_retry(tmp_path, monkeypatch):
    downloader = MagicMock()
    request = downloader.__enter__.return_value.get_info_extractor.return_value._download_json
    request.return_value = {'statusCode': 10113}  # SharkSlide, never solve/retry
    sleeps = []
    monkeypatch.setitem(sys.modules, 'tiktok_access', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'yt_dlp', SimpleNamespace(YoutubeDL=lambda _: downloader))
    monkeypatch.setattr(drama_inventory.time, 'sleep', sleeps.append)
    monkeypatch.setattr(sys, 'argv', ['inventory', '--state-dir', str(tmp_path),
        '--handle', HANDLE, '--uid', UID, '--sec-uid', SEC, '--brave-profile', '/test-owner-profile'])
    with pytest.raises(ValueError, match='10113'):
        drama_inventory.main()
    assert request.call_count == 1 and sleeps == [1]
    assert not (tmp_path / 'inventory.json').exists()


def test_inventory_honors_longer_server_wait():
    assert drama_inventory.server_wait({'Retry-After': '2400'}) == 2400
    assert drama_inventory.server_wait({'Retry-After': 'Wed, 21 Oct 2015 07:28:00 GMT'}) == 0
    assert drama_inventory.server_wait({'Retry-After': 'invalid'}) == 0
