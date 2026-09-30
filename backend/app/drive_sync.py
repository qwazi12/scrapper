"""
Google Drive synchronization service for SocialPilot AI.
Extracts video files from Google Drive folders (including channel subfolders)
and syncs them into the PostgreSQL / SQLite QueueItem table.
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("scrapper.drive_sync")

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]

LOCAL_KEY_CANDIDATES = [
    "/Users/kwasiyeboah/m3/omnistream/service_account.json",
    os.path.expanduser("~/m3/omnistream/service_account.json"),
    "service_account.json",
]


def extract_folder_id(url_or_id: str) -> str:
    """Extract folder ID whether user passed full URL or bare ID."""
    raw = (url_or_id or "").strip()
    match = re.search(r"folders/([a-zA-Z0-9_-]+)", raw)
    if match:
        return match.group(1)
    # Check if id= query param
    match_param = re.search(r"id=([a-zA-Z0-9_-]+)", raw)
    if match_param:
        return match_param.group(1)
    return raw.split("?")[0].strip("/")


def get_drive_service():
    """Build and return an authorized Google Drive v3 client."""
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
    Example:
    'Juice Betrays SAMCRO ｜ Sons Of Anarchy #shorts #sonsofanarchy_-tz-p4vEDgA.mp4'
    -> ('Juice Betrays SAMCRO ｜ Sons Of Anarchy', '#shorts #sonsofanarchy')
    """
    stem = Path(raw_name).stem

    # Extract any hashtags
    hashtags = re.findall(r"#\w+", stem)
    tags_str = " ".join(hashtags) if hashtags else "#shorts #movieclips"

    # Remove hashtags from title
    title = re.sub(r"#\w+", "", stem)

    # Strip trailing YouTube-style 11-char video IDs like _jV6NxeUhLXg or _-tz-p4vEDgA
    title = re.sub(r"_[a-zA-Z0-9_-]{10,12}$", "", title)

    # Clean punctuation and extra spaces
    title = title.replace("_", " ").strip()
    title = re.sub(r"\s+", " ", title)

    if not title:
        title = Path(raw_name).stem

    return title, tags_str


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

    # 1. Discover subfolders (channels)
    subfolders_query = f"'{folder_id}' in parents and trashed = false and mimeType = 'application/vnd.google-apps.folder'"
    folders_result = service.files().list(
        q=subfolders_query,
        fields="nextPageToken, files(id, name)",
        pageSize=100
    ).execute()
    subfolders = folders_result.get("files", [])

    # Target scan list: [(folder_id, channel_pipeline_name)]
    targets: List[Tuple[str, str]] = []
    if subfolders:
        for sf in subfolders:
            targets.append((sf["id"], sf["name"]))
    else:
        # Single folder without subfolders
        targets.append((folder_id, default_pipeline or "Movie Clips"))

    total_scanned = 0
    added_count = 0
    skipped_count = 0
    channels_summary = []

    for fid, channel_name in targets:
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
                pageSize=100
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

            # Check if this item already exists in the queue (by drive_link or video_name + pipeline)
            existing = (
                db_session.query(QueueItem)
                .filter(
                    (QueueItem.drive_link == web_link) |
                    ((QueueItem.video_name == name) & (QueueItem.pipeline == channel_name))
                )
                .first()
            )

            if existing:
                ch_skipped += 1
                skipped_count += 1
                continue

            title, tags = clean_video_title(name)
            item_status = "ready" if auto_approve else "review"

            new_item = QueueItem(
                pipeline=channel_name,
                video_name=name,
                drive_link=web_link,
                thumb_path=thumb,
                source=f"Google Drive ({channel_name})",
                title=title,
                description=title,
                tags=tags,
                status=item_status,
            )
            db_session.add(new_item)
            ch_added += 1
            added_count += 1

        db_session.commit()
        channels_summary.append({
            "channel": channel_name,
            "found": len(channel_videos),
            "added": ch_added,
            "skipped": ch_skipped
        })

    msg = (
        f"Synced {added_count} new video items across {len(targets)} channel(s). "
        f"({skipped_count} skipped as already present)"
    )
    logbus.log("info", "drive_sync_success", msg)

    return {
        "ok": True,
        "message": msg,
        "total_scanned": total_scanned,
        "added": added_count,
        "skipped": skipped_count,
        "channels": channels_summary,
    }
