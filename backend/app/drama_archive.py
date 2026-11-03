"""Verify archive bytes and canonical episode mapping; filenames are not evidence.

Archive manifests are provenance, not copyright authorization. A separate exact
media/copy owner review is required by drama_controls before readiness.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from . import drive_sync, pending_metadata as pm
from .uploads import validate_video
from .studio.media_quality import file_hash

MAX_MANIFEST_BYTES = 20 * 1024 * 1024
FIELDS = 'id,size,md5Checksum,sha256Checksum,trashed,parents,appProperties,mimeType'


def fail(message):
    raise HTTPException(409, 'Drama archive evidence: ' + message)


def remote(service, fid):
    value = service.files().get(fileId=fid, fields=FIELDS, supportsAllDrives=True).execute()
    if not isinstance(value, dict):
        fail('remote metadata is malformed')
    return value


def size(meta):
    value = meta.get('size')
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,20}', value):
        return int(value)
    fail('remote file size is malformed')


def _hash(value, length):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{' + str(length) + '}', value))


def season(row):
    value = row.get('season', row.get('season_number'))
    if value is not None and (type(value) is not int or value < 1):
        fail('invalid explicit canonical season')
    if 'season' in row and 'season_number' in row and row['season'] != row['season_number']:
        fail('contradictory source season fields')
    return value


def inspect(item, manifest_id, video_id):
    service = drive_sync.get_drive_service()
    meta = remote(service, manifest_id)
    if meta.get('trashed') or not 0 < size(meta) <= MAX_MANIFEST_BYTES:
        fail('missing or oversized manifest')
    content = service.files().get_media(fileId=manifest_id, supportsAllDrives=True).execute()
    if (not isinstance(content, bytes) or len(content) > MAX_MANIFEST_BYTES
            or len(content) != size(meta) or hashlib.md5(content).hexdigest() != meta.get('md5Checksum')):
        fail('manifest size/checksum mismatch')
    try:
        manifest = json.loads(content)
        row = manifest['items'][video_id]
        channel = manifest['channel_id']
        folder_id = manifest['folder_id']
        sid, episode = row['series_id'], row['episode_number']
    except (KeyError, TypeError, ValueError):
        fail('canonical series/source mapping missing')
    if (not isinstance(manifest.get('items'), dict)
            or any(not isinstance(r, dict) for r in manifest['items'].values())):
        fail('canonical source inventory is malformed')
    if (manifest.get('platform') != 'tiktok' or manifest.get('inventory_complete') is not True
            or not isinstance(channel, str) or not isinstance(sid, str) or not sid
            or type(episode) is not int or episode < 1
            or meta.get('appProperties') != {'scrapper_archive_manifest': 'tiktok:' + channel}
            or folder_id not in meta.get('parents', [])):
        fail('manifest identity or complete source inventory is unconfirmed')
    source_season = season(row)
    series = [r for r in manifest['items'].values() if r.get('series_id') == sid and season(r) == source_season]
    try:
        series.sort(key=lambda r: r['episode_number'])
        catalog = [{'episode': r['episode_number'], 'video_id': r['video_id'], 'url': r['original_url']}
                   for r in series]
    except (KeyError, TypeError):
        fail('series source catalog is incomplete')
    if (not catalog or len(catalog) > 2000 or [r['episode'] for r in catalog] != list(range(1, len(catalog) + 1))
            or len({r['video_id'] for r in catalog}) != len(catalog)):
        fail('missing, ambiguous or duplicate episode slots')
    for r in catalog:
        if not isinstance(r['url'], str) or not isinstance(r['video_id'], str):
            fail('invalid canonical source URL/ID')
        url = urlsplit(r['url'])
        if (not re.fullmatch(r'\d{15,24}', r['video_id']) or url.scheme != 'https'
                or url.hostname != 'www.tiktok.com' or url.username or url.password
                or not re.fullmatch(r'/@[A-Za-z0-9_.]+/video/' + r['video_id'], url.path)):
            fail('invalid canonical source URL/ID')
    if row.get('restricted') or row.get('status') not in ('uploaded', 'aliased'):
        fail('episode has no accessible verified archive copy')
    canonical = row
    if row.get('status') == 'aliased':
        canonical = manifest['items'].get(row.get('canonical_video_id'), {})
        if (row.get('alias_evidence') != 'full_decode_and_exact_sha256_md5_size'
                or row.get('canonical_drive_file_id') != canonical.get('drive_file_id')
                or any(row.get(k) != canonical.get(k) for k in ('sha256', 'md5', 'file_size'))):
            fail('alias is not tied to an exact verified original')
    fid = canonical.get('drive_file_id')
    if (canonical.get('status') != 'uploaded' or canonical.get('restricted')
            or canonical.get('full_decode_passed') is not True
            or type(canonical.get('file_size')) is not int or canonical['file_size'] <= 0
            or not _hash(canonical.get('sha256'), 64) or not _hash(canonical.get('md5'), 32)
            or drive_sync.extract_drive_file_id(item.drive_link or '') != fid):
        fail('queue/Drive file identity or archive decode receipt differs')
    actual = remote(service, fid)
    expected_props = {'scrapper_platform': 'tiktok', 'scrapper_account': channel,
                      'scrapper_video': canonical.get('video_id')}
    if (actual.get('trashed') or not str(actual.get('mimeType', '')).startswith('video/')
            or actual.get('appProperties') != expected_props
            or actual.get('sha256Checksum') != canonical['sha256']
            or actual.get('md5Checksum') != canonical['md5']
            or size(actual) != canonical.get('file_size')):
        fail('remote video identity/size/checksums changed')
    parent = canonical.get('drive_parent_id')
    if parent not in actual.get('parents', []):
        fail('remote parent differs from the verified archive receipt')
    # Aliases may refer to another canonical series, but its marked parent must
    # still belong to the same archive root. Never infer identity from its name.
    folder = remote(service, parent)
    if (folder.get('trashed') or folder_id not in folder.get('parents', [])
            or folder.get('appProperties', {}).get('scrapper_archive_channel') != channel
            or folder.get('appProperties', {}).get('scrapper_archive_series') != canonical.get('series_id')):
        fail('canonical series folder provenance differs')
    path = None
    try:
        path = drive_sync.download_drive_file(fid)
        probe = validate_video(Path(path))
        if Path(path).stat().st_size != canonical['file_size'] or file_hash(Path(path)) != canonical['sha256']:
            fail('downloaded media differs from the verified canonical copy')
        aliases = sorted({r.get('drive_file_id') or r.get('canonical_drive_file_id') for r in manifest['items'].values()
                          if r.get('sha256') == canonical['sha256']} - {None})
        video = next(stream for stream in probe['streams'] if stream.get('codec_type') == 'video')
        audio = next((stream for stream in probe['streams'] if stream.get('codec_type') == 'audio'), {})
        return {'series_key': pm.digest(['tiktok', channel, sid, source_season]), 'series_id': sid,
                'season': source_season, 'channel_id': channel,
                'episode': episode, 'total_episodes': len(catalog), 'catalog': catalog,
                'catalog_hash': pm.digest(catalog), 'source_video_id': video_id,
                'source_url': row['original_url'], 'canonical_video_id': canonical['video_id'],
                'drive_file_id': fid, 'sha256': canonical['sha256'], 'md5': canonical['md5'],
                'drive_parent_id': parent, 'canonical_series_id': canonical['series_id'],
                'alias_drive_ids': aliases,
                'file_size': canonical['file_size'], 'manifest_id': manifest_id,
                'manifest_sha256': hashlib.sha256(content).hexdigest(),
                'decode': 'passed', 'duration': float(probe['format']['duration']),
                'media': {'format': probe['format'].get('format_name', ''), 'video_codec': video.get('codec_name'),
                          'audio_codec': audio.get('codec_name'), 'width': video.get('width'), 'height': video.get('height')},
                'mapping_basis': 'source-marked complete archive manifest; not filename or SEO title',
                'rights_status': 'not certified by automated media inspection'}
    finally:
        if path is not None:
            Path(path).unlink(missing_ok=True)


def verify_dispatch(proof):
    """Fresh source-marked remote identity; local bytes are checked separately."""
    service = drive_sync.get_drive_service()
    meta = remote(service, proof['drive_file_id'])
    props = {'scrapper_platform': 'tiktok', 'scrapper_account': proof['channel_id'],
             'scrapper_video': proof['canonical_video_id']}
    if (meta.get('trashed') or meta.get('appProperties') != props
            or proof['drive_parent_id'] not in meta.get('parents', [])
            or meta.get('sha256Checksum') != proof['sha256'] or meta.get('md5Checksum') != proof['md5']
            or size(meta) != proof['file_size']):
        fail('remote canonical identity changed before dispatch')
