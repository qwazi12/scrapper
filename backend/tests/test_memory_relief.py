"""2026-10-20: cached video pages counted as service memory on Railway."""
import os

from backend.app import memory_relief


def test_release_advises_large_files_and_skips_small_and_database(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    big = tmp_path / "a" / "render.mp4"; big.write_bytes(b"x" * 2_000_000)
    small = tmp_path / "thumb.jpg"; small.write_bytes(b"x" * 1000)
    db = tmp_path / "scrapper.db"; db.write_bytes(b"x" * 2_000_000)
    seen = []
    real = getattr(os, "posix_fadvise", None)

    def fake(fd, off, length, advice):
        seen.append(os.fstat(fd).st_size)
    monkeypatch.setattr(os, "posix_fadvise", fake, raising=False)
    monkeypatch.setattr(os, "POSIX_FADV_DONTNEED", getattr(os, "POSIX_FADV_DONTNEED", 4), raising=False)
    res = memory_relief.release(tmp_path)
    assert res == {"files": 1, "bytes": 2_000_000} and seen == [2_000_000]
    assert big.read_bytes()[:1] == b"x"                         # nothing deleted or changed


def test_maybe_release_runs_at_most_every_few_minutes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(memory_relief, "release", lambda root, **k: calls.append(root) or {"files": 0})
    monkeypatch.setattr(memory_relief, "_last", 0.0)
    assert memory_relief.maybe_release(tmp_path) is not None
    assert memory_relief.maybe_release(tmp_path) is None
    assert len(calls) == 1
