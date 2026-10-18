"""Actual FFmpeg final-file checks and failed-upload preservation."""
import asyncio
import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from backend.app.config import settings
from backend.app.studio import media, media_quality
from backend.app.uploads import save_video


def make_video(path, audio=True):
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=1"]
    if audio: args += ["-f", "lavfi", "-i", "sine=f=400:d=1", "-c:a", "aac"]
    media.run([*args, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])


def test_invalid_empty_and_oversized_upload_preserve_old_file(tmp_path, monkeypatch):
    dest = tmp_path / "trailer.mp4"; dest.write_bytes(b"previous export")
    for payload, status in ((b"", 400), (b"not video", 400), (b"x" * 100, 413)):
        monkeypatch.setattr(settings, "max_upload_bytes", 50)
        with pytest.raises(HTTPException) as error:
            asyncio.run(save_video(UploadFile(file=io.BytesIO(payload), filename="anything.mp4"), dest))
        assert error.value.status_code == status and dest.read_bytes() == b"previous export"
        assert not list(tmp_path.glob(".upload-*"))


def test_final_missing_audio_blocks_and_hash_changes_invalidate_approval(tmp_path):
    silent = tmp_path / "silent.mp4"; make_video(silent, False)
    qa = media_quality.inspect_export(silent, expected_seconds=1)
    assert "Missing narration audio stream" in qa["errors"]
    final = tmp_path / "final.mp4"; make_video(final)
    qa = media_quality.inspect_export(final, expected_seconds=1)
    assert not qa["errors"] and qa["decode"] == "passed" and qa["width"] == 320
    p = SimpleNamespace(render={"file": "final.mp4", "quality": qa}, script={"sentences": ["fixture"]}, plan=[], review={})
    fingerprint = media_quality.export_fingerprint(p, tmp_path)
    p.review = {"export_approval": {"fingerprint": fingerprint}}
    media_quality.require_export_approval(p, tmp_path)
    p.script = {"sentences": ["changed"]}
    with pytest.raises(RuntimeError, match="approve this exact export"):
        media_quality.require_export_approval(p, tmp_path)
    final.write_bytes(final.read_bytes() + b"changed")
    with pytest.raises(RuntimeError, match="changed since quality"):
        media_quality.export_fingerprint(p, tmp_path)
