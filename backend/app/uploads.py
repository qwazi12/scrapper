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
        try:
            probe = json.loads(media.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", name], timeout=30).stdout)
            video = next(x for x in probe.get("streams", []) if x.get("codec_type") == "video")
            if not video.get("width") or not video.get("height") or float(probe["format"].get("duration", 0)) <= 0:
                raise ValueError("Invalid video dimensions/duration")
        except Exception as exc:
            raise HTTPException(400, "Upload is not a valid playable video") from exc
        try:
            media.run(["ffmpeg", "-v", "error", "-xerror", "-err_detect", "explode", "-i", name,
                       "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"], timeout=120)
        except Exception as exc:
            raise HTTPException(400,"Upload failed complete media decoding") from exc
        if destination.is_symlink():
            raise HTTPException(400, "Unsafe destination")
        os.replace(name, destination)
        return size, probe
    finally:
        Path(name).unlink(missing_ok=True)
