"""Tests for Top 5 & Top 10 Countdown Compilations."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from backend.app.studio.compilations import bumper, curation, stitch


def test_theme_presets():
    themes = curation.list_themes()
    assert len(themes) >= 5
    ids = [t["id"] for t in themes]
    assert "upcoming_scifi" in ids
    assert "mindbending_thrillers" in ids


def test_create_bumper_frame(tmp_path):
    dest = tmp_path / "test_bumper.jpg"
    out = bumper.create_bumper_frame(
        rank=5,
        title="DUNE: PART THREE",
        badge="IN THEATERS 2026",
        dest=dest,
    )
    assert out.exists()
    assert out.stat().st_size > 0
    with Image.open(out) as im:
        assert im.size == (1920, 1080)


def test_record_compilation_made():
    curation.record_compilation_made("Top 5 Sci-Fi 2026", [101, 102])
    made = curation.get_made_compilations()
    assert "Top 5 Sci-Fi 2026" in made


def test_generate_compilation_thumbnails(tmp_path):
    # Create fake project objects with local poster/backdrop
    p1 = MagicMock()
    p1.id = 1
    p1.title = "PROJECT ONE"
    pos1 = tmp_path / "pos1.jpg"
    Image.new("RGB", (500, 750), (200, 30, 30)).save(pos1)
    p1.facts = {"local": {"poster": str(pos1)}}

    p2 = MagicMock()
    p2.id = 2
    p2.title = "PROJECT TWO"
    pos2 = tmp_path / "pos2.jpg"
    Image.new("RGB", (500, 750), (30, 30, 200)).save(pos2)
    p2.facts = {"local": {"poster": str(pos2)}}

    thumbs = stitch.generate_compilation_thumbnails(
        projects=[p1, p2],
        title="TOP 5 SCI-FI MOVIES",
        out_dir=tmp_path / "thumbs",
    )
    assert len(thumbs) == 3
    for t in thumbs:
        assert t.exists()
        with Image.open(t) as im:
            assert im.size == (1280, 720)
    assert (tmp_path / "thumbs" / "thumbnail.jpg").exists()


def test_cinemeta_formatting():
    from backend.app.studio.compilations import cinemeta

    raw = {
        "id": "tt1234567",
        "name": "Test Movie",
        "year": "2026",
        "genres": ["Action", "Sci-Fi"],
        "director": ["Director Name"],
        "cast": ["Actor A", "Actor B"],
        "imdbRating": "8.5",
        "description": "A thrilling adventure.",
        "poster": "https://example.com/poster.jpg",
        "trailers": [{"source": "abc12345", "type": "Trailer"}],
    }
    res = cinemeta._format_meta(raw, "movie")
    assert res["title"] == "Test Movie"
    assert res["imdb_id"] == "tt1234567"
    assert res["rating"] == 8.5
    assert len(res["trailers"]) == 1
    assert res["trailers"][0]["youtube_id"] == "abc12345"


def test_fmhy_curated_resources():
    from backend.app.studio.compilations import fmhy

    tools = fmhy.get_curated_resources()
    assert len(tools) >= 5
    names = [t["name"] for t in tools]
    assert "Trakt.tv" in names
    assert "Letterboxd" in names
    assert "NewPipe Extractor" in names


def test_uniform_audio_keeps_the_joined_audio_as_long_as_the_video(tmp_path):
    """Owner (2026-10-13): countdown audio broke — pieces with different audio formats
    (96 kHz mono renders, 48 kHz stereo cards, 24 kHz voice, or none) were joined as-is."""
    import subprocess
    def clip(name, dur, audio):
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s=320x180:d={dur}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={audio[0]}:duration={dur}", "-ac", str(audio[1])]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-video_track_timescale", "30000"]
        cmd += (["-c:a", "aac"] if audio else []) + [str(tmp_path / name)]
        subprocess.run(cmd, check=True)
        return tmp_path / name
    parts = [clip("a.mp4", 3, (24000, 1)), clip("b.mp4", 2, None), clip("c.mp4", 4, (96000, 1))]
    u = [stitch.uniform_audio(p, tmp_path / f"u{i}.mp4") for i, p in enumerate(parts)]
    (tmp_path / "l.txt").write_text("".join(f"file '{x}'\n" for x in u))
    out = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "l.txt"), "-c", "copy", str(out)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,sample_rate,duration", "-of", "json", str(out)],
                           capture_output=True, text=True).stdout
    import json
    streams = {s["codec_type"]: s for s in json.loads(probe)["streams"]}
    assert streams["audio"]["sample_rate"] == "48000"
    assert abs(float(streams["audio"]["duration"]) - float(streams["video"]["duration"])) < 0.2


def test_trailing_silence_finds_the_end_screen(tmp_path):
    import subprocess
    f = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x180:d=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-af", "apad", "-t", "10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(f)], check=True)
    assert 3.5 < stitch.trailing_silence(f) < 4.5          # 6 s of sound, 4 s silent tail
