"""C05 (2026-10-19): pieces whose audio runs past their picture, joined and loudness-normalised, must
come out with audio and video the same length (the release gate is 250 ms). Real ffmpeg, no providers."""
import json
import shutil
import subprocess

import pytest

from backend.app.studio.compilations import stitch

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def _run(*a):
    subprocess.run(a, check=True, capture_output=True)


def _lengths(p):
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(p)],
                                   capture_output=True, text=True).stdout)
    return {s["codec_type"]: float(s["duration"]) for s in pr["streams"]}


def _piece(tmp, name, video_s, audio_s, rate):
    out = tmp / f"{name}.mp4"
    # testsrc2 (moving picture) so x264 really uses B-frames, like real renders
    _run("ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={video_s}",
         "-f", "lavfi", "-i", f"sine=f=440:r={rate}:d={audio_s}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ac", "1", str(out))
    return out


def test_joined_and_normalised_countdown_keeps_audio_on_the_picture(tmp_path):
    shapes = [(4.0, 1.6, 22050), (2.5, 2.98, 22050), (7.3, 4.5, 22050), (2.5, 2.8, 48000), (5.0, 5.0, 24000)] * 3
    pieces = [_piece(tmp_path, f"p{i}", v, a, r) for i, (v, a, r) in enumerate(shapes)]
    uniform = []
    for i, p in enumerate(pieces):
        u = stitch.uniform_audio(p, tmp_path / f"u{i}.mp4", 6.93 if shapes[i][0] == 7.3 else None)
        lengths = _lengths(u)
        assert abs(lengths["video"] - lengths["audio"]) <= 0.05, (i, lengths)
        uniform.append(u)
    (tmp_path / "list.txt").write_text("".join(f"file '{u}'\n" for u in uniform))
    raw = tmp_path / "raw.mp4"
    _run("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "list.txt"), "-c", "copy", str(raw))
    total = stitch.video_seconds(raw)
    final = tmp_path / "final.mp4"
    _run("ffmpeg", "-v", "error", "-y", "-i", str(raw), "-c:v", "copy",
         "-af", f"loudnorm=I=-14:TP=-1.5:LRA=11,apad,atrim=0:{total:.3f}",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{total:.3f}", str(final))
    lengths = _lengths(final)
    assert abs(lengths["video"] - lengths["audio"]) <= 0.1, lengths
    # The release check decodes the whole file and refuses any decoder/muxer complaint
    # (trimmed pieces used to leave out-of-order frames at the joins).
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(final), "-f", "null", "-"],
                             capture_output=True, text=True)
    assert decoded.stderr.strip() == "", decoded.stderr[:300]
