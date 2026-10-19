#!/usr/bin/env python3
"""Database Restore CLI.

Safely restores a SQLite snapshot into the live Scrapper database.

Features:
- Dry-run by default: displays side-by-side row count comparison between Current and Backup.
- Requires `--confirm --offline-confirmed` and an exclusive maintenance lock.
- Creates an emergency pre-restore snapshot (`scrapper_pre_restore_<timestamp>.db.gz`)
  before replacing the active database so any restore can be undone.
- Performs `PRAGMA integrity_check;` before and after restore.
"""

from __future__ import annotations

import argparse
import gzip
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


from backend.app.sqlite_snapshots import compressed_snapshot, unique_stem, offline_lock, snapshot_raw, integrity


def get_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return a mapping of {table_name: row_count} for user tables."""
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [row[0] for row in cur.fetchall()]
    counts = {}
    for t in sorted(tables):
        try:
            quoted = t.replace('"', '""')
            cur.execute(f'SELECT COUNT(*) FROM "{quoted}";')
            counts[t] = cur.fetchone()[0]
        except Exception:
            counts[t] = -1
    return counts


def run_integrity_check(db_path: pathlib.Path) -> bool:
    """Verify database passes SQLite integrity check."""
    try:
        integrity(db_path)
        return True
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
    parser.add_argument('--offline-confirmed', action='store_true',
        help='Confirm all app and worker processes, including older versions, are stopped.')
    args = parser.parse_args()
    if args.confirm and not args.offline_confirmed:
        print('Restore refused: stop all app/workers, then use --confirm --offline-confirmed.', file=sys.stderr)
        return 1

    src_path = pathlib.Path(args.from_file).resolve()
    target_path = pathlib.Path(args.target_db).resolve()

    if not src_path.is_file():
        print(f"❌ Backup file not found: {src_path}", file=sys.stderr)
        return 1

    print(f"\n🔍 Inspecting backup snapshot: {src_path}")
    print(f"🎯 Target live database:     {target_path}\n")

    # 1. Unpack if compressed .gz
    target_path.parent.mkdir(parents=True,exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix=".scrapper_restore_",dir=target_path.parent)
    decompressed_path = pathlib.Path(temp_dir) / "snapshot.db"

    try:
        if src_path.suffix == ".gz":
            print("📦 Decompressing .db.gz archive for verification...")
            with gzip.open(src_path, "rb") as f_in, open(decompressed_path, "wb") as f_out:
                total=0
                # Bound decompression by available target storage; preserve headroom.
                limit=max(0,(shutil.disk_usage(target_path.parent).free-16*1024*1024)//3)
                while chunk:=f_in.read(1024*1024):
                    total+=len(chunk)
                    if total>limit:raise RuntimeError('Insufficient free space for restore and emergency snapshot')
                    f_out.write(chunk)
            inspect_db = decompressed_path
        else:
            snapshot_raw(src_path,decompressed_path)
            inspect_db = decompressed_path

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
            target_conn = sqlite3.connect(target_path.as_uri()+'?mode=ro',uri=True)
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
            print("To execute this restore, stop all app/workers and run again with --confirm --offline-confirmed:\n")
            print(f"  python scripts/restore_db.py --from \"{src_path}\" --confirm --offline-confirmed\n")
            return 0

        # All operations below run while an exclusive cooperating-process lock is held.
        # The explicit offline flag also covers older app versions without this lock.
        with offline_lock(target_path):
            if target_path.is_file():
                emergency=compressed_snapshot(target_path,target_path.parent/'backups',
                    unique_stem('scrapper_pre_restore'))
                print(f"Verified emergency snapshot: {emergency['path']} SHA256={emergency['sha256']}")
            # SQLite backup is transactional; never copy raw WAL-less database bytes.
            snapshot_raw(inspect_db,target_path)
            integrity(target_path)
            with sqlite3.connect(target_path) as restored:
                if get_table_counts(restored)!=backup_counts:
                    raise RuntimeError('Post-restore table counts differ from the verified snapshot')
            print(f"DATABASE RESTORE COMPLETED SUCCESSFULLY from {src_path.name} into {target_path.name}")
        return 0
    except Exception as exc:
        print(f'Restore failed: {exc}',file=sys.stderr)
        return 1

    finally:
        # Clean up temp decompression directory
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
