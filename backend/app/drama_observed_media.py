"""Bounded observed-delivery comparison, tolerant of normal platform encoding.

These are technical sampled-video/full-audio coverage checks, not a semantic
story/rights certificate. Gain and codec equality are deliberately not gates.
"""
from array import array
import math
from pathlib import Path

from . import control, pending_metadata as pm
from .studio.media_quality import file_hash
from .uploads import validate_video

VERSION = 'observed-drama-media-v1'
POLICY = {'version': VERSION, 'max_bytes': 256 * 1024 * 1024, 'max_seconds': 142,
    'duration_tolerance_seconds': 0.5, 'duration_tolerance_fraction': 0.01,
    'video_samples_per_second': 2, 'video_sample_size': [32, 32],
    'video_bad_frame_mean_absolute_error': 0.18, 'video_bad_frame_fraction': 0.05,
    'source_motion_mean_absolute_difference': 0.003,
    'observed_motion_minimum_source_fraction': 0.10, 'maximum_missing_motion_run_seconds': 1.0,
    'audio_sample_rate': 8000, 'audio_window_seconds': 0.2, 'audio_alignment_windows': 1,
    'audio_signature_rms_distance': 0.20, 'audio_bad_active_window_fraction': 0.05,
    'audio_active_relative_rms': 0.01, 'audio_max_missing_active_run_seconds': 0.6,
    'audio_gain_db': 'reported only; not a gate', 'codecs_and_waveforms': 'need not be identical'}


def fail(message):
    raise ValueError(message)


def run(args, limit):
    result = control.run(args, timeout=120, text=False)
    if result.returncode or len(result.stdout) > limit:
        fail('bounded complete media decode unavailable')
    return result.stdout


def probe(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= POLICY['max_bytes']:
        fail('observed media file missing, unsafe or too large')
    result = validate_video(path)
    duration = float(result['format']['duration'])
    if not math.isfinite(duration) or not 0 < duration <= POLICY['max_seconds']:
        fail('observed duration is outside this bounded verifier')
    video = [s for s in result['streams'] if s.get('codec_type') == 'video']
    audio = [s for s in result['streams'] if s.get('codec_type') == 'audio']
    if len(video) != 1 or len(audio) != 1 or not video[0].get('width') or not video[0].get('height'):
        fail('one completely decodable video and audible audio stream required')
    if audio[0].get('channels') not in (1, 2):
        fail('unsupported observed audio channel layout')
    return {'sha256': file_hash(path), 'bytes': path.stat().st_size, 'duration': duration,
        'aspect': video[0]['width'] / video[0]['height'], 'channels': audio[0]['channels'],
        'video_codec': video[0].get('codec_name'), 'audio_codec': audio[0].get('codec_name'),
        'audio_profile': audio[0].get('profile'), 'width': video[0]['width'], 'height': video[0]['height']}


def pictures(path):
    data = run(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2', '-i', str(path),
        '-map', '0:v:0', '-vf', 'fps=2,scale=32:32:force_original_aspect_ratio=decrease,pad=32:32:(ow-iw)/2:(oh-ih)/2,format=gray',
        '-an', '-threads', '2', '-f', 'rawvideo', '-'], 300 * 1024)
    if not data or len(data) % 1024:
        fail('observed video timeline samples unavailable')
    return [data[i:i+1024] for i in range(0, len(data), 1024)]


def audio(path, channels):
    data = run(['ffmpeg', '-v', 'error', '-nostdin', '-threads', '2', '-i', str(path),
        '-map', '0:a:0', '-vn', '-ar', '8000', '-ac', str(channels), '-f', 'f32le', '-'],
        POLICY['max_seconds'] * 8000 * channels * 4 + 32000)
    values = array('f'); values.frombytes(data)
    if not values or any(not math.isfinite(x) for x in values):
        fail('complete finite decoded audio unavailable')
    return [values[channel::channels] for channel in range(channels)]


def signature(values):
    energy = math.fsum(x*x for x in values)
    if energy <= 1e-12:
        return {'rms': 0, 'features': [0] * 5}
    n = len(values)
    # Short-lag normalized autocorrelation and crossing rate are independent of
    # gain/phase, unlike strict sample equality. They expose wrong/silent audio.
    features = [math.fsum(values[i] * values[i-lag] for i in range(lag, n)) / energy
        for lag in (1, 2, 4, 8)]
    features.append(sum((a < 0) != (b < 0) for a, b in zip(values, values[1:])) / n)
    return {'rms': math.sqrt(energy/n), 'features': features}


def windows(values):
    return [signature(values[i:i+1600]) for i in range(0, len(values), 1600) if len(values[i:i+1600]) >= 400]


def audio_check(source, observed):
    results = []
    for src, dst in zip(source, observed):
        src_rms = math.sqrt(math.fsum(x*x for x in src) / len(src))
        dst_rms = math.sqrt(math.fsum(x*x for x in dst) / len(dst))
        if min(src_rms, dst_rms) <= 1e-8:
            fail('silent/unknown complete audio cannot establish delivered content')
        a, b = windows(src), windows(dst)
        active = [i for i, x in enumerate(a) if x['rms'] >= src_rms * 0.01]
        choices = []
        for offset in (-1, 0, 1):
            bad = []; missing = []
            for i in active:
                if not 0 <= i + offset < len(b):
                    missing.append(i); bad.append(i); continue
                x, y = a[i], b[i+offset]
                if y['rms'] < dst_rms * 0.01:
                    missing.append(i); bad.append(i); continue
                distance = math.sqrt(math.fsum((v-w)**2 for v,w in zip(x['features'], y['features'])) / 5)
                # A grossly different local level envelope can identify omitted
                # speech/music even when a different background tone remains.
                ratio = (y['rms']/dst_rms) / (x['rms']/src_rms)
                if distance > 0.20 or not 0.25 <= ratio <= 4:
                    bad.append(i)
            longest = current = 0; previous = None
            for i in missing:
                current = current + 1 if previous is not None and i == previous + 1 else 1
                previous = i; longest = max(longest, current)
            choices.append({'alignment_seconds': offset * 0.2, 'bad_active_fraction': len(bad)/max(len(active),1),
                'missing_active_run_seconds': longest * 0.2, 'active_windows': len(active),
                'source_windows': len(a), 'observed_windows': len(b)})
        best = min(choices, key=lambda value: (value['bad_active_fraction'], value['missing_active_run_seconds']))
        best['gain_db_observed'] = 20 * math.log10(dst_rms/src_rms)
        best['passed'] = best['active_windows'] > 0 and best['bad_active_fraction'] <= 0.05 and best['missing_active_run_seconds'] <= 0.6
        results.append(best)
    return results


def compare(original, served):
    source, observed = probe(original), probe(served)
    errors = []
    tolerance = max(0.5, source['duration'] * 0.01)
    if abs(source['duration'] - observed['duration']) > tolerance:
        errors.append('observed duration differs beyond codec/timeline tolerance')
    if abs(source['aspect'] - observed['aspect']) > 0.02:
        errors.append('observed aspect indicates crop/stretch or unsupported transformation')
    a, b = pictures(original), pictures(served)
    frame_results = []
    for shift in (-1, 0, 1):
        distances = [sum(abs(v-w) for v,w in zip(frame, b[i+shift])) / (1024 * 255)
            if 0 <= i+shift < len(b) else 1 for i, frame in enumerate(a)]
        longest=current=0
        for i in range(1,len(a)):
            if not 1<=i+shift<len(b):
                current+=1;longest=max(longest,current);continue
            source_motion=sum(abs(v-w) for v,w in zip(a[i-1],a[i]))/(1024*255)
            observed_motion=sum(abs(v-w) for v,w in zip(b[i+shift-1],b[i+shift]))/(1024*255)
            missing_motion=source_motion>0.003 and observed_motion<max(0.0008,source_motion*0.10)
            current=current+1 if missing_motion else 0;longest=max(longest,current)
        frame_results.append({'alignment_seconds': shift * 0.5,'missing_motion_run_seconds':longest*0.5,
            'bad_frame_fraction': sum(x > 0.18 for x in distances) / len(distances),
            'mean_absolute_error': sum(distances) / len(distances),
            'source_samples': len(a), 'observed_samples': len(b)})
    visual = min(frame_results, key=lambda value: (value['bad_frame_fraction'], value['mean_absolute_error']))
    if visual['bad_frame_fraction'] > 0.05:
        errors.append('sampled complete video timeline differs beyond re-encoding tolerance')
    if visual['missing_motion_run_seconds']>1.0:
        errors.append('observed video freezes while the reviewed source timeline continues moving')
    channels = audio_check(audio(original, source['channels']), audio(served, source['channels']))
    if any(not channel['passed'] for channel in channels):
        errors.append('full decoded per-channel audio coverage/content signature differs or is missing')
    value = {'policy': POLICY, 'policy_sha256': pm.digest(POLICY), 'source': source, 'observed': observed,
        'duration_tolerance_seconds': tolerance, 'visual': visual, 'audio_channels': channels,
        'media_checks_passed': not errors, 'errors': errors,
        'scope': 'Full decode, sampled video timeline and full decoded audio windows; not a semantic/perceptual or rights certificate'}
    return value


def passes(value):
    """Re-evaluate measured bounds; a stored success boolean is insufficient."""
    try:
        if value['policy']!=POLICY or value['policy_sha256']!=pm.digest(POLICY) or value['errors']:
            return False
        source,observed=value['source'],value['observed'];visual=value['visual'];audio=value['audio_channels']
        finite=lambda x:type(x) in (int,float) and math.isfinite(x)
        if (not all(finite(p['duration']) and 0<p['duration']<=142 and 0<p['bytes']<=POLICY['max_bytes']
                and isinstance(p['sha256'],str) and len(p['sha256'])==64 for p in (source,observed))
                or abs(source['duration']-observed['duration'])>max(0.5,source['duration']*.01)
                or abs(source['aspect']-observed['aspect'])>.02
                or not 0<=visual['bad_frame_fraction']<=.05 or not 0<=visual['missing_motion_run_seconds']<=1
                or abs(visual['source_samples']-source['duration']*2)>2
                or abs(visual['observed_samples']-observed['duration']*2)>2
                or source['channels'] not in (1,2) or len(audio)!=source['channels']):
            return False
        return all(finite(c['bad_active_fraction']) and 0<=c['bad_active_fraction']<=.05
            and finite(c['missing_active_run_seconds']) and 0<=c['missing_active_run_seconds']<=.6
            and c['active_windows']>0 and abs(c['source_windows']-source['duration']*5)<=2
            and abs(c['observed_windows']-observed['duration']*5)<=2 for c in audio)
    except (KeyError,TypeError,ValueError):
        return False
