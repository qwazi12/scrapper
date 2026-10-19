"""Database Backups & Point-in-Time Snapshots.

Provides:
1. Online SQLite snapshot via `sqlite3.Connection.backup`
2. Integrity check via `PRAGMA integrity_check;`
3. Gzip compression (.db.gz) and SHA-256 validation
4. Local disk retention: keep 7 daily copies in `/data/backups/`
5. Checksum-verified Google Drive uploads when configured; no automatic remote pruning
6. Automatic pre-migration snapshot in `init_db()`
7. Nightly 3:00 AM Eastern automated trigger in scheduler
8. Dry-run and confirmed restore engine (`scripts/restore_db.py`)
"""

from __future__ import annotations

import datetime
from datetime import timezone
import json
import logging
import os
import pathlib
import tempfile
import time
from typing import Any
from zoneinfo import ZoneInfo

from .config import settings

logger = logging.getLogger("scrapper.backup")

_last_nightly_date: str | None = None
_last_backup_cache: dict[str, Any] | None = None


class BackupError(Exception):
    """Raised when a backup operation or integrity check fails."""


def get_sqlite_db_path() -> pathlib.Path | None:
    """Return the absolute Path to the local SQLite database file, or None if Postgres."""
    url = settings.resolved_database_url
    if not url.startswith("sqlite:///"):
        return None
    db_file = url.replace("sqlite:///", "", 1)
    return pathlib.Path(db_file).resolve()


def get_local_backups_dir() -> pathlib.Path:
    """Return directory for local disk backups, ensuring it exists."""
    p = settings.data_path / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_status_cache_file() -> pathlib.Path:
    return get_local_backups_dir() / "latest_status.json"


def _read_persisted_status() -> dict[str, Any] | None:
    f = _get_status_cache_file()
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _write_persisted_status(data: dict[str, Any]) -> None:
    """Publish whole JSON atomically; an older completion cannot replace newer evidence."""
    import fcntl
    from .sqlite_snapshots import fsync_directory
    f = _get_status_cache_file()
    temporary = None
    try:
        with (f.parent / '.status.lock').open('a+b') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            old = _read_persisted_status()
            if old and str(old.get('created_at', '')) > str(data.get('created_at', '')):
                return
            fd, temporary = tempfile.mkstemp(prefix='.status-', dir=f.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, indent=2)
                stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, f)
            fsync_directory(f.parent)
    except Exception as exc:
        logger.warning("Could not persist backup status to disk: %s", exc)
    finally:
        if temporary: pathlib.Path(temporary).unlink(missing_ok=True)


def create_backup(tag: str = "manual", upload_to_drive: bool = True, *, rotate: bool = True) -> dict[str, Any]:
    """Create a verified, compressed point-in-time snapshot of the database.

    Args:
        tag: Label for the backup ('nightly', 'pre_migration', 'manual', etc.).
        upload_to_drive: If True, uploads the compressed file to Google Drive.

    Returns:
        dict with metadata, sizes, hash, and destination status.
    """
    db_path = get_sqlite_db_path()
    if not db_path:
        return {
            "ok": False,
            "error": "SQLite snapshot is unavailable for PostgreSQL; no cloud backup or restore coverage has been verified",
            "type": "postgres",
        }

    if not db_path.is_file():
        raise BackupError(f"Database file does not exist: {db_path}")

    from contextlib import nullcontext
    from .sqlite_snapshots import compressed_snapshot,unique_stem,lock_handle
    stem=unique_stem('scrapper',tag)
    final_path=get_local_backups_dir()/(stem+'.db.gz')
    # Pin the final name before it becomes visible to concurrent retention.
    # Before migrations, there are no claim tables and rotation is disabled.
    from .file_use import pin
    fd=lock_handle(db_path)
    try:
        with pin([final_path]) if rotate else nullcontext():
            return _create_backup(db_path,tag,upload_to_drive,rotate,stem)
    finally:os.close(fd)


def _create_backup(db_path,tag,upload_to_drive,rotate,stem):
    from .sqlite_snapshots import compressed_snapshot
    start_time=time.time()
    now_utc=datetime.datetime.now(timezone.utc)
    now_local=now_utc.astimezone(ZoneInfo(settings.backup_timezone))
    backups_dir=get_local_backups_dir()
    snapshot=compressed_snapshot(db_path,backups_dir,stem)
    final_gz_path=pathlib.Path(snapshot['path']);final_gz_name=snapshot['filename']
    uncompressed_size=snapshot['uncompressed_bytes'];compressed_size=snapshot['compressed_bytes'];sha256_hash=snapshot['sha256']
    duration=round(time.time()-start_time,2)

    # 4. Prune local disk copies (keep settings.backup_keep_local)
    pruned_local = prune_local_backups(keep=settings.backup_keep_local,protected=[final_gz_path]) if rotate else []

    # 5. Off-disk upload to Google Shared Drive
    drive_info: dict[str, Any] = {
        "uploaded": False,
        "folder_id": settings.backup_drive_folder_id,
        "file_id": None,
        "link": None,
        "status": "disabled",
        "error": None,
        "pruned": [],
        "retention": "kept; automatic remote pruning is disabled",
    }

    if upload_to_drive and settings.backup_drive_folder_id:
        drive_res = _upload_to_google_drive(final_gz_path, settings.backup_drive_folder_id)
        drive_info.update(drive_res)
        if drive_info["uploaded"]:
            drive_info["pruned"] = prune_drive_backups(
                settings.backup_drive_folder_id, keep_days=settings.backup_keep_drive
            )

    result = {
        "ok": True,
        "filename": final_gz_name,
        "path": str(final_gz_path),
        "tag": tag,
        "created_at": now_utc.isoformat(),
        "created_at_local": now_local.isoformat(),
        "uncompressed_bytes": uncompressed_size,
        "compressed_bytes": compressed_size,
        "sha256": sha256_hash,
        "compressed_sha256":snapshot["compressed_sha256"],
        "duration_seconds": duration,
        "pruned_local": pruned_local,
        "drive": drive_info,
    }

    global _last_backup_cache
    _last_backup_cache = result
    _write_persisted_status(result)

    return result


def create_pre_migration_backup() -> dict[str, Any] | None:
    """Fail closed before the first schema mutation of an existing database."""
    db_path=get_sqlite_db_path()
    if not db_path or not db_path.is_file() or db_path.stat().st_size==0:return None
    try:return create_backup(tag='pre_migration',upload_to_drive=False,rotate=False)
    except Exception as exc:raise BackupError('Pre-migration snapshot failed; database schema was not changed') from exc


def prune_local_backups(keep: int = 7, *, protected=()) -> list[str]:
    """Delete oldest local backups, retaining the newest `keep` copies."""
    backups_dir = get_local_backups_dir()
    keep=max(1,int(keep))  # never remove the only local recovery copy
    files = [f for f in backups_dir.glob("scrapper_*.db.gz") if f.is_file()]
    # Sort descending by mtime (newest first)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    protected={pathlib.Path(p).resolve() for p in protected}
    pruned = []
    if len(files) > keep:
        to_delete = files[keep:]
        for f in to_delete:
            if f.resolve() in protected:continue
            try:
                from .file_use import pin
                with pin([f],exclusive=True):f.unlink()
                pruned.append(f.name)
                logger.info("Pruned old local backup: %s", f.name)
            except Exception as exc:
                logger.warning("Failed to delete old backup %s: %s", f.name, exc)
    return pruned


def _upload_to_google_drive(file_path: pathlib.Path, folder_id: str) -> dict[str, Any]:
    """Upload a backup file into the Google Drive folder."""
    try:
        from .drive_sync import get_drive_service
        from .studio.drive_store import _upload
        from .delivery_archive import record_transfer,transfer_scope
        from .file_use import pin
        service=get_drive_service()
        with pin([file_path]),transfer_scope('DatabaseBackup:'+file_path.name[:75],None):
            drive_file=_upload(service,file_path,folder_id,file_path.name)
            record_transfer('DatabaseBackup:'+file_path.name[:75],None,drive_file,'sqlite_snapshot')
        file_id = drive_file.get("id")
        link = drive_file.get("webViewLink")
        logger.info("Successfully uploaded backup to Google Drive folder %s: %s (%s)", folder_id, file_path.name, file_id)

        return {
            "uploaded": True,
            "status": "synced",
            "verification":drive_file["verification"],
            "file_id": file_id,
            "link": link,
            "error": None,
        }
    except Exception as exc:
        err_msg = str(exc)
        logger.warning("Google Drive backup upload failed: %s", err_msg)
        # Check for service account quota / My Drive limitation
        if "storageQuotaExceeded" in err_msg or "storage quota" in err_msg or "cannot be shared" in err_msg:
            status_desc = "Local disk only — Drive off-disk copy pending Shared Drive setup"
        else:
            status_desc = f"Drive error: {err_msg[:120]}"

        return {
            "uploaded": False,
            "status": "pending_shared_drive" if "quota" in err_msg.lower() else "error",
            "file_id": None,
            "link": None,
            "error": status_desc,
        }


def prune_drive_backups(folder_id: str, keep_days: int = 30) -> list[str]:
    """Keep remote snapshots until managed-copy/reference checks can authorize pruning.

    Folder membership and age do not prove ownership or another usable backup.
    In particular, the old implementation could permanently delete unrelated
    owner files sharing the backup folder. No remote mutation is made here.
    """
    logger.info("Remote database snapshots retained; automatic Drive pruning is disabled")
    return []


def get_backup_status() -> dict[str, Any]:
    """Return comprehensive status for settings UI and health checks."""
    global _last_backup_cache
    last = _read_persisted_status()  # persisted evidence is shared across app processes

    backups_dir = get_local_backups_dir()
    local_files = [f for f in backups_dir.glob("scrapper_*.db.gz") if f.is_file()]
    local_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    items = []
    for f in local_files[:10]:
        st = f.stat()
        mtime = datetime.datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()
        items.append({
            "filename": f.name,
            "size_bytes": st.st_size,
            "created_at": mtime,
            "download_url": f"/api/backup/download/{f.name}",
        })

    # Calculate next scheduled run (3:00 AM Eastern)
    eastern = ZoneInfo(settings.backup_timezone)
    now_eastern = datetime.datetime.now(eastern)
    target = now_eastern.replace(hour=settings.backup_hour, minute=0, second=0, microsecond=0)
    if now_eastern >= target:
        target += datetime.timedelta(days=1)
    next_scheduled_iso = target.astimezone(timezone.utc).isoformat()

    # Check for staleness (> 26 hours since last successful backup)
    is_stale = False
    warning = None

    if local_files:
        newest_age_sec = time.time() - local_files[0].stat().st_mtime
        if newest_age_sec > 26 * 3600:
            is_stale = True
            hours_old = round(newest_age_sec / 3600, 1)
            warning = f"Database backup is {hours_old} hours old (exceeds 26h threshold)"
    else:
        is_stale = True
        warning = "No database backups exist on disk yet"

    # Evaluate Drive status
    drive_status = "disabled"
    if settings.backup_drive_folder_id:
        if last and last.get("drive", {}).get("uploaded"):
            drive_status = "synced"
        elif last and last.get("drive", {}).get("error"):
            drive_status = last["drive"]["error"]
        else:
            drive_status = "configured"

    return {
        "ok": True,
        "enabled": settings.backup_enabled,
        "database_type": "sqlite" if get_sqlite_db_path() else "postgres",
        "last_backup": last,
        "is_stale": is_stale,
        "warning": warning,
        "local_copies_count": len(local_files),
        "local_copies": items,
        "drive_folder_id": settings.backup_drive_folder_id,
        "drive_status": drive_status,
        "next_scheduled_run": next_scheduled_iso,
        "retention": {
            "keep_local": settings.backup_keep_local,
            "keep_drive_days": settings.backup_keep_drive,
        },
    }


_last_stale_warning: str | None = None


def maybe_run_nightly_backup() -> None:
    """Called every scheduler tick. Triggers backup at 3:00 am Eastern once daily."""
    global _last_nightly_date
    if not settings.backup_enabled:
        return

    now_eastern = datetime.datetime.now(ZoneInfo(settings.backup_timezone))
    today_str = now_eastern.strftime("%Y-%m-%d")

    # Run between 03:00 and 03:59 Eastern
    if now_eastern.hour == settings.backup_hour and _last_nightly_date != today_str:
        logger.info("Executing scheduled nightly database backup at 3:00 AM Eastern...")
        _last_nightly_date = today_str
        try:
            create_backup(tag="nightly", upload_to_drive=True)
        except Exception as exc:
            logger.error("Nightly database backup failed: %s", exc, exc_info=True)

    # Periodic staleness warning logging (once per day)
    status = get_backup_status()
    global _last_stale_warning
    if status["is_stale"] and _last_stale_warning != today_str:   # once a day, whatever the tick length
        _last_stale_warning = today_str
        logger.warning("DATABASE BACKUP STALENESS WARNING: %s", status["warning"])
