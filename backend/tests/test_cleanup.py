"""Unit tests for disk space management, cache pruning, and cleanup."""

import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

# Ensure backend and repo root are in python path
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT))

from app import cleanup
from app.config import settings


class TestCleanup(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="scrapper_test_cleanup_")
        self.temp_path = pathlib.Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cleanup_render_intermediates(self):
        out_dir = self.temp_path / "render_new"
        out_dir.mkdir(parents=True)
        # Create intermediate files
        (out_dir / "subscribe_0.mov").write_bytes(b"mov1" * 100)
        (out_dir / "banner_0.mov").write_bytes(b"mov2" * 100)
        (out_dir / "subscribe.png").write_bytes(b"png1" * 50)
        (out_dir / "segments.txt").write_bytes(b"list" * 10)
        (out_dir / "narration.m4a").write_bytes(b"m4a" * 100)
        (out_dir / "poster_clean.jpg").write_bytes(b"jpg" * 50)
        # Final output files to keep
        (out_dir / "final.mp4").write_bytes(b"final_video" * 200)
        (out_dir / "thumbnail.jpg").write_bytes(b"thumb" * 50)

        res = cleanup.cleanup_render_intermediates(out_dir)
        self.assertGreaterEqual(res["files_removed"], 5)
        self.assertGreater(res["bytes_freed"], 0)

        # Intermediate files removed
        self.assertFalse((out_dir / "subscribe_0.mov").exists())
        self.assertFalse((out_dir / "banner_0.mov").exists())
        self.assertFalse((out_dir / "narration.m4a").exists())
        self.assertFalse((out_dir / "segments.txt").exists())

        # Essential video and thumbnail preserved
        self.assertTrue((out_dir / "final.mp4").exists())
        self.assertTrue((out_dir / "thumbnail.jpg").exists())

    def test_prune_lru_cache(self):
        cache_dir = self.temp_path / "cache"
        cache_dir.mkdir()
        # Create 5 files with staggered mtimes
        f1 = cache_dir / "oldest.dat"
        f1.write_bytes(b"A" * 1000)
        os.utime(f1, (time.time() - 500, time.time() - 500))

        f2 = cache_dir / "middle.dat"
        f2.write_bytes(b"B" * 1000)
        os.utime(f2, (time.time() - 300, time.time() - 300))

        f3 = cache_dir / "newest.dat"
        f3.write_bytes(b"C" * 1000)
        os.utime(f3, (time.time() - 10, time.time() - 10))

        # Total is 3000 bytes. Cap at 1500 bytes. Target ratio 0.8 => target = 1200 bytes
        res = cleanup.prune_lru_cache(cache_dir, max_bytes=1500, target_ratio=0.8)
        self.assertGreaterEqual(res["files_removed"], 2)
        # Oldest and middle should be evicted
        self.assertFalse(f1.exists())
        self.assertFalse(f2.exists())
        # Newest should remain
        self.assertTrue(f3.exists())

    def test_cleanup_leftover_renders(self):
        projects_root = self.temp_path / "studio" / "projects"
        p1 = projects_root / "p_1"
        p1.mkdir(parents=True)
        rn = p1 / "render_new"
        rn.mkdir()
        (rn / "junk.tmp").write_bytes(b"junk")

        # Fake mtime older than 2 hours
        os.utime(rn, (time.time() - 7200, time.time() - 7200))

        # Mock settings.data_dir to temp_path
        orig_data_dir = settings.data_dir
        try:
            settings.data_dir = str(self.temp_path)
            res = cleanup.cleanup_leftover_renders(max_age_seconds=3600)
            self.assertEqual(res["folders_removed"], 1)
            self.assertFalse(rn.exists())
            self.assertTrue(p1.exists())
        finally:
            settings.data_dir = orig_data_dir

    def test_cleanup_temp_downloads(self):
        dl_dir = self.temp_path / "downloads"
        dl_dir.mkdir(parents=True)
        part = dl_dir / "vid.mp4.part"
        part.write_bytes(b"partial video data")
        os.utime(part, (time.time() - 10000, time.time() - 10000))

        fresh = dl_dir / "fresh.mp4.part"
        fresh.write_bytes(b"fresh partial video data")

        orig_data_dir = settings.data_dir
        try:
            settings.data_dir = str(self.temp_path)
            res = cleanup.cleanup_temp_downloads(max_age_seconds=7200)
            self.assertEqual(res["files_removed"], 1)
            self.assertFalse(part.exists())
            self.assertTrue(fresh.exists())
        finally:
            settings.data_dir = orig_data_dir

    def test_disk_usage_and_threshold_guard(self):
        usage = cleanup.get_disk_usage(self.temp_path)
        self.assertIn("total_bytes", usage)
        self.assertIn("used_bytes", usage)
        self.assertIn("percent_used", usage)
        self.assertIn("status", usage)

        # Test threshold guard
        orig_max = settings.max_disk_usage_percent
        try:
            # Set artificial 0% max threshold to force trigger
            settings.max_disk_usage_percent = 0.0
            with self.assertRaises(cleanup.DiskFullError):
                cleanup.check_disk_space()
        finally:
            settings.max_disk_usage_percent = orig_max


if __name__ == "__main__":
    unittest.main()
