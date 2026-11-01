"""Focused diskless transfer bounds, complete decoding and same-ID recovery tests."""
import hashlib
import io
import json
import subprocess
import sys
import threading
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

from scripts import archive_memory as archive


def test_verified_relocation_survives_old_live_worker_ledger(tmp_path):
    state = {'channel_id': 'channel', 'folder_id': 'source-folder', 'parent_id': 'old-parent'}
    destination = {'channel_id': 'channel', 'folder_id': 'source-folder',
                   'parent_id': 'holding-space-123', 'holding_space': 'The Ick Room',
                   'verified_at': '2026-10-31T23:20:00Z'}
    (tmp_path / 'destination.json').write_text(json.dumps(destination))
    args = SimpleNamespace(parent_id='old-parent')
    archive.apply_destination(state, args, 'channel', tmp_path)
    assert state['folder_id'] == 'source-folder'
    assert state['parent_id'] == args.parent_id == 'holding-space-123'
    destination['folder_id'] = 'another-source'
    (tmp_path / 'destination.json').write_text(json.dumps(destination))
    with pytest.raises(ValueError, match='does not match'):
        archive.apply_destination(state, args, 'channel', tmp_path)


def test_pipe_bounds_and_cancellation():
    stop = threading.Event()
    with pytest.raises(ValueError, match='in-memory size bound'):
        archive.collect_pipe([sys.executable, '-c', 'import sys;sys.stdout.buffer.write(b"x"*4096)'],
                             b'', 1024, stop)
    stop.set()
    with pytest.raises(RuntimeError, match='cancellation'):
        archive.collect_pipe([sys.executable, '-c', 'import time;time.sleep(30)'], b'', 1024, stop)


def test_pipe_roundtrip():
    data = b'only in memory' * 100
    result = archive.collect_pipe([sys.executable, '-c',
        'import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())'], data, 4096, threading.Event())
    assert result.getvalue() == data


def test_success_exit_with_transport_interruption_still_retries():
    with pytest.raises(TimeoutError, match='Source transport interrupted'):
        archive.collect_pipe([sys.executable, '-c',
            'import sys;sys.stdout.buffer.write(b"partial media");sys.stderr.write("Connection timed out")'],
            b'', 4096, threading.Event())


def test_failed_ffmpeg_diagnostics_distinguish_timeout_from_unknown_exit():
    with pytest.raises(RuntimeError) as timeout:
        archive.collect_pipe([sys.executable, '-c',
            'import sys;sys.stderr.write("Error opening input: Connection timed out https://cdn.example/private?token=secret");sys.exit(196)'],
            b'', 4096, threading.Event())
    assert archive.failure_kind(timeout.value) == 'transient'
    assert 'secret' not in str(timeout.value)
    with pytest.raises(RuntimeError) as unknown:
        archive.collect_pipe([sys.executable, '-c', 'import sys;sys.exit(196)'],
                             b'', 4096, threading.Event())
    assert archive.failure_kind(unknown.value) == 'manual'


def test_complete_fragmented_mp4_and_truncation():
    result = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=10',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '3',
        '-c:v', 'libx264', '-g', '5', '-preset', 'ultrafast', '-c:a', 'aac', '-movflags',
        'frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', 'pipe:1'], capture_output=True, check=True)
    buffer = io.BytesIO(result.stdout)
    validated = archive.validate_buffer(buffer, 3)
    assert validated['full_decode_passed'] and validated['width'] == 160
    assert validated['sha256'] == hashlib.sha256(result.stdout).hexdigest()
    assert 2.9 < validated['track_durations']['video'] < 3.1
    with pytest.raises(ValueError, match='duration differ'):
        archive.validate_buffer(buffer, 20)


def test_lost_upload_response_reconciles_same_id(monkeypatch):
    service = MagicMock()
    parent, props = 'folder', {'scrapper_video': 'video'}
    empty = {'size': '0', 'parents': [parent], 'appProperties': props}
    complete = dict(empty, size='3', md5Checksum=hashlib.md5(b'abc').hexdigest())
    receipts = iter([empty, complete])
    monkeypatch.setattr(archive, 'remote', lambda *_: next(receipts))
    service.files.return_value.update.return_value.next_chunk.side_effect = TimeoutError('lost reply')
    row = {'file_size': 3, 'md5': complete['md5Checksum'], 'sha256': hashlib.sha256(b'abc').hexdigest()}
    assert archive.upload_buffer(service, 'same-id', io.BytesIO(b'abc'), row, parent, props,
                                 threading.Event()) == complete
    assert service.files.return_value.update.call_args.kwargs['fileId'] == 'same-id'
    assert service.files.return_value.update.call_count == 1


@pytest.mark.parametrize('meta', [
    {'size': '3', 'parents': ['folder'], 'appProperties': {'scrapper_video': 'wrong'}},
    {'size': '5', 'md5Checksum': 'wrong', 'parents': ['folder'], 'appProperties': {'scrapper_video': 'video'}},
])
def test_remote_identity_or_nonempty_mismatch_never_overwritten(monkeypatch, meta):
    service = MagicMock()
    monkeypatch.setattr(archive, 'remote', lambda *_: meta)
    with pytest.raises(ValueError):
        archive.upload_buffer(service, 'same-id', io.BytesIO(b'abc'),
            {'file_size': 3, 'md5': 'expected'}, 'folder', {'scrapper_video': 'video'}, threading.Event())
    service.files.return_value.update.assert_not_called()


class ImmediateOwnerEvent(threading.Event):
    """Fake elapsed cooldown time, not actual network/time recovery."""
    def __init__(self):
        super().__init__()
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self.is_set()


def scheduler():
    job = archive.Archive.__new__(archive.Archive)
    job.args = SimpleNamespace(concurrency=4, cooldown_seconds=60, rate_cooldown_seconds=900, resume_retries=3)
    job.owner_stop = ImmediateOwnerEvent()
    job.stop = threading.Event()
    job.save = MagicMock()
    job.state = {'items': {'done': {'status': 'uploaded', 'drive_file_id': 'preserved'},
                           'todo': {'status': 'discovered'}}}
    return job


def test_timeout_cooldown_automatically_resumes_without_repeating_successes():
    job = scheduler()
    calls = []
    def process(vid):
        assert vid == 'todo'
        calls.append(vid)
        if len(calls) == 1:
            job.state['items'][vid].update(status='failed', transient_attempts=1)
            job.stop.set()
            raise archive.TransferFailure(TimeoutError('source timed out'), 'transient')
        assert not job.stop.is_set()
        assert job.state['active_concurrency'] == 2
        job.state['items'][vid]['status'] = 'uploaded'
    job.process = process
    assert job.drain(['done', 'todo']) == []
    assert calls == ['todo', 'todo']
    assert job.owner_stop.waits == [60]
    assert job.state['items']['done']['drive_file_id'] == 'preserved'


def test_repeated_timeouts_stop_after_three_automatic_resumes():
    job = scheduler()
    def process(vid):
        row = job.state['items'][vid]
        row.update(status='failed', transient_attempts=row.get('transient_attempts', 0) + 1)
        job.stop.set()
        raise archive.TransferFailure(TimeoutError('source timed out'), 'transient')
    job.process = process
    assert job.drain(['todo'])
    assert job.owner_stop.waits == [60, 180, 540]
    assert job.state['items']['todo']['transient_attempts'] == 4


def test_authentication_failure_does_not_auto_restart():
    job = scheduler()
    def process(vid):
        raise archive.TransferFailure(RuntimeError("Sign in to confirm you're not a bot"), 'manual')
    job.process = process
    assert job.drain(['todo'])
    assert not job.owner_stop.waits


def test_owner_stop_during_cooldown_does_not_restart():
    job = scheduler()
    calls = []
    def process(vid):
        calls.append(vid)
        job.state['items'][vid].update(status='failed', transient_attempts=1)
        raise archive.TransferFailure(TimeoutError('source timed out'), 'transient')
    def wait(seconds):
        job.owner_stop.set()
        return True
    job.process = process
    job.owner_stop.wait = wait
    assert job.drain(['todo']) == []
    assert calls == ['todo']


def test_four_transfer_workers_can_run_together():
    job = scheduler()
    ids = ['one', 'two', 'three', 'four']
    job.state['items'] = {vid: {'status': 'discovered'} for vid in ids}
    barrier = threading.Barrier(4)
    threads = set()
    def process(vid):
        threads.add(threading.get_ident())
        barrier.wait(timeout=5)
        job.state['items'][vid]['status'] = 'uploaded'
    job.process = process
    assert job.drain(ids) == []
    assert len(threads) == 4


@pytest.mark.parametrize('error,kind', [
    (TimeoutError('read timed out'), 'transient'),
    (RuntimeError('HTTP Error 503: Service Unavailable'), 'transient'),
    (RuntimeError('HTTP Error 403: Forbidden'), 'manual'),
    (RuntimeError('HTTP Error 429: Too Many Requests'), 'rate_limited'),
    (RuntimeError("HTTP Error 429: Sign in to confirm you're not a bot"), 'manual'),
    (RuntimeError('Quota exceeded'), 'manual'),
    (RuntimeError('Remote checksum mismatch'), 'manual'),
    (RuntimeError('Shared cancellation; incomplete Drive ID retained'), 'cancelled'),
])
def test_recovery_classification(error, kind):
    assert archive.failure_kind(error) == kind


def test_server_retry_after_is_honored():
    import httplib2
    from googleapiclient.errors import HttpError
    error = HttpError(httplib2.Response({'status': '503', 'retry-after': '180'}), b'{}')
    assert archive.retry_after_seconds(error) == 180


def test_setup_timeout_uses_same_bounded_cooldown():
    job = scheduler()
    callback = MagicMock(side_effect=[TimeoutError('Drive setup timed out'), 'connected'])
    assert job.recover_call(callback, 'Drive setup') == 'connected'
    assert job.owner_stop.waits == [60]
    assert callback.call_count == 2


def test_setup_access_failure_does_not_retry():
    job = scheduler()
    callback = MagicMock(side_effect=RuntimeError('permission denied'))
    with pytest.raises(RuntimeError, match='permission denied'):
        job.recover_call(callback, 'Drive setup')
    assert callback.call_count == 1 and not job.owner_stop.waits


def test_rate_limit_waits_longer_and_resumes_with_one_worker():
    job = scheduler()
    calls = []
    def process(vid):
        calls.append(vid)
        if len(calls) == 1:
            job.state['items'][vid].update(status='failed', transient_attempts=1)
            job.stop.set()
            raise archive.TransferFailure(RuntimeError('HTTP Error 429: Too Many Requests'), 'rate_limited')
        assert job.state['active_concurrency'] == 1
        job.state['items'][vid]['status'] = 'uploaded'
    job.process = process
    assert job.drain(['todo']) == []
    assert job.owner_stop.waits == [900]
