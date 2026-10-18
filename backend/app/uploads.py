"""Bounded, atomic uploads. Failed validation preserves the previous media."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from fastapi import HTTPException, UploadFile
from .config import settings


async def save_video(file: UploadFile, destination: Path) -> tuple[int, dict]:
    from .studio import media
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".upload-", suffix=".part", dir=destination.parent)
    size = 0
    try:
        with os.fdopen(fd, "wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, "Video exceeds configured upload size limit")
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        if not size:
            raise HTTPException(400, "Empty video upload")
        probe=validate_video(Path(name))
        if destination.is_symlink():
            raise HTTPException(400, "Unsafe destination")
        os.replace(name, destination)
        return size, probe
    finally:
        Path(name).unlink(missing_ok=True)


def validate_video(path: Path) -> dict:
    from .studio import media
    try:
        probe=json.loads(media.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],timeout=30).stdout)
        video=next(x for x in probe.get('streams',[]) if x.get('codec_type')=='video')
        if (not video.get('width') or not video.get('height') or video['width']*video['height']>20_000_000
                or float(probe['format'].get('duration',0))<=0):raise ValueError('Invalid dimensions/duration')
    except Exception as exc:raise HTTPException(400,'Upload is not a valid bounded playable video') from exc
    try:
        media.run(['ffmpeg','-v','error','-xerror','-err_detect','explode','-threads','2','-i',str(path),
                   '-map','0:v:0','-map','0:a?','-f','null','-'],timeout=120)
    except Exception as exc:raise HTTPException(400,'Upload failed complete media decoding') from exc
    return probe


async def save_cookies(file: UploadFile, destination: Path):
    data=await file.read(1024*1024+1)
    if len(data)>1024*1024:raise HTTPException(413,'Cookies must be at most 1 MiB')
    try:
        text=data.decode('utf-8-sig')
        if not text or '\x00' in text:raise ValueError()
        count=0
        for line in text.splitlines():
            if not line.strip() or (line.startswith('#') and not line.startswith('#HttpOnly_')):continue
            values=line.removeprefix('#HttpOnly_').split('\t')
            if (len(values)!=7 or not values[0] or any(c.isspace() for c in values[0])
                    or values[1] not in ('TRUE','FALSE') or not values[2].startswith('/')
                    or values[3] not in ('TRUE','FALSE') or not values[4].isdigit() or not values[5]
                    or len(values[5])+len(values[6])>16384):raise ValueError()
            count+=1
        if not count:raise ValueError()
    except (UnicodeError,ValueError):raise HTTPException(400,'Upload a valid Netscape cookies.txt export')
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.cookies-',dir=destination.parent)
    try:
        with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
        os.replace(name,destination)
    finally:Path(name).unlink(missing_ok=True)
    return len(data)
