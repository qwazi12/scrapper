#!/usr/bin/env python3
"""Archive an inventoried YouTube Shorts channel using bounded RAM, not media files.

Uses the existing Drive credential. No queue, scheduling or publishing APIs.
Only metadata/receipts are persisted. Completed Drive IDs survive interruptions;
an incomplete in-memory transfer may need downloading again after a process exit.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
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
        if code:
            raise RuntimeError(safe_error(errors.decode(errors='replace')) or
                               f'Media subprocess exited {code}')
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
        inventory = json.loads(args.inventory.read_text())
        self.channel = inventory['channel_id']
        if not inventory.get('complete') or not re.fullmatch(r'UC[\w-]{22}', self.channel):
            raise ValueError('A complete canonical channel inventory is required')
        ids = [e['id'] for e in inventory['items']]
        if not ids or len(ids) != len(set(ids)) or any(not re.fullmatch(r'[\w-]{11}', v) for v in ids):
            raise ValueError('Invalid or duplicate inventory IDs')
        path = self.root / 'ledger.json'
        self.state = json.loads(path.read_text()) if path.exists() else {
            'created_at': now(), 'source_url': inventory['webpage_url'],
            'channel_id': self.channel, 'inventory_complete': True, 'folder_name': args.folder_name,
            'parent_id': args.parent_id, 'scope': 'Archive only; no queue, scheduling or publishing',
            'media_storage': 'Bounded memory and pipes only; no local media files',
            'items': {e['id']: {'video_id': e['id'], 'title': e['title'],
                       'original_url': 'https://www.youtube.com/shorts/' + e['id'],
                       'status': 'discovered'} for e in inventory['items']}}
        apply_destination(self.state, args, self.channel, self.root)
        if self.state['channel_id'] != self.channel or self.state['parent_id'] != args.parent_id:
            raise ValueError('Ledger does not match requested channel/parent')
        for e in inventory['items']:
            self.state['items'].setdefault(e['id'], {'video_id': e['id'], 'title': e['title'],
                'original_url': 'https://www.youtube.com/shorts/' + e['id'], 'status': 'discovered'})
        self.state.update(pid=os.getpid(), status='preflight', error=None,
                          concurrency=args.concurrency, max_video_bytes=args.max_video_bytes)
        self.save()

    def counts(self):
        items = list(self.state['items'].values())
        uploaded = [r for r in items if r['status'] == 'uploaded']
        return {'discovered': len(items), 'downloaded': sum(bool(r.get('sha256')) for r in items),
                'uploaded': len(uploaded), 'failed': sum(r['status'] == 'failed' for r in items),
                'remaining': len(items) - len(uploaded),
                'uploaded_bytes': sum(r.get('file_size', 0) for r in uploaded)}

    def save(self):
        self.state.update(counts=self.counts(), updated_at=now())
        atomic_json(self.root / 'ledger.json', self.state)

    def patch(self, vid, **values):
        with self.lock:
            self.state['items'][vid].update(values)
            self.save()

    def props(self, vid):
        return {'scrapper_platform': 'youtube', 'scrapper_account': self.channel, 'scrapper_video': vid}

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

    def manifest(self, service):
        with self.manifest_lock:
            with self.lock:
                if not self.state.get('manifest_id'):
                    self.state['manifest_id'] = service.files().generateIds(count=1, space='drive', type='files').execute()['ids'][0]
                    self.save()
                mid = self.state['manifest_id']
                data = json.dumps(self.state, ensure_ascii=False, indent=2).encode()
            props = {'scrapper_archive_manifest': 'youtube:' + self.channel}
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

    def metadata(self, row):
        cmd = [str(self.args.ytdlp_python), '-m', 'yt_dlp', '--ignore-config', '--no-cache-dir',
               '--no-playlist', '--dump-single-json', '--skip-download', '--no-progress',
               '--socket-timeout', '25', '--extractor-retries', '0', '--retries', '1',
               '--sleep-requests', '1', '--format', FORMAT, '--format-sort', 'res:1080']
        if self.args.brave_profile:
            cmd += ['--cookies-from-browser', 'brave:' + str(self.args.brave_profile)]
        for name in ('deno', 'node', 'bun'):
            if shutil.which(name):
                cmd += ['--js-runtimes', name + ':' + shutil.which(name)]
                break
        cmd += [row['original_url']]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(safe_error(result.stderr))
        info = json.loads(result.stdout)
        if info.get('id') != row['video_id'] or info.get('channel_id') != self.channel:
            raise ValueError('Source identity mismatch')
        if info.get('is_live') or not 0 < float(info.get('duration') or 0) <= 1200:
            raise ValueError('Unexpected live/unbounded source in Shorts archive')
        if info.get('has_drm'):
            raise ValueError('DRM content cannot be archived')
        return info

    def process(self, vid):
        if self.stop.is_set():
            return
        service = get_drive_service()
        # Allow slow chunk responses without reducing TLS/security checks.
        http = getattr(service._http, 'http', None)
        if http is not None:
            http.timeout = 180
        buffer = None
        try:
            row = dict(self.state['items'][vid])
            fid = row.get('drive_file_id')
            if fid:
                try:
                    meta = remote(service, fid)
                except HttpError as exc:
                    if exc.resp.status != 404:
                        raise
                else:
                    verify_identity(meta, self.state['folder_id'], self.props(vid))
                    if int(meta.get('size', 0)):
                        verify_receipt(meta, row, self.state['folder_id'], self.props(vid))
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
                '--format', FORMAT, '--format-sort', 'res:1080',
                '--downloader-args', 'ffmpeg_i:-rw_timeout 25000000',
                '--downloader-args', 'ffmpeg_o:-f mp4 -movflags frag_keyframe+empty_moov+default_base_moof',
                '--output', '-']
            buffer = collect_pipe(cmd, json.dumps(info).encode(), self.args.max_video_bytes, self.stop,
                                  progress=lambda size: self.patch(vid, in_memory_bytes=size,
                                                                  transfer_updated_at=now()))
            validation = validate_buffer(buffer, float(info['duration']))
            self.patch(vid, status='validated', **validation)
            row = dict(self.state['items'][vid])
            if not fid:
                # Reconcile identity-marked files before reserving a new ID.
                q = ("'" + self.state['folder_id'] + "' in parents and trashed=false and "
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
                name = (info.get('upload_date') or 'undated') + '_' + re.sub(r'[^\w .()-]', '_', info['title'])[:120].strip() + '_' + vid + '.mp4'
                self.patch(vid, drive_filename=name)
                service.files().create(body={'id': fid, 'name': name, 'mimeType': 'video/mp4',
                    'parents': [self.state['folder_id']], 'appProperties': self.props(vid)}, fields='id', **ALL).execute()
            self.patch(vid, status='uploading')
            receipt = upload_buffer(service, fid, buffer, row, self.state['folder_id'], self.props(vid), self.stop)
            self.patch(vid, status='uploaded', error=None, verified_at=now(),
                       remote_sha256_verified=receipt.get('sha256Checksum') == row['sha256'])
            buffer.close()
            buffer = None
            self.manifest(service)
            print(json.dumps({'verified_video_id': vid, 'counts': self.counts()}), flush=True)
        except Exception as exc:
            self.stop.set()
            self.patch(vid, status='failed' if self.state['items'][vid]['status'] != 'uploaded' else 'uploaded',
                       error=safe_error(exc))
            with self.lock:
                self.state['error'] = safe_error(exc)
                self.save()
            print(json.dumps({'error_video_id': vid, 'error': safe_error(exc)}), flush=True)
            raise
        finally:
            if buffer is not None:
                buffer.close()

    def run(self):
        service = get_drive_service()
        self.folder(service)
        pending = [vid for vid, row in self.state['items'].items() if row['status'] != 'uploaded']
        if self.args.max_videos:
            pending = pending[:self.args.max_videos]
        self.state.update(status='running', started_at=now())
        self.save()
        self.manifest(service)
        errors = []
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.concurrency) as pool:
            futures = [pool.submit(self.process, vid) for vid in pending]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    errors.append(safe_error(exc))
        self.state.update(status='blocked' if errors else ('complete' if not self.counts()['remaining'] else
                          'stopped' if self.stop.is_set() else 'partial'), finished_at=now())
        self.save()
        self.manifest(service)
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
    parser.add_argument('--concurrency', type=int, choices=(1, 2), default=2)
    parser.add_argument('--max-video-bytes', type=int, default=256 * 1024 * 1024)
    parser.add_argument('--max-videos', type=int)
    args = parser.parse_args()
    if not 1024 <= args.max_video_bytes <= 256 * 1024 * 1024 or args.max_videos is not None and args.max_videos < 1:
        parser.error('Use a positive video count and a media RAM cap between 1 KiB and 256 MiB')
    # Preserve the venv launcher symlink: resolving it selects the global Python
    # and can silently load a different downloader/dependency installation.
    args.ytdlp_python = Path(os.path.abspath(args.ytdlp_python))
    return Archive(args).run()


if __name__ == '__main__':
    raise SystemExit(main())
