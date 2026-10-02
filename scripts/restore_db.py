#!/usr/bin/env python3
"""Database Restore CLI.

Safely restores a SQLite snapshot into the live Scrapper database.

Features:
- Dry-run by default: displays side-by-side row count comparison between Current and Backup.
- Requires `--confirm` flag to execute.
- Creates an emergency pre-restore snapshot (`scrapper_pre_restore_<timestamp>.db.gz`)
  before replacing the active database so any restore can be undone.
- Performs `PRAGMA integrity_check;` before and after restore.
"""

from __future__ import annotations

import argparse
import datetime
from datetime import timezone
import gzip
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile

# Add repo root to sys.path so we can import app modules if needed
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from backend.app.config import settings
    DEFAULT_DB_PATH = settings.data_path / "scrapper.db"
    BACKUPS_DIR = settings.data_path / "backups"
except Exception:
    DEFAULT_DB_PATH = pathlib.Path("data/scrapper.db").resolve()
    BACKUPS_DIR = pathlib.Path("data/backups").resolve()


def get_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return a mapping of {table_name: row_count} for user tables."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row[0] for row in cur.fetchall()]
    counts = {}
    for t in sorted(tables):
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{t}";')
            counts[t] = cur.fetchone()[0]
        except Exception:
            counts[t] = -1
    return counts


def run_integrity_check(db_path: pathlib.Path) -> bool:
    """Verify database passes SQLite integrity check."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check;")
        res = cur.fetchall()
        conn.close()
        return bool(res and res[0][0].lower() == "ok")
    except Exception as exc:
        print(f"❌ Integrity check failed for {db_path}: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrapper Database Restore Tool")
    parser.add_argument(
        "--from",
        dest="from_file",
        required=True,
        help="Path to .db or .db.gz backup file to restore from",
    )
    parser.add_argument(
        "--to",
        dest="target_db",
        default=str(DEFAULT_DB_PATH),
        help=f"Target database file to restore into (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually apply the restore. Without this flag, only a dry-run comparison is shown.",
    )
    args = parser.parse_args()

    src_path = pathlib.Path(args.from_file).resolve()
    target_path = pathlib.Path(args.target_db).resolve()

    if not src_path.is_file():
        print(f"❌ Backup file not found: {src_path}", file=sys.stderr)
        return 1

    print(f"\n🔍 Inspecting backup snapshot: {src_path}")
    print(f"🎯 Target live database:     {target_path}\n")

    # 1. Unpack if compressed .gz
    temp_dir = tempfile.mkdtemp(prefix="scrapper_restore_")
    decompressed_path = pathlib.Path(temp_dir) / "snapshot.db"

    try:
        if src_path.suffix == ".gz":
            print("📦 Decompressing .db.gz archive for verification...")
            with gzip.open(src_path, "rb") as f_in, open(decompressed_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            inspect_db = decompressed_path
        else:
            inspect_db = src_path

        # 2. Check snapshot integrity
        if not run_integrity_check(inspect_db):
            print("❌ The backup snapshot failed PRAGMA integrity_check! Aborting restore for safety.", file=sys.stderr)
            return 1
        print("✓ Backup snapshot integrity check passed (healthy).")

        # 3. Read table counts
        src_conn = sqlite3.connect(inspect_db)
        backup_counts = get_table_counts(src_conn)
        src_conn.close()

        target_exists = target_path.is_file()
        current_counts: dict[str, int] = {}
        if target_exists:
            target_conn = sqlite3.connect(f"file:{target_path}?mode=ro", uri=True)
            current_counts = get_table_counts(target_conn)
            target_conn.close()

        # 4. Display comparison table
        all_tables = sorted(set(backup_counts.keys()) | set(current_counts.keys()))
        print("\n" + "=" * 65)
        print(f"{'Table Name':<28} | {'Current DB':>12} | {'Backup DB':>12} | {'Diff':>6}")
        print("-" * 65)

        total_current = 0
        total_backup = 0

        for t in all_tables:
            c_cnt = current_counts.get(t, 0)
            b_cnt = backup_counts.get(t, 0)
            diff = b_cnt - c_cnt
            diff_str = f"+{diff}" if diff > 0 else str(diff) if diff < 0 else "0"
            print(f"{t:<28} | {c_cnt:>12} | {b_cnt:>12} | {diff_str:>6}")
            total_current += max(0, c_cnt)
            total_backup += max(0, b_cnt)

        print("-" * 65)
        print(f"{'TOTAL ROWS':<28} | {total_current:>12} | {total_backup:>12} | {total_backup - total_current:>+6}")
        print("=" * 65 + "\n")

        # 5. Handle Dry Run vs Confirm
        if not args.confirm:
            print("ℹ️  [DRY RUN COMPLETE] No changes were made to the live database.")
            print("To execute this restore, run again with --confirm:\n")
            print(f"  python scripts/restore_db.py --from \"{src_path}\" --confirm\n")
            return 0

        # --- EXECUTE RESTORE ---
        print("⚠️  --confirm supplied: Proceeding with live database restoration...")

        # A. Save emergency pre-restore snapshot of current database
        backups_dir = target_path.parent / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        pre_restore_file = backups_dir / f"scrapper_pre_restore_{ts}.db.gz"

        if target_exists:
            print(f"🛡️  Creating emergency pre-restore backup: {pre_restore_file.name}...")
            with open(target_path, "rb") as f_in, gzip.open(pre_restore_file, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            print(f"✓ Pre-restore snapshot saved ({pre_restore_file.stat().st_size} bytes).")

        # B. Perform lock-safe restore using sqlite3 online backup
        print(f"🔄 Restoring snapshot into {target_path}...")
        src_conn = sqlite3.connect(inspect_db)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        dst_conn = sqlite3.connect(str(target_path))

        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        # C. Post-restore verification
        if not run_integrity_check(target_path):
            print("❌ Post-restore integrity check failed!", file=sys.stderr)
            if pre_restore_file.is_file():
                print(f"⚠️  Restore was unsuccessful. You can recover with: {pre_restore_file}", file=sys.stderr)
            return 1

        print("✓ Post-restore integrity check passed successfully.")
        print(f"\n🎉 DATABASE RESTORE COMPLETED SUCCESSFULLY from {src_path.name} into {target_path.name}!")
        return 0

    finally:
        # Clean up temp decompression directory
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
