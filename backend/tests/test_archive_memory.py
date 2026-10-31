"""Focused diskless transfer bounds, complete decoding and same-ID recovery tests."""
import hashlib
import io
import subprocess
import sys
import threading
from unittest.mock import MagicMock

import pytest

from scripts import archive_memory as archive


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
