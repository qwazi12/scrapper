"""Unit tests for SQLite database backup, integrity checking, pruning, and restore.

Uses standard Python unittest so tests can execute in any clean environment.
"""

from __future__ import annotations

import gzip
import io
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

# Ensure backend and repo root are in python path
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT))

from app import backup
from app.config import settings
from scripts import restore_db


class TestDatabaseBackup(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="test_backup_"))
        self.db_file = self.tmp_dir / "test_scrapper.db"
        self.backups_dir = self.tmp_dir / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)

        # Seed sample SQLite DB
        conn = sqlite3.connect(self.db_file)
        cur = conn.cursor()
        cur.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);")
        cur.execute("INSERT INTO users (name) VALUES ('Alice'), ('Bob'), ('Charlie');")
        cur.execute("CREATE TABLE queue_items (id INTEGER PRIMARY KEY, title TEXT, status TEXT);")
        cur.execute("INSERT INTO queue_items (title, status) VALUES ('Video 1', 'ready'), ('Video 2', 'review');")
        conn.commit()
        conn.close()

        # Monkeypatch settings
        self.orig_db_url = settings.database_url
        self.orig_data_dir = settings.data_dir
        self.orig_drive_folder = settings.backup_drive_folder_id

        settings.database_url = f"sqlite:///{self.db_file}"
        settings.data_dir = str(self.tmp_dir)
        settings.backup_drive_folder_id = ""  # off-disk disabled in unit tests

    def tearDown(self):
        settings.database_url = self.orig_db_url
        settings.data_dir = self.orig_data_dir
        settings.backup_drive_folder_id = self.orig_drive_folder
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_create_backup_and_integrity(self):
        res = backup.create_backup(tag="test_unit", upload_to_drive=False)

        self.assertTrue(res["ok"])
        self.assertEqual(res["tag"], "test_unit")
        self.assertGreater(res["uncompressed_bytes"], 0)
        self.assertGreater(res["compressed_bytes"], 0)
        self.assertEqual(len(res["sha256"]), 64)

        gz_path = pathlib.Path(res["path"])
        self.assertTrue(gz_path.is_file())
        self.assertTrue(gz_path.name.endswith("_test_unit.db.gz"))

        # Verify decompressed file is a valid, healthy SQLite database
        raw_data = gzip.decompress(gz_path.read_bytes())
        temp_restored = self.backups_dir / "restored_check.db"
        temp_restored.write_bytes(raw_data)

        chk_conn = sqlite3.connect(temp_restored)
        cur = chk_conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        self.assertEqual(cur.fetchone()[0], "ok")

        cur.execute("SELECT COUNT(*) FROM users;")
        self.assertEqual(cur.fetchone()[0], 3)

        cur.execute("SELECT COUNT(*) FROM queue_items;")
        self.assertEqual(cur.fetchone()[0], 2)
        chk_conn.close()

    def test_prune_local_backups(self):
        # Create 10 dummy backup files with incrementing mtimes
        base_time = time.time() - 1000
        for i in range(10):
            f = self.backups_dir / f"scrapper_2026100{i}_000000_nightly.db.gz"
            f.write_bytes(b"dummy gz content")
            os.utime(f, (base_time + i * 60, base_time + i * 60))

        pruned = backup.prune_local_backups(keep=7)
        self.assertEqual(len(pruned), 3)

        remaining = sorted([f.name for f in self.backups_dir.glob("scrapper_*.db.gz")])
        self.assertEqual(len(remaining), 7)
        self.assertNotIn("scrapper_20261000_000000_nightly.db.gz", remaining)
        self.assertNotIn("scrapper_20261001_000000_nightly.db.gz", remaining)
        self.assertNotIn("scrapper_20261002_000000_nightly.db.gz", remaining)
        self.assertIn("scrapper_20261009_000000_nightly.db.gz", remaining)

    def test_backup_status_staleness(self):
        # 1. No backups -> is_stale is True
        status = backup.get_backup_status()
        self.assertTrue(status["is_stale"])
        self.assertEqual(status["local_copies_count"], 0)

        # 2. Fresh backup -> is_stale is False
        res = backup.create_backup(tag="fresh", upload_to_drive=False)
        status2 = backup.get_backup_status()
        self.assertFalse(status2["is_stale"])
        self.assertEqual(status2["local_copies_count"], 1)

        # 3. Old backup (>26 hours old) -> is_stale is True
        fresh_file = pathlib.Path(res["path"])
        old_time = time.time() - (27 * 3600)
        os.utime(fresh_file, (old_time, old_time))

        status3 = backup.get_backup_status()
        self.assertTrue(status3["is_stale"])
        self.assertIn("exceeds 26h threshold", status3["warning"])

    def test_restore_cli_dry_run_and_confirm(self):
        # 1. Take initial backup
        res = backup.create_backup(tag="baseline", upload_to_drive=False)
        gz_file = pathlib.Path(res["path"])

        # 2. Mutate live DB (delete Alice, add extra queue items)
        conn = sqlite3.connect(self.db_file)
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE name='Alice';")
        cur.execute("INSERT INTO queue_items (title, status) VALUES ('Video 3', 'posted'), ('Video 4', 'posted');")
        conn.commit()
        conn.close()

        # 3. Test dry run
        captured_out = io.StringIO()
        with patch.object(sys, "argv", ["restore_db.py", "--from", str(gz_file), "--to", str(self.db_file)]):
            with patch("sys.stdout", captured_out):
                ret = restore_db.main()
        self.assertEqual(ret, 0)
        output = captured_out.getvalue()
        self.assertIn("[DRY RUN COMPLETE]", output)
        self.assertIn("users", output)
        self.assertIn("queue_items", output)

        # Verify DB was NOT modified by dry run
        conn = sqlite3.connect(self.db_file)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users;")
        self.assertEqual(cur.fetchone()[0], 2)
        conn.close()

        # 4. Test confirmed restore
        captured_out2 = io.StringIO()
        with patch.object(sys, "argv", ["restore_db.py", "--from", str(gz_file), "--to", str(self.db_file), "--confirm"]):
            with patch("sys.stdout", captured_out2):
                ret_confirm = restore_db.main()
        self.assertEqual(ret_confirm, 0)
        output2 = captured_out2.getvalue()
        self.assertIn("DATABASE RESTORE COMPLETED SUCCESSFULLY", output2)

        # Verify database was restored to baseline snapshot
        conn = sqlite3.connect(self.db_file)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users;")
        self.assertEqual(cur.fetchone()[0], 3)  # Alice restored!
        cur.execute("SELECT COUNT(*) FROM queue_items;")
        self.assertEqual(cur.fetchone()[0], 2)  # Videos 3 & 4 rolled back!
        conn.close()

        # Verify emergency pre-restore snapshot was generated
        pre_restores = list(self.backups_dir.glob("scrapper_pre_restore_*.db.gz"))
        self.assertGreaterEqual(len(pre_restores), 1)


if __name__ == "__main__":
    unittest.main()
