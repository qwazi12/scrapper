"""Keep cached video files from counting as service memory (2026-10-20, Railway cost).

Railway bills the container's memory, and Linux counts the page cache of every video it has read or
written (renders, Drive downloads, uploads, hashing). Measured: 1.99 GB = app 197 MB + 1.69 GB file cache.
Every few minutes we tell the kernel it can drop cached pages of large files under /data. Nothing is
deleted; a file read again simply comes from disk.
"""
from __future__ import annotations

import os
import pathlib
import time
from typing import Any

MIN_BYTES = 1_000_000          # only video-sized files; the database and small files are left alone
EVERY_SECONDS = 300
_last = 0.0


def cgroup_memory() -> dict[str, int]:
    """Current container memory and its file-cache part (cgroup v2), in bytes; {} if not available."""
    out: dict[str, int] = {}
    try:
        out["current"] = int(pathlib.Path("/sys/fs/cgroup/memory.current").read_text().strip())
        for line in pathlib.Path("/sys/fs/cgroup/memory.stat").read_text().splitlines():
            key, _, value = line.partition(" ")
            if key in ("anon", "file"):
                out[key] = int(value)
    except (OSError, ValueError):
        return {}
    return out


def release(root: pathlib.Path, min_bytes: int = MIN_BYTES, budget_seconds: float = 20.0) -> dict[str, Any]:
    """Advise the kernel to drop cached pages of large files under root. Never raises."""
    if not hasattr(os, "posix_fadvise"):
        return {"files": 0, "skipped": "not supported on this system"}
    started, files, total = time.monotonic(), 0, 0
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if time.monotonic() - started > budget_seconds:
                return {"files": files, "bytes": total, "stopped": "time budget"}
            path = os.path.join(dirpath, name)
            try:
                size = os.stat(path, follow_symlinks=False).st_size
                if size < min_bytes or name.endswith((".db", ".db-wal", ".db-shm")):
                    continue
                fd = os.open(path, os.O_RDONLY)
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                finally:
                    os.close(fd)
                files += 1
                total += size
            except OSError:
                continue
    return {"files": files, "bytes": total}


def maybe_release(root: pathlib.Path) -> dict[str, Any] | None:
    global _last
    if time.monotonic() - _last < EVERY_SECONDS:
        return None
    _last = time.monotonic()
    before = cgroup_memory()
    result = release(root)
    after = cgroup_memory()
    if before and after:
        result.update(memory_before=before.get("current"), memory_after=after.get("current"),
                      file_cache_after=after.get("file"))
    return result
