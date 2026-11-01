#!/usr/bin/env python3
"""Archive an inventoried YouTube/TikTok source using bounded RAM, not media files.

Uses the existing Drive credential. No queue, scheduling or publishing APIs.
Only metadata/receipts are persisted. Completed Drive IDs survive interruptions;
an incomplete in-memory transfer may need downloading again after a process exit.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import email.utils
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.drive_sync import get_drive_service
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

ALL = {'supportsAllDrives': True}
FORMAT = 'bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/b[ext=mp4]/b'


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_error(value):
    return re.sub(r'https?://[^\s<>]+', '[remote URL]', str(value))[-1800:]


def atomic_json(path, data):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(0o600)
    tmp.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class SourceUnavailable(ValueError):
    """A specific removed/private/paid source; log and continue without unlocking."""


def source_contract(inventory):
    platform = inventory.get('platform', 'youtube')
    channel = inventory['channel_id']
    if platform == 'youtube':
        valid = bool(re.fullmatch(r'UC[\w-]{22}', channel))
        pattern = r'[\w-]{11}'
    elif platform == 'tiktok':
        valid = (bool(re.fullmatch(r'MS4wLjABAAAA[A-Za-z0-9_-]{20,150}', channel)) and
                 bool(re.fullmatch(r'\d{19}', inventory.get('account_id', ''))) and
                 bool(re.fullmatch(r'[A-Za-z0-9_.]{1,30}', inventory.get('handle', ''))))
        pattern = r'\d{19}'
    else:
        raise ValueError('Unsupported archive platform')
    if not inventory.get('complete') or not valid:
        raise ValueError('A complete canonical channel inventory is required')
    ids = [e['id'] for e in inventory['items']]
    if not ids or len(ids) != len(set(ids)) or any(not re.fullmatch(pattern, v) for v in ids):
        raise ValueError('Invalid or duplicate inventory IDs')
    return platform


def source_row(inventory, entry, platform):
    vid = entry['id']
    url = ('https://www.youtube.com/shorts/' + vid if platform == 'youtube' else
           f"https://www.tiktok.com/@{inventory['handle']}/video/{vid}")
    return {**{k: entry[k] for k in ('series_id', 'series_title', 'episode_number',
                                     'source_preview_flag', 'source_limited_free_flag',
                                     'restricted') if k in entry},
            'video_id': vid, 'title': entry['title'], 'original_url': url, 'status': 'discovered'}


def apply_destination(state, args, channel, root):
    """Honor an owner-requested, verified folder relocation on the next resume.

    An active worker retains the same child folder ID while its parent is moved.
    Keep the override outside its mutable ledger so a checkpoint cannot undo it.
    The normal Drive folder identity/parent check still runs before transfers.
    """
    path = root / 'destination.json'
    if not path.exists():
        return
    destination = json.loads(path.read_text())
    if (destination.get('channel_id') != channel or state.get('channel_id') != channel or
            not state.get('folder_id') or destination.get('folder_id') != state['folder_id'] or
            not destination.get('verified_at') or
            not re.fullmatch(r'[A-Za-z0-9_-]{10,128}', destination.get('parent_id', ''))):
        raise ValueError('Destination relocation does not match this archive')
    args.parent_id = destination['parent_id']
    state['parent_id'] = destination['parent_id']
    state['holding_space'] = destination.get('holding_space')


def collect_pipe(command, payload, bound, stop, timeout=900, progress=None):
    """Read subprocess media into RAM with a hard cap; never spool to disk."""
    child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    errors = bytearray()

    def drain_errors():
        while chunk := child.stderr.read(4096):
            errors.extend(chunk)
            if len(errors) > 32768:
                del errors[:-32768]

    error_thread = threading.Thread(target=drain_errors, daemon=True)
    error_thread.start()
    buffer = io.BytesIO()
    deadline = time.monotonic() + timeout
    last_progress = time.monotonic()
    selector = selectors.DefaultSelector()
    terminated = False
    try:
        # yt-dlp reads the entire JSON input before starting a download.
        if payload:
            child.stdin.write(payload)
        child.stdin.close()
        selector.register(child.stdout, selectors.EVENT_READ)
        while True:
            if stop.is_set():
                raise RuntimeError('Owner/shared cancellation; no local media file retained')
            if time.monotonic() > deadline:
                raise TimeoutError('In-memory source transfer timed out')
            if not selector.select(.25):
                continue
            chunk = os.read(child.stdout.fileno(), 1024 * 1024)
            if not chunk:
                break
            if buffer.tell() + len(chunk) > bound:
                raise ValueError('Video exceeds configured in-memory size bound')
            buffer.write(chunk)
            if progress is not None and time.monotonic() - last_progress >= 5:
                progress(buffer.tell())
                last_progress = time.monotonic()
        code = child.wait(timeout=10)
        error_thread.join(timeout=2)
        error_text = errors.decode(errors='replace')
        if code:
            raise RuntimeError(safe_error(error_text) or
                               f'Media subprocess exited {code}')
        # ffmpeg can return success after a transport interruption and a short
        # fragment. Preserve the timeout classification before validation, so
        # the bounded cooldown can retry instead of mislabeling it as bad media.
        if error_text and failure_kind(RuntimeError(error_text)) == 'transient':
            raise TimeoutError('Source transport interrupted: ' + safe_error(error_text))
        if not buffer.tell():
            raise ValueError('Empty source video')
        buffer.seek(0)
        return buffer
    except BaseException:
        # yt-dlp can spawn ffmpeg. Kill this exact isolated process group even
        # when its parent has already exited, so no orphan transfer survives.
        try:
            os.killpg(child.pid, signal.SIGTERM)
            terminated = True
        except ProcessLookupError:
            pass
        except PermissionError:
            child.terminate()
            terminated = True
        buffer.close()
        raise
    finally:
        selector.close()
        if child.poll() is None:
            if not terminated:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    child.kill()
                child.wait()
        error_thread.join(timeout=2)


def validate_buffer(buffer, declared_duration):
    with buffer.getbuffer() as data:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                '-show_packets', '-show_entries',
                                'packet=pts_time,duration_time,stream_index:stream=index,codec_type,width,height:format=duration',
                                '-of', 'json', 'pipe:0'], input=data, capture_output=True,
                               timeout=45)
        if probe.returncode:
            raise ValueError('In-memory ffprobe failed: ' + safe_error(probe.stderr.decode()))
        info = json.loads(probe.stdout)
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        if not 0 < video.get('width', 0) * video.get('height', 0) <= 20_000_000:
            raise ValueError('Invalid video dimensions')
        if not any(s['codec_type'] == 'audio' for s in info['streams']):
            raise ValueError('Missing source audio')
        # A non-seekable fragmented MP4 header can describe only its first
        # fragment. Read packet timestamps to EOF and validate both tracks.
        durations = {}
        for stream in info['streams']:
            if stream['codec_type'] not in ('video', 'audio'):
                continue
            packets = [p for p in info['packets'] if p['stream_index'] == stream['index'] and 'pts_time' in p]
            start = min(float(p['pts_time']) for p in packets)
            end = max(float(p['pts_time']) + float(p.get('duration_time', 0)) for p in packets)
            durations[stream['codec_type']] = end - start
        duration = durations['video']
        if any(value <= 0 or abs(value - declared_duration) > max(2, declared_duration * .01)
               for value in durations.values()):
            raise ValueError(f'Source and output duration differ: source={declared_duration}, '
                             f'tracks={durations}; refuse truncated upload')
        decoded = subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-err_detect', 'explode',
                                  '-threads', '2', '-i', 'pipe:0', '-map', '0:v:0',
                                  '-map', '0:a:0', '-f', 'null', '-'], input=data,
                                 capture_output=True, timeout=180)
        if decoded.returncode:
            raise ValueError('Full in-memory decode failed: ' + safe_error(decoded.stderr.decode()))
        return {'file_size': len(data), 'md5': hashlib.md5(data).hexdigest(),
                'sha256': hashlib.sha256(data).hexdigest(), 'duration': duration,
                'width': video['width'], 'height': video['height'],
                'track_durations': durations, 'full_decode_passed': True, 'file_format': 'mp4'}


def verify_identity(meta, parent, props):
    if meta.get('trashed') or parent not in meta.get('parents', []) or meta.get('appProperties') != props:
        raise ValueError('Remote identity or parent mismatch; no overwrite')


def verify_receipt(meta, row, parent, props):
    verify_identity(meta, parent, props)
    if int(meta.get('size', -1)) != row['file_size'] or meta.get('md5Checksum') != row['md5']:
        raise ValueError('Remote checksum/size mismatch; no overwrite')
    if meta.get('sha256Checksum') and meta['sha256Checksum'] != row['sha256']:
        raise ValueError('Remote SHA256 mismatch')


def remote(service, fid):
    return service.files().get(fileId=fid, fields='id,name,size,md5Checksum,sha256Checksum,trashed,parents,appProperties,mimeType', **ALL).execute()


def retryable(exc):
    import httplib2
    from google.auth.exceptions import TransportError
    return (isinstance(exc, HttpError) and exc.resp.status in (408, 500, 502, 503, 504) or
            isinstance(exc, (OSError, TimeoutError, httplib2.HttpLib2Error, TransportError)))


class TransferFailure(RuntimeError):
    def __init__(self, original, kind):
        super().__init__(safe_error(original))
        self.kind = kind
        self.original = original


def failure_kind(exc):
    """Retry ordinary transport failures, never authentication or integrity failures."""
    if isinstance(exc, TransferFailure):
        return exc.kind
    text = str(exc).lower()
    if any(s in text for s in ('shared cancellation', 'owner/shared cancellation', 'archive cancelled')):
        return 'cancelled'
    if isinstance(exc, FileNotFoundError) or any(s in text for s in (
            'captcha', 'not a bot', 'sign in', 'log in', 'login required', 'age-restrict',
            'drm', 'checksum', 'identity mismatch', 'quota', 'permission', 'access denied',
            'http error 401', 'http error 403', 'duration differ',
            'decode failed', 'in-memory size bound')):
        return 'manual'
    if isinstance(exc, HttpError):
        if exc.resp.status == 429:
            return 'rate_limited'
        return 'transient' if exc.resp.status in (408, 500, 502, 503, 504) else 'manual'
    if 'http error 429' in text or 'server returned 429' in text:
        return 'rate_limited'
    if isinstance(exc, (TimeoutError, ConnectionError, subprocess.TimeoutExpired)):
        return 'transient'
    if any(s in text for s in ('timed out', 'timeout', 'connection reset', 'connection aborted',
                              'connection refused', 'remote end closed', 'temporary failure',
                              'input/output error',
                              'http error 408', 'http error 500', 'http error 502',
                              'http error 503', 'http error 504', 'server returned 5',
                              'page needs to be reloaded')):
        return 'transient'
    return 'manual'


def retry_after_seconds(exc):
    if isinstance(exc, TransferFailure):
        exc = exc.original
    if not isinstance(exc, HttpError):
        return 0
    value = exc.resp.get('retry-after', '')
    try:
        return max(0, int(value))
    except (ValueError, TypeError):
        try:
            date = email.utils.parsedate_to_datetime(value)
            return max(0, (date - dt.datetime.now(dt.timezone.utc)).total_seconds())
        except (ValueError, TypeError, AttributeError):
            return 0


def upload_buffer(service, fid, buffer, row, parent, props, stop):
    """Every retry reconciles the same durable ID; never create duplicate copies."""
    for attempt in range(3):
        meta = remote(service, fid)
        verify_identity(meta, parent, props)
        if int(meta.get('size', 0)):
            verify_receipt(meta, row, parent, props)
            return meta
        buffer.seek(0)
        request = service.files().update(fileId=fid, media_body=MediaIoBaseUpload(
            buffer, mimetype='video/mp4', chunksize=4 * 1024 * 1024, resumable=True),
            fields='id', **ALL)
        try:
            response = None
            while response is None:
                if stop.is_set():
                    raise RuntimeError('Shared cancellation; incomplete Drive ID retained')
                _, response = request.next_chunk(num_retries=2)
        except Exception as exc:
            # If this read fails, stop instead of blindly resending uncertain bytes.
            meta = remote(service, fid)
            verify_identity(meta, parent, props)
            if int(meta.get('size', 0)):
                verify_receipt(meta, row, parent, props)
                return meta
            if attempt == 2 or not retryable(exc) or stop.wait((5, 15)[min(attempt, 1)]):
                raise
            continue
        meta = remote(service, fid)
        verify_receipt(meta, row, parent, props)
        return meta


class Archive:
    def __init__(self, args):
        self.args = args
        self.root = args.state_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.lock_file = (self.root / 'archive.lock').open('a')
        fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock, self.manifest_lock, self.stop = threading.RLock(), threading.Lock(), threading.Event()
        self.owner_stop = threading.Event()
        inventory = json.loads(args.inventory.read_text())
        self.channel = inventory['channel_id']
        self.platform = source_contract(inventory)
        self.account_id, self.handle = inventory.get('account_id'), inventory.get('handle')
        self.format = 'b[ext=mp4]/b' if self.platform == 'tiktok' else FORMAT
        self.last_snapshot, self.last_manifest = 0, 0
        self.rows_root = self.root / 'receipts'
        self.journal = len(inventory['items']) >= 1000 or self.rows_root.exists()
        if self.journal:
            self.rows_root.mkdir(mode=0o700, exist_ok=True)
        path = self.root / 'ledger.json'
        self.state = json.loads(path.read_text()) if path.exists() else {
            'created_at': now(), 'source_url': inventory['webpage_url'],
            'channel_id': self.channel, 'platform': self.platform,
            'account_id': self.account_id, 'handle': self.handle,
            'inventory_complete': True, 'folder_name': args.folder_name,
            'inventory_coverage': {k: inventory[k] for k in ('series_count', 'listed_episode_slots',
                'missing_episode_slots', 'all_episode_slots_present', 'cross_series_duplicate_ids') if k in inventory},
            'parent_id': args.parent_id, 'scope': 'Archive only; no queue, scheduling or publishing',
            'media_storage': 'Bounded memory and pipes only; no local media files',
            'items': {e['id']: source_row(inventory, e, self.platform) for e in inventory['items']}}
        apply_destination(self.state, args, self.channel, self.root)
        if (self.state['channel_id'] != self.channel or self.state['parent_id'] != args.parent_id or
                self.state.get('platform', 'youtube') != self.platform or
                self.state.get('account_id') != self.account_id):
            raise ValueError('Ledger does not match requested channel/parent')
        for e in inventory['items']:
            self.state['items'].setdefault(e['id'], source_row(inventory, e, self.platform))
        if self.journal:
            for receipt in self.rows_root.glob('*.json'):
                data = json.loads(receipt.read_text())
                vid, row = receipt.stem, data['row']
                if (data['channel_id'] != self.channel or data['platform'] != self.platform or
                        vid not in self.state['items'] or row.get('video_id') != vid or
                        row.get('original_url') != self.state['items'][vid]['original_url']):
                    raise ValueError('Durable row receipt identity mismatch')
                self.state['items'][vid] = row
        self.state.update(pid=os.getpid(), status='preflight', error=None,
                          concurrency=args.concurrency, max_video_bytes=args.max_video_bytes)
        self.save()

    def counts(self):
        items = list(self.state['items'].values())
        uploaded = [r for r in items if r['status'] == 'uploaded']
        skipped = sum(r['status'] == 'unavailable' for r in items)
        return {'discovered': len(items), 'downloaded': sum(bool(r.get('sha256')) for r in items),
                'uploaded': len(uploaded), 'failed': sum(r['status'] == 'failed' for r in items),
                'skipped': skipped, 'remaining': len(items) - len(uploaded) - skipped,
                'uploaded_bytes': sum(r.get('file_size', 0) for r in uploaded)}

    def save(self, force=True):
        self.state.update(counts=self.counts(), updated_at=now())
        if getattr(self, 'journal', False) and not force and time.monotonic() - self.last_snapshot < 10:
            return
        atomic_json(self.root / 'ledger.json', self.state)
        self.last_snapshot = time.monotonic()

    def patch(self, vid, **values):
        with self.lock:
            self.state['items'][vid].update(values)
            if getattr(self, 'journal', False):
                atomic_json(self.rows_root / (vid + '.json'), {
                    'channel_id': self.channel, 'platform': self.platform,
                    'row': self.state['items'][vid]})
            self.save(force=False)

    def props(self, vid):
        return {'scrapper_platform': self.platform, 'scrapper_account': self.channel, 'scrapper_video': vid}

    def folder(self, service):
        if not self.state.get('folder_id'):
            # Reuse an exact source-marked folder, never a same-name unrelated folder.
            q = ("'" + self.args.parent_id + "' in parents and trashed=false and "
                 "appProperties has { key='scrapper_archive_channel' and value='" + self.channel + "' }")
            result = service.files().list(q=q, pageSize=100, fields='incompleteSearch,nextPageToken,files(id)',
                includeItemsFromAllDrives=True, **ALL).execute()
            if result.get('incompleteSearch') or result.get('nextPageToken') or len(result.get('files', [])) > 1:
                raise ValueError('Ambiguous folder search')
            self.state['folder_id'] = (result['files'][0]['id'] if result.get('files') else
                service.files().generateIds(count=1, space='drive', type='files').execute()['ids'][0])
            self.save()
        fid = self.state['folder_id']
        try:
            folder = remote(service, fid)
        except HttpError as exc:
            if exc.resp.status != 404:
                raise
            folder = service.files().create(body={'id': fid, 'name': self.args.folder_name,
                'mimeType': 'application/vnd.google-apps.folder', 'parents': [self.args.parent_id],
                'appProperties': {'scrapper_archive_channel': self.channel}}, fields='id', **ALL).execute()
            folder = remote(service, fid)
        verify_identity(folder, self.args.parent_id, {'scrapper_archive_channel': self.channel})
        self.state['folder_url'] = 'https://drive.google.com/drive/folders/' + fid
        self.save()

    def manifest(self, service, force=True):
        with self.manifest_lock:
            if self.journal and not force and time.monotonic() - self.last_manifest < 60:
                return
            with self.lock:
                if not self.state.get('manifest_id'):
                    self.state['manifest_id'] = service.files().generateIds(count=1, space='drive', type='files').execute()['ids'][0]
                    self.save()
                mid = self.state['manifest_id']
                data = json.dumps(self.state, ensure_ascii=False, indent=2).encode()
            props = {'scrapper_archive_manifest': self.platform + ':' + self.channel}
            try:
                m = remote(service, mid)
            except HttpError as exc:
                if exc.resp.status != 404:
                    raise
                service.files().create(body={'id': mid, 'name': 'archive-manifest.json',
                    'mimeType': 'application/json', 'parents': [self.state['folder_id']],
                    'appProperties': props}, fields='id', **ALL).execute()
                m = remote(service, mid)
            verify_identity(m, self.state['folder_id'], props)
            service.files().update(fileId=mid, media_body=MediaIoBaseUpload(io.BytesIO(data),
                mimetype='application/json'), fields='id', **ALL).execute()
            m = remote(service, mid)
            if m.get('md5Checksum') != hashlib.md5(data).hexdigest():
                raise ValueError('Manifest upload checksum mismatch')
            self.last_manifest = time.monotonic()

    def media_folder(self, service, row):
        """Preserve TikTok's series grouping with independently marked folders."""
        sid = row.get('series_id')
        if not sid:
            return self.state['folder_id']
        if self.platform != 'tiktok' or not re.fullmatch(r'\d{19}', sid):
            raise ValueError('Invalid series folder identity')
        props = {'scrapper_archive_channel': self.channel, 'scrapper_archive_series': sid}
        with self.lock:
            series = self.state.setdefault('series_folders', {})
            if sid not in series:
                q = ("'" + self.state['folder_id'] + "' in parents and trashed=false and "
                     "appProperties has { key='scrapper_archive_series' and value='" + sid + "' }")
                result = service.files().list(q=q, pageSize=100,
                    fields='incompleteSearch,nextPageToken,files(id)',
                    includeItemsFromAllDrives=True, **ALL).execute()
                if result.get('incompleteSearch') or result.get('nextPageToken') or len(result.get('files', [])) > 1:
                    raise ValueError('Ambiguous series folder search')
                fid = (result['files'][0]['id'] if result.get('files') else
                       service.files().generateIds(count=1, space='drive', type='files').execute()['ids'][0])
                name = re.sub(r'[^\w .()-]', '_', row['series_title'])[:140].strip() + ' [' + sid + ']'
                series[sid] = {'folder_id': fid, 'name': name, 'title': row['series_title']}
                self.save()  # reserve identity durably before any cloud creation
            entry = series[sid]
            fid = entry['folder_id']
            try:
                folder = remote(service, fid)
            except HttpError as exc:
                if exc.resp.status != 404:
                    raise
                service.files().create(body={'id': fid, 'name': entry['name'],
                    'mimeType': 'application/vnd.google-apps.folder',
                    'parents': [self.state['folder_id']], 'appProperties': props},
                    fields='id', **ALL).execute()
                folder = remote(service, fid)
            verify_identity(folder, self.state['folder_id'], props)
            if folder.get('mimeType') != 'application/vnd.google-apps.folder':
                raise ValueError('Series destination is not a folder')
            return fid

    def metadata(self, row):
        if self.platform == 'tiktok' and row.get('series_id'):
            if not self.args.brave_profile:
                raise ValueError('Normal owner browser profile is required for this series archive')
            cmd = [str(self.args.ytdlp_python), str(Path(__file__).with_name('tiktok_drama_metadata.py')),
                   '--video-id', row['video_id'], '--series-id', row['series_id'],
                   '--episode-number', str(row['episode_number']), '--uid', self.account_id,
                   '--channel-id', self.channel, '--handle', self.handle,
                   '--brave-profile', str(self.args.brave_profile)]
        else:
            cmd = self.page_metadata_command(row)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode:
            if self.platform == 'tiktok' and any(t in result.stderr.lower() for t in (
                    'this video is private', 'video not available', 'video has been removed',
                    'paid content is not supported')):
                raise SourceUnavailable(safe_error(result.stderr))
            raise RuntimeError(safe_error(result.stderr))
        info = json.loads(result.stdout)
        if info.get('id') != row['video_id'] or info.get('channel_id') != self.channel:
            raise ValueError('Source identity mismatch')
        if self.platform == 'tiktok' and (info.get('uploader_id') != self.account_id or
                                         info.get('uploader') != self.handle):
            raise ValueError('TikTok uploader identity mismatch')
        maximum = 7200 if self.platform == 'tiktok' else 1200
        if info.get('is_live') or not 0 < float(info.get('duration') or 0) <= maximum:
            raise ValueError('Unexpected live/unbounded archive source')
        if info.get('has_drm'):
            raise ValueError('DRM content cannot be archived')
        return info

    def page_metadata_command(self, row):
        client = ([str(Path(__file__).with_name('tiktok_access.py'))] if self.platform == 'tiktok'
                  else ['-m', 'yt_dlp'])
        cmd = [str(self.args.ytdlp_python), *client, '--ignore-config', '--no-cache-dir',
               '--no-playlist', '--dump-single-json', '--skip-download', '--no-progress',
               '--socket-timeout', '25', '--extractor-retries', '0', '--retries', '1',
               '--sleep-requests', '1', '--format', self.format, '--format-sort', 'res:1080']
        if self.args.brave_profile:
            cmd += ['--cookies-from-browser', 'brave:' + str(self.args.brave_profile)]
        for name in ('deno', 'node', 'bun'):
            if shutil.which(name):
                cmd += ['--js-runtimes', name + ':' + shutil.which(name)]
                break
        cmd += [row['original_url']]
        return cmd

    def process(self, vid):
        if self.stop.is_set() or self.owner_stop.is_set():
            return
        buffer = None
        try:
            service = get_drive_service()
            # Allow slow chunk responses without reducing TLS/security checks.
            http = getattr(service._http, 'http', None)
            if http is not None:
                http.timeout = 180
            row = dict(self.state['items'][vid])
            if row.get('restricted'):
                raise SourceUnavailable('Inventory marks this episode restricted; no access attempt')
            parent = self.media_folder(service, row)
            self.patch(vid, drive_parent_id=parent)
            fid = row.get('drive_file_id')
            if fid:
                try:
                    meta = remote(service, fid)
                except HttpError as exc:
                    if exc.resp.status != 404:
                        raise
                else:
                    verify_identity(meta, parent, self.props(vid))
                    if int(meta.get('size', 0)):
                        verify_receipt(meta, row, parent, self.props(vid))
                        self.patch(vid, status='uploaded', error=None, verified_at=now())
                        return
            self.patch(vid, status='extracting', error=None)
            info = self.metadata(row)
            if self.stop.is_set():
                return
            self.patch(vid, status='downloading', title=info['title'], upload_date=info.get('upload_date'),
                       source_duration=info['duration'], selected_format=info['format_id'])
            cmd = [str(self.args.ytdlp_python), '-m', 'yt_dlp', '--ignore-config', '--no-cache-dir',
                '--load-info-json', '-', '--no-simulate', '--no-playlist', '--quiet', '--no-progress',
                '--no-part', '--retries', '1', '--socket-timeout', '25', '--downloader', 'ffmpeg',
                '--format', self.format, '--format-sort', 'res:1080',
                '--downloader-args', 'ffmpeg_i:-rw_timeout 25000000',
                '--downloader-args', 'ffmpeg_o:-loglevel error -f mp4 -movflags frag_keyframe+empty_moov+default_base_moof',
                '--output', '-']
            buffer = collect_pipe(cmd, json.dumps(info).encode(), self.args.max_video_bytes, self.stop,
                                  progress=lambda size: self.patch(vid, in_memory_bytes=size,
                                                                  transfer_updated_at=now()))
            validation = validate_buffer(buffer, float(info['duration']))
            self.patch(vid, status='validated', **validation)
            row = dict(self.state['items'][vid])
            if not fid:
                # Reconcile identity-marked files before reserving a new ID.
                q = ("'" + parent + "' in parents and trashed=false and "
                     "appProperties has { key='scrapper_account' and value='" + self.channel + "' } and "
                     "appProperties has { key='scrapper_video' and value='" + vid + "' }")
                found = service.files().list(q=q, pageSize=100, fields='incompleteSearch,nextPageToken,files(id)',
                    includeItemsFromAllDrives=True, **ALL).execute()
                if found.get('incompleteSearch') or found.get('nextPageToken') or len(found.get('files', [])) > 1:
                    raise ValueError('Ambiguous video identity search')
                fid = (found['files'][0]['id'] if found.get('files') else
                       service.files().generateIds(count=1, space='drive', type='files').execute()['ids'][0])
                self.patch(vid, drive_file_id=fid)
            try:
                remote(service, fid)
            except HttpError as exc:
                if exc.resp.status != 404:
                    raise
                label = (f"{row['series_title']}_EP{row['episode_number']:03d}_" if row.get('series_id') else '')
                name = ((info.get('upload_date') or 'undated') + '_' +
                        re.sub(r'[^\w .()-]', '_', label + info['title'])[:160].strip() + '_' + vid + '.mp4')
                self.patch(vid, drive_filename=name)
                service.files().create(body={'id': fid, 'name': name, 'mimeType': 'video/mp4',
                    'parents': [parent], 'appProperties': self.props(vid)}, fields='id', **ALL).execute()
            self.patch(vid, status='uploading')
            receipt = upload_buffer(service, fid, buffer, row, parent, self.props(vid), self.stop)
            self.patch(vid, status='uploaded', error=None, verified_at=now(),
                       transient_attempts=0,
                       remote_sha256_verified=receipt.get('sha256Checksum') == row['sha256'])
            buffer.close()
            buffer = None
            self.manifest(service, force=False)
            print(json.dumps({'verified_video_id': vid, 'counts': self.counts()}), flush=True)
        except SourceUnavailable as exc:
            self.patch(vid, status='unavailable', error=safe_error(exc), recovery='skip_restricted')
            print(json.dumps({'unavailable_video_id': vid, 'reason': safe_error(exc)}), flush=True)
        except Exception as exc:
            kind = failure_kind(exc)
            self.stop.set()
            values = {'error': safe_error(exc), 'recovery': kind}
            if kind in ('transient', 'rate_limited'):
                values['transient_attempts'] = self.state['items'][vid].get('transient_attempts', 0) + 1
            if self.state['items'][vid]['status'] != 'uploaded':
                values['status'] = 'interrupted' if kind == 'cancelled' else 'failed'
            self.patch(vid, **values)
            with self.lock:
                if kind != 'cancelled' or not self.state.get('error'):
                    self.state['error'] = safe_error(exc)
                self.save()
            print(json.dumps({'error_video_id': vid, 'error': safe_error(exc), 'recovery': kind}), flush=True)
            raise TransferFailure(exc, kind) from exc
        finally:
            if buffer is not None:
                buffer.close()

    def wait_cooldown(self, seconds, reason):
        deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
        self.state.update(status='cooldown', next_retry_at=deadline.isoformat(),
                          cooldown_reason=reason, cooldown_seconds=seconds)
        self.save()
        print(json.dumps({'status': 'cooldown', 'retry_at': deadline.isoformat(),
                          'seconds': seconds, 'reason': reason}), flush=True)
        # The shared failure event is already set; only owner cancellation may
        # interrupt this wait. No network requests occur during the cooldown.
        if self.owner_stop.wait(seconds):
            return False
        self.stop.clear()
        self.state.update(status='running', next_retry_at=None, error=None)
        self.save()
        return True

    def recover_call(self, callback, stage):
        """Apply the same bounded cooldown to setup and final manifest writes."""
        for attempt in range(self.args.resume_retries + 1):
            try:
                return callback()
            except Exception as exc:
                kind = failure_kind(exc)
                if (kind not in ('transient', 'rate_limited') or attempt == self.args.resume_retries or
                        self.owner_stop.is_set()):
                    raise
                seconds = max(self.args.cooldown_seconds * 3 ** attempt, retry_after_seconds(exc))
                if kind == 'rate_limited':
                    seconds = max(seconds, self.args.rate_cooldown_seconds * 2 ** attempt)
                self.state['recovery_stage'] = stage
                if not self.wait_cooldown(seconds, safe_error(exc)):
                    raise TransferFailure(RuntimeError('Shared cancellation during cooldown'), 'cancelled') from exc

    def drain(self, target):
        """Bounded batches permit adaptive concurrency and resumable cooldowns."""
        concurrency = self.args.concurrency
        while not self.owner_stop.is_set():
            pending = [vid for vid in target if self.state['items'][vid]['status'] not in ('uploaded', 'unavailable')]
            if not pending:
                return []
            self.state.update(active_concurrency=concurrency, status='running')
            self.save()
            errors = []
            batch = pending[:max(25, concurrency)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(self.process, vid): vid for vid in batch}
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as exc:
                        errors.append((futures[future], exc))
            if self.owner_stop.is_set():
                return []
            if not errors:
                # Restore the requested cap after a clean lower-concurrency batch.
                concurrency = self.args.concurrency
                continue
            hard = [(vid, exc) for vid, exc in errors if failure_kind(exc) not in ('transient', 'rate_limited', 'cancelled')]
            recoverable = [(vid, exc) for vid, exc in errors if failure_kind(exc) in ('transient', 'rate_limited')]
            exhausted = [vid for vid, _ in recoverable
                         if self.state['items'][vid].get('transient_attempts', 0) > self.args.resume_retries]
            if hard or not recoverable or exhausted:
                return [safe_error(exc) for _, exc in errors]
            attempt = max(self.state['items'][vid].get('transient_attempts', 1) for vid, _ in recoverable)
            seconds = max(self.args.cooldown_seconds * 3 ** (attempt - 1),
                          max(retry_after_seconds(exc) for _, exc in recoverable))
            concurrency = min(concurrency, 2)
            if any(failure_kind(exc) == 'rate_limited' for _, exc in recoverable):
                seconds = max(seconds, self.args.rate_cooldown_seconds * 2 ** (attempt - 1))
                concurrency = 1
            self.state['resume_concurrency'] = concurrency
            self.state['cooldown_restarts'] = self.state.get('cooldown_restarts', 0) + 1
            if not self.wait_cooldown(seconds, '; '.join(safe_error(exc) for _, exc in recoverable)):
                return []
        return []

    def run(self):
        pending = [vid for vid, row in self.state['items'].items() if row['status'] not in ('uploaded', 'unavailable')]
        if self.args.max_videos:
            pending = pending[:self.args.max_videos]
        self.state.update(status='running', started_at=now())
        self.save()
        def cancel(*_):
            self.owner_stop.set()
            self.stop.set()
        signal.signal(signal.SIGTERM, cancel)
        signal.signal(signal.SIGINT, cancel)
        # SIGUSR1 drains active transfers without discarding their RAM buffers.
        signal.signal(signal.SIGUSR1, lambda *_: self.owner_stop.set())
        retry_at = self.state.get('next_retry_at')
        if retry_at:
            remaining = (dt.datetime.fromisoformat(retry_at) - dt.datetime.now(dt.timezone.utc)).total_seconds()
            if remaining > 0:
                self.wait_cooldown(remaining, 'Honoring persisted cooldown before resume')
        def prepare():
            service = get_drive_service()
            self.folder(service)
            self.manifest(service)
            return service
        if self.owner_stop.is_set():
            self.state.update(status='stopped', finished_at=now())
            self.save()
            return 0
        service = self.recover_call(prepare, 'Drive setup')
        errors = self.drain(pending)
        final_status = 'blocked' if errors else ('complete' if not self.counts()['remaining'] else
                       'stopped' if self.owner_stop.is_set() else 'partial')
        if final_status == 'complete' and (self.counts()['skipped'] or
                self.state.get('inventory_coverage', {}).get('missing_episode_slots')):
            final_status = 'complete_with_unavailable'
        def finalize():
            self.state.update(status=final_status, finished_at=now())
            self.save()
            self.manifest(service)
        self.recover_call(finalize, 'final manifest')
        print(json.dumps({k: self.state[k] for k in ('status', 'counts', 'folder_url', 'error')}), flush=True)
        return 2 if errors else 0


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--parent-id', required=True)
    parser.add_argument('--folder-name', required=True)
    parser.add_argument('--ytdlp-python', type=Path, required=True)
    parser.add_argument('--brave-profile', type=Path)
    parser.add_argument('--concurrency', type=int, choices=(1, 2, 3, 4), default=2)
    parser.add_argument('--max-video-bytes', type=int, default=256 * 1024 * 1024)
    parser.add_argument('--ram-budget-mib', type=int, default=1024)
    parser.add_argument('--cooldown-seconds', type=int, default=60)
    parser.add_argument('--rate-cooldown-seconds', type=int, default=900)
    parser.add_argument('--resume-retries', type=int, default=3)
    parser.add_argument('--max-videos', type=int)
    args = parser.parse_args()
    if not 1024 <= args.max_video_bytes <= 256 * 1024 * 1024 or args.max_videos is not None and args.max_videos < 1:
        parser.error('Use a positive video count and a media RAM cap between 1 KiB and 256 MiB')
    if (not 1 <= args.ram_budget_mib <= 1024 or
            args.max_video_bytes * args.concurrency > args.ram_budget_mib * 1024 * 1024):
        parser.error('Concurrent media buffers must fit the configured RAM budget (at most 1 GiB)')
    if not 30 <= args.cooldown_seconds <= 600 or not 0 <= args.resume_retries <= 3:
        parser.error('Use 30–600 seconds for base cooldown and at most three automatic resumes per item')
    if not 300 <= args.rate_cooldown_seconds <= 3600:
        parser.error('Use at least 5 minutes for rate-limit cooldowns (default 15 minutes)')
    # Preserve the venv launcher symlink: resolving it selects the global Python
    # and can silently load a different downloader/dependency installation.
    args.ytdlp_python = Path(os.path.abspath(args.ytdlp_python))
    archive = Archive(args)
    try:
        return archive.run()
    except Exception as exc:
        archive.stop.set()
        archive.state.update(status='stopped' if archive.owner_stop.is_set() else 'blocked',
                             error=safe_error(exc), finished_at=now())
        archive.save()
        print(json.dumps({'status': archive.state['status'], 'error': safe_error(exc)}), flush=True)
        return 0 if archive.owner_stop.is_set() else 2


if __name__ == '__main__':
    raise SystemExit(main())
