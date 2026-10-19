"""
Google Drive synchronization & ingestion service for SocialPilot AI.
Extracts video files from Google Drive folders, uploads new channel scrapes,
and synchronizes with the PostgreSQL / SQLite QueueItem table.
"""
import io
import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("scrapper.drive_sync")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
]

LOCAL_KEY_CANDIDATES = [
    "/Users/kwasiyeboah/m3/omnistream/service_account.json",
    os.path.expanduser("~/m3/omnistream/service_account.json"),
    "service_account.json",
]

DEFAULT_PARENT_FOLDER = "1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo"  # Movie Clips


def _default_destination() -> list:
    """The connected Upload-Post channels new Shorts post to (empty if Upload-Post is unreachable;
    the scheduler fills it in later)."""
    from .social import queue_manager
    return queue_manager.default_destination()


def extract_folder_id(url_or_id: str) -> str:
    """Extract folder ID whether user passed full URL or bare ID."""
    raw = (url_or_id or "").strip()
    match = re.search(r"folders/([a-zA-Z0-9_-]+)", raw)
    if match:
        return match.group(1)
    match_param = re.search(r"id=([a-zA-Z0-9_-]+)", raw)
    if match_param:
        return match_param.group(1)
    return raw.split("?")[0].strip("/")


def extract_drive_file_id(url_or_id: str) -> Optional[str]:
    """Extract file ID from a Google Drive file link."""
    raw = (url_or_id or "").strip()
    match = re.search(r"/file/d/([a-zA-Z0-9_-]+)", raw)
    if match:
        return match.group(1)
    match_id = re.search(r"id=([a-zA-Z0-9_-]+)", raw)
    if match_id:
        return match_id.group(1)
    if len(raw) >= 20 and "/" not in raw:
        return raw
    return None


def get_drive_service():
    """Build and return an authorized Google Drive v3 client."""
    from .e2e_safety import deny_live
    deny_live("Google Drive access")
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_env = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    creds = None

    if sa_env:
        sa_env = sa_env.strip()
        if sa_env.startswith("{"):
            try:
                info = json.loads(sa_env)
                creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
            except Exception as e:
                logger.error(f"Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON: {e}")
        elif os.path.exists(sa_env):
            creds = service_account.Credentials.from_service_account_file(sa_env, scopes=SCOPES)

    if not creds:
        for candidate in LOCAL_KEY_CANDIDATES:
            if os.path.exists(candidate):
                logger.info(f"Using local Google Service Account key: {candidate}")
                creds = service_account.Credentials.from_service_account_file(candidate, scopes=SCOPES)
                break

    if not creds:
        raise RuntimeError(
            "Google Service Account key not found. Please set GOOGLE_SERVICE_ACCOUNT_JSON or place service_account.json."
        )

    return build("drive", "v3", credentials=creds)


def clean_video_title(raw_name: str) -> Tuple[str, str]:
    """
    Cleans raw video filename into human-readable title and extracted hashtags.
    """
    stem = Path(raw_name).stem
    hashtags = re.findall(r"#\w+", stem)
    tags_str = " ".join(hashtags) if hashtags else "#shorts #movieclips"

    title = re.sub(r"#\w+", "", stem)
    title = re.sub(r"_[a-zA-Z0-9_-]{10,12}$", "", title)
    title = title.replace("_", " ").strip()
    title = re.sub(r"\s+", " ", title)

    if not title:
        title = Path(raw_name).stem

    return title, tags_str


def find_or_create_subfolder(service, folder_name: str, parent_id: str) -> str:
    """Find existing subfolder in parent or create it (supports Shared Drives)."""
    clean_name = folder_name.strip()
    if not clean_name: raise ValueError("Folder name is empty")
    quoted_name = clean_name.replace("\\", "\\\\").replace("'", "\\'")
    parent_id = extract_folder_id(parent_id)
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", parent_id): raise ValueError("Invalid Drive folder ID")
    query = (
        f"name = '{quoted_name}' and '{parent_id}' in parents and "
        f"mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    res = service.files().list(
        q=query,
        fields="files(id, name)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]

    meta = {
        "name": clean_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    created = service.files().create(body=meta, fields="id, name", supportsAllDrives=True).execute()
    return created["id"]


def upload_file_to_drive(service, file_path: str, parent_folder_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
    """Uploads a local video file to a Google Drive folder (supports Shared Drives)."""
    from googleapiclient.http import MediaFileUpload

    file_name = filename or os.path.basename(file_path)
    file_metadata = {
        "name": file_name,
        "parents": [parent_folder_id],
    }
    media = MediaFileUpload(file_path, resumable=True)
    res = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, webViewLink, thumbnailLink",
        supportsAllDrives=True,
    ).execute()
    return res


def download_drive_file(drive_link_or_id: str, dest_dir: Optional[str] = None) -> Path:
    """Downloads a video file from Google Drive to a local temporary file."""
    from googleapiclient.http import MediaIoBaseDownload

    file_id = extract_drive_file_id(drive_link_or_id)
    if not file_id:
        raise ValueError(f"Could not extract Google Drive file ID from {drive_link_or_id}")

    service = get_drive_service()
    meta = service.files().get(fileId=file_id, fields="id, name, mimeType, size", supportsAllDrives=True).execute()
    # Remote names are untrusted metadata. Never use them as a local path.
    # A unique, exclusive file also prevents collisions and pre-existing symlinks.
    target_dir = Path(dest_dir or tempfile.gettempdir())
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(str(meta.get("name", ""))).suffix.lower()
    if suffix not in (".mp4", ".mov", ".webm", ".mkv"):
        suffix = ".mp4"
    from .config import settings
    if int(meta.get("size",0))>settings.max_upload_bytes:raise ValueError("Drive video exceeds upload size limit")
    fd, temporary = tempfile.mkstemp(prefix="scrapper-drive-", suffix=".part", dir=target_dir)
    target_path = Path(temporary + suffix)
    try:
        from .file_use import pin,assert_current
        from . import control
        from .uploads import validate_video
        with pin([temporary,target_path],exclusive=True):
            request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
            with os.fdopen(fd, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request, chunksize=1024*1024)
                done = False
                while not done:
                    control.check()
                    _, done = downloader.next_chunk()
                    if fh.tell()>settings.max_upload_bytes:raise ValueError('Drive video exceeds upload size limit')
                fh.flush();os.fsync(fh.fileno())
            validate_video(Path(temporary))
            assert_current()
            os.link(temporary, target_path)
            return target_path
    except BaseException:
        # Includes cancellation: no half-file becomes a usable download.
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    finally:
        Path(temporary).unlink(missing_ok=True)


def trash_drive_file(drive_link_or_id: str) -> bool:
    """Legacy entry point fails closed until remote version/ref ownership exists.

    Callers cannot prove that a newer render preserves the old version's bytes,
    nor that a remote link is not used by a transfer, history or another project.
    """
    raise ValueError("Automatic Drive trash is disabled: keep remote versions until exact alternate-copy and reference ownership can be verified")


def sync_drive_to_queue(
    folder_url_or_id: str,
    default_pipeline: str,
    auto_approve: bool,
    db_session,
) -> Dict[str, Any]:
    """
    Scan a Google Drive folder and its channel subfolders, creating QueueItem records.
    """
    from .models import QueueItem
    from .logbus import logbus

    folder_id = extract_folder_id(folder_url_or_id)
    if not folder_id:
        raise ValueError("Invalid folder ID or URL provided")

    service = get_drive_service()

    # Determine parent folder name
    parent_name = "Movie Clips"
    try:
        parent_meta = service.files().get(fileId=folder_id, fields="name", supportsAllDrives=True).execute()
        if parent_meta.get("name"):
            parent_name = parent_meta["name"]
    except Exception:
        pass

    # 1. Discover subfolders (channels)
    subfolders_query = f"'{folder_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
    folders_result = service.files().list(
        q=subfolders_query,
        fields="nextPageToken, files(id, name)",
        pageSize=100,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    # Finished LongForm breakdowns are stored, not imported as clips.
    subfolders = [f for f in folders_result.get("files", []) if f["name"].strip() != "LongForm Studio"]

    targets: List[Tuple[str, str]] = []
    if subfolders:
        for sf in subfolders:
            targets.append((sf["id"], sf["name"]))
    else:
        targets.append((folder_id, default_pipeline or parent_name))

    from sqlalchemy import func
    # New imports go to the back of the posting order.
    next_position = db_session.query(func.max(QueueItem.position)).scalar() or 0

    total_scanned = 0
    added_count = 0
    skipped_count = 0
    channels_summary = []

    from . import control, undo
    created_ids: list[int] = []
    new_rows: list = []
    try:
      for fid, channel_name in targets:
          control.check()
          control.progress(f"scanning {channel_name}")
          video_query = (
              f"'{fid}' in parents and trashed = false and "
              f"(mimeType contains 'video/' or name contains '.mp4' or name contains '.mov' or name contains '.webm')"
          )

          page_token = None
          channel_videos = []
          while True:
              resp = service.files().list(
                  q=video_query,
                  fields="nextPageToken, files(id, name, mimeType, webViewLink, thumbnailLink, size)",
                  pageToken=page_token,
                  pageSize=100,
                  supportsAllDrives=True,
                  includeItemsFromAllDrives=True,
              ).execute()
              channel_videos.extend(resp.get("files", []))
              page_token = resp.get("nextPageToken")
              if not page_token:
                  break

          total_scanned += len(channel_videos)
          ch_added = 0
          ch_skipped = 0

          for f in channel_videos:
              file_id = f["id"]
              name = f["name"]
              web_link = f.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
              thumb = f.get("thumbnailLink")

              existing = (
                  db_session.query(QueueItem)
                  .filter(
                      (QueueItem.drive_link == web_link) |
                      ((QueueItem.video_name == name) & (QueueItem.pipeline == channel_name))
                  )
                  .first()
              )

              if existing:
                  # Update source if it was missing full folder path
                  if existing.source != f"{parent_name} / {channel_name}":
                      existing.source = f"{parent_name} / {channel_name}"
                  ch_skipped += 1
                  skipped_count += 1
                  continue

              title, tags = clean_video_title(name)
              item_status = "ready" if auto_approve else "review"
              next_position += 1

              new_item = QueueItem(
                  pipeline=channel_name,
                  video_name=name,
                  drive_link=web_link,
                  thumb_path=thumb,
                  source=f"{parent_name} / {channel_name}",
                  title=title,
                  description=title,
                  tags=tags,
                  status=item_status,
                  position=next_position,
                  accounts=_default_destination(),   # owner (2026-10-14): new Shorts post to the connected channels
              )
              db_session.add(new_item)
              new_rows.append(new_item)
              ch_added += 1
              added_count += 1

          db_session.commit()
          created_ids.extend(r.id for r in new_rows)
          new_rows.clear()
          channels_summary.append({
              "channel": channel_name,
              "folder": f"{parent_name} / {channel_name}",
              "found": len(channel_videos),
              "added": ch_added,
              "skipped": ch_skipped
          })
    finally:
        # Undo point for whatever was added, even if the sync was stopped midway.
        if created_ids:
            undo.add_created(db_session, "queue", f"Drive sync added {len(created_ids)} videos", created_ids)
            db_session.commit()

    msg = f"Synced {added_count} new video items across {len(targets)} channel(s). ({skipped_count} existing)"
    logbus.log("info", "drive_sync_success", msg)

    return {
        "ok": True,
        "message": msg,
        "parent_folder": parent_name,
        "total_scanned": total_scanned,
        "added": added_count,
        "skipped": skipped_count,
        "channels": channels_summary,
    }


def ingest_channel_to_drive(
    url: str,
    parent_folder_id: str,
    parent_folder_name: str,
    channel_name: Optional[str],
    max_videos: int,
    auto_approve: bool,
    db_session,
) -> Dict[str, Any]:
    """Compatibility entry point: complete Shorts archives, never posting items.

    Explicit old publication options fail instead of silently promoting media.
    Full inventory is returned before callers should start a new archive job.
    Existing restart jobs can use this path to finish their archive safely.
    """
    if auto_approve:
        raise ValueError("Archives cannot automatically approve or publish media")
    from . import channel_archive, worker_leases
    found=channel_archive.discover(url,parent_folder_id or DEFAULT_PARENT_FOLDER,['shorts'])
    with worker_leases.scope('archive:'+found['channel_id']):
        return channel_archive.run(found['channel_id'],retry=True)
