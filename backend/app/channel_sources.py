"""Public YouTube metadata/media adapter. No cookies, proxies or challenge bypass.

Channel tabs use yt-dlp's complete continuation pagination. No playlistend limit
is set for inventory. Shorts are defined by the channel Shorts tab, not duration.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from . import control

CHANNEL_ID = re.compile(r'UC[\w-]{22}', re.ASCII)
VIDEO_ID = re.compile(r'[\w-]{11}', re.ASCII)
KINDS = ('shorts', 'videos', 'streams')


class AccessRestricted(RuntimeError):
    pass


def canonical_input(raw):
    u = urlsplit(raw.strip())
    if u.scheme != 'https' or u.hostname not in ('youtube.com','www.youtube.com') or u.username or u.password or u.port:
        raise ValueError('Use an https YouTube channel URL')
    parts=u.path.strip('/').split('/')
    if parts[0].startswith('@') and re.fullmatch(r'@[\w.-]{1,100}',parts[0]): base=parts[0]
    elif len(parts)>=2 and parts[0]=='channel' and CHANNEL_ID.fullmatch(parts[1]): base='/'.join(parts[:2])
    else: raise ValueError('Use a channel handle or canonical /channel/UC… URL')
    extra=parts[1:] if base.startswith('@') else parts[2:]
    if extra and (len(extra)!=1 or extra[0] not in KINDS): raise ValueError('Unsupported channel tab')
    return 'https://www.youtube.com/'+base


def _execute(args, timeout=600, cancelled=None):
    from .e2e_safety import deny_live
    deny_live('YouTube channel archive access')
    from backend.core.scraper import js_runtime_args
    cmd=[sys.executable,'-m','yt_dlp',*js_runtime_args(),'--ignore-config','--no-progress','--socket-timeout','30',
        '--extractor-retries','1','--retries','2','--fragment-retries','2','--sleep-requests','1',*args]
    p=control.run(cmd,timeout=timeout) if cancelled is None else _cancellable_run(cmd,timeout,cancelled)
    if p.returncode:
        message=(p.stderr or p.stdout or 'YouTube request failed')[-2000:]
        if any(x in message.lower() for x in ('not a bot','sign in','captcha','http error 403','http error 429','private video','members-only','drm','video unavailable','has been removed','age-restricted')):
            raise AccessRestricted(message)
        raise RuntimeError(message)
    return p.stdout


def _cancellable_run(cmd,timeout,cancelled):
    import subprocess,tempfile,time
    if cancelled.is_set():raise RuntimeError('Archive worker lease lost')
    with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
        child=subprocess.Popen(cmd,stdout=out,stderr=err)
        try:
            deadline=time.monotonic()+timeout
            while child.poll() is None:
                control.check()
                if cancelled.is_set() or time.monotonic()>deadline:raise RuntimeError('Archive download stopped: lease lost or timeout')
                try:child.wait(timeout=.5)
                except subprocess.TimeoutExpired:pass
            if cancelled.is_set():raise RuntimeError('Archive worker lease lost')
            out.seek(0);err.seek(0)
            return subprocess.CompletedProcess(cmd,child.returncode,out.read().decode(errors='replace'),err.read().decode(errors='replace'))
        finally:
            if child.poll() is None:child.kill();child.wait()


def inventory(url, kinds):
    base=canonical_input(url)
    if not kinds or any(k not in KINDS for k in kinds): raise ValueError('Choose Shorts, uploads or archived streams')
    result=None; entries={}
    for kind in dict.fromkeys(kinds):
        control.check(); control.progress(f'Enumerating complete {kind} library (all continuation pages)')
        try:
            data=json.loads(_execute(['--flat-playlist','--skip-download','--dump-single-json',base+'/'+kind]))
        except RuntimeError as exc:
            if 'does not have a '+kind+' tab' not in str(exc).lower(): raise
            # Register the exact channel, but do not invent an empty complete library.
            identity=json.loads(_execute(['--flat-playlist','--skip-download','--playlist-end','1','--dump-single-json',base]))
            cid=identity.get('channel_id')
            if not CHANNEL_ID.fullmatch(cid or ''):raise ValueError('Channel identity unavailable')
            return {'channel_id':cid,'title':identity.get('channel') or identity.get('title') or cid,
                'handle':identity.get('uploader_id'),'source_url':base,'videos':[], 'complete':False,
                'warnings':[str(exc)[-1000:]]}
        cid=data.get('channel_id')
        if not CHANNEL_ID.fullmatch(cid or ''): raise ValueError('YouTube did not return a canonical channel ID')
        if result and result['channel_id']!=cid: raise ValueError('Channel identity changed between tabs')
        result={'channel_id':cid,'title':data.get('channel') or data.get('uploader') or data.get('title') or cid,
                'handle':data.get('uploader_id'),'source_url':base}
        for item in data.get('entries',[]):
            control.check()
            if not item or not VIDEO_ID.fullmatch(item.get('id','')): raise ValueError('Incomplete channel inventory: a video has no stable ID')
            vid=item['id']
            entries.setdefault(vid,{'video_id':vid,'title':item.get('title') or vid,'media_kind':kind,
                'original_url':f'https://www.youtube.com/watch?v={vid}','upload_date':item.get('upload_date'),
                'duration':item.get('duration'),'estimated_bytes':item.get('filesize') or item.get('filesize_approx'),
                'availability':item.get('availability'),'live_status':item.get('live_status')})
    return {**result,'videos':list(entries.values()),'complete':True}


def download(channel_id, video_id, destination: Path, max_bytes, cancelled=None):
    if not CHANNEL_ID.fullmatch(channel_id) or not VIDEO_ID.fullmatch(video_id): raise ValueError('Invalid media identity')
    destination.mkdir(parents=True,exist_ok=True)
    _execute(['--no-playlist','--write-info-json','--no-overwrites','--max-filesize',str(max_bytes),
        '--format','bv*[height<=1080]+ba/b[height<=1080]','--merge-output-format','mp4',
        '--output',str(destination/'%(id)s.%(ext)s'),f'https://www.youtube.com/watch?v={video_id}'],timeout=1800,cancelled=cancelled)
    info_path=destination/(video_id+'.info.json')
    if not info_path.is_file(): raise RuntimeError('Download has no metadata receipt')
    info=json.loads(info_path.read_text())
    if info.get('id')!=video_id or info.get('channel_id')!=channel_id: raise ValueError('Downloaded media belongs to a different channel/video')
    if info.get('is_live') or info.get('live_status') in ('is_live','is_upcoming'): raise ValueError('Live/upcoming broadcasts are not archived uploads')
    paths=[p for p in destination.glob(video_id+'.*') if p.suffix.lower() in ('.mp4','.webm','.mkv','.mov')]
    if len(paths)!=1 or paths[0].stat().st_size>max_bytes: raise RuntimeError('Download missing, ambiguous or exceeds available transfer bound')
    return paths[0],info
