"""Original audio tails need exact packet evidence, never relaxed duration gates."""
import copy
import io
import json
import subprocess
from unittest.mock import MagicMock

import pytest

from scripts import archive_memory as archive


def packets(video=32, audio=38):
    return {
        'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'hevc'},
                    {'index': 1, 'codec_type': 'audio', 'codec_name': 'aac'}],
        'packets': [{'stream_index': i, 'pts_time': str(n), 'duration_time': '1',
                     'data_hash': 'SHA256:' + f'{i * 100 + n:064x}'}
                    for i, count in enumerate((video, audio)) for n in range(count)]}


def test_original_audio_tail_requires_every_packet_and_preserves_proof():
    source = packets()
    output = copy.deepcopy(source)
    for p in output['packets']:
        p['pts_time'] = str(float(p['pts_time']) + .13)
    proof = archive.verify_source_audio_overrun(output, source, 31)
    assert proof['reason'] == 'original_audio_tail_preserved'
    assert proof['tracks']['audio']['packet_count'] == 38
    assert proof['tracks']['video']['source_duration'] == 32
    assert len(proof['tracks']['video']['ordered_payload_sha256']) == 64


@pytest.mark.parametrize('damage', ['missing', 'changed', 'reordered', 'retimed', 'desynced', 'codec'])
def test_incomplete_or_changed_media_cannot_use_original_audio_exception(damage):
    source, output = packets(), packets()
    if damage == 'missing':
        output['packets'].pop()
    elif damage == 'changed':
        output['packets'][-1]['data_hash'] = 'SHA256:' + 'f' * 64
    elif damage == 'reordered':
        output['packets'][-1], output['packets'][-2] = output['packets'][-2], output['packets'][-1]
    elif damage == 'retimed':
        output['packets'][-2]['pts_time'] = '36.5'
    elif damage == 'desynced':
        for p in output['packets']:
            if p['stream_index'] == 1:
                p['pts_time'] = str(float(p['pts_time']) + 1)
    else:
        output['streams'][0]['codec_name'] = 'h264'
    with pytest.raises(ValueError, match='packet'):
        archive.verify_source_audio_overrun(output, source, 31)


@pytest.mark.parametrize('video,audio', [(20, 38), (32, 20), (32, 32)])
def test_short_video_or_audio_is_never_excused(video, audio):
    with pytest.raises(ValueError, match='duration differs'):
        archive.verify_source_audio_overrun(packets(video, audio), packets(video, audio), 31)


def test_missing_or_ambiguous_packet_evidence_blocks():
    for damage in ('hash', 'timestamp', 'stream'):
        source = packets()
        if damage == 'hash':
            del source['packets'][0]['data_hash']
        elif damage == 'timestamp':
            del source['packets'][0]['pts_time']
        else:
            source['streams'].append(dict(source['streams'][0]))
        with pytest.raises(ValueError):
            archive.verify_source_audio_overrun(packets(), source, 31)


def test_real_generated_audio_tail_decodes_but_still_needs_original_proof(monkeypatch):
    # Generated, approved test signals only; media remains in RAM.
    result = subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=10:d=3',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=6',
        '-c:v', 'libx264', '-g', '5', '-preset', 'ultrafast', '-c:a', 'aac',
        '-movflags', 'frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', 'pipe:1'],
        capture_output=True, check=True)
    probe = subprocess.run([
        'ffprobe', '-v', 'error', '-show_packets', '-show_streams', '-show_data_hash', 'sha256',
        '-of', 'json', 'pipe:0'], input=result.stdout, capture_output=True, check=True)
    original = json.loads(probe.stdout)
    remote = MagicMock(return_value=original)
    monkeypatch.setattr(archive, 'probe_original_packets', remote)
    buffer = io.BytesIO(result.stdout)
    with pytest.raises(ValueError, match='duration differ'):
        archive.validate_buffer(buffer, 3)
    remote.assert_not_called()
    verified = archive.validate_buffer(buffer, 3, 'https://media.example/temporary')
    assert verified['full_decode_passed']
    assert verified['source_packet_proof']['tracks']['audio']['packet_count'] > 100
    assert 'media.example' not in json.dumps(verified)
    original['packets'].pop()
    with pytest.raises(ValueError, match='packet integrity'):
        archive.validate_buffer(buffer, 3, 'https://media.example/temporary')
    remote.reset_mock()
    with pytest.raises(ValueError, match='duration differ'):
        archive.validate_buffer(buffer, 20, 'https://media.example/temporary')
    remote.assert_not_called()


def test_original_probe_is_bounded_https_only_and_no_media_url_in_evidence(monkeypatch):
    collect = MagicMock(return_value=io.BytesIO(json.dumps(packets()).encode()))
    monkeypatch.setattr(archive, 'collect_pipe', collect)
    assert archive.probe_original_packets('https://media.example/secret')['streams']
    command, payload, bound, _stop = collect.call_args.args
    assert '-show_data_hash' in command and payload == b'' and bound == 16 * 1024 ** 2
    assert collect.call_args.kwargs['timeout'] == 90
    with pytest.raises(ValueError, match='HTTPS'):
        archive.probe_original_packets('file:///private/media')
    assert collect.call_count == 1
