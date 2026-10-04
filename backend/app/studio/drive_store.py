"""Keep every finished breakdown in Google Drive, folder "LongForm Studio".

Uploads final.mp4 + thumbnail in chunks (Stop-able), records the links on the
project (`drive`) and on its queue item (`drive_link`, so posting still works
if the server's local copy is gone). Re-saving a re-render replaces the old
Drive files (moved to trash, recoverable for 30 days).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from .. import control, costs
from ..config import settings
from ..db import SessionLocal
from .. import logbus
from ..models import QueueItem, StudioProject
from . import runner

logger = logging.getLogger("scrapper.studio.drive")

FOLDER_NAME = "LongForm Studio"
YOUTUBE_FOLDER = "1_VLhKfSEYZFyPBXi7kYu4uw94JUronDP"  # parent of Movie Clips
CHUNK = 8 * 1024 * 1024
ALL = {"supportsAllDrives": True}

NO_QUOTA_HELP = (
    "Google Drive refused the upload: the service account has no storage of its own "
    "(it can only save into a Shared Drive). Fix: in Google Drive create a Shared Drive, "
    "add the service account as Content manager, make a \"LongForm Studio\" folder in it and "
    "set LONGFORM_DRIVE_FOLDER_ID on Railway to that folder's id. Then press Save to Drive again."
)


class DriveStoreError(Exception):
    pass


def _explain(exc: Exception) -> str:
    msg = str(exc)
    if "storageQuotaExceeded" in msg or "storage quota" in msg:
        return NO_QUOTA_HELP
    return f"Drive error: {msg[:300]}"


def folder_id(service) -> str:
    if settings.longform_drive_folder_id:
        return settings.longform_drive_folder_id
    q = (f"name = '{FOLDER_NAME}' and '{YOUTUBE_FOLDER}' in parents and "
         "mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    found = service.files().list(q=q, fields="files(id)", includeItemsFromAllDrives=True, **ALL).execute()
    if found.get("files"):
        return found["files"][0]["id"]
    made = service.files().create(body={"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder",
                                        "parents": [YOUTUBE_FOLDER]}, fields="id", **ALL).execute()
    logbus.log("info", "longform_drive_folder", f"Created Drive folder '{FOLDER_NAME}'")
    return made["id"]


def _upload(service, path, parent: str, name: str) -> dict[str, Any]:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), resumable=True, chunksize=CHUNK)
    req = service.files().create(body={"name": name, "parents": [parent]}, media_body=media,
                                 fields="id, webViewLink", **ALL)
    resp = None
    size = path.stat().st_size
    while resp is None:
        control.check()  # Stop lands between 8 MB chunks
        status, resp = req.next_chunk()
        if status:
            control.progress(f"Uploading {name}: {int(status.progress() * 100)}% of {round(size / 1e6)} MB")
    costs.record_free("drive")
    return resp


def _safe_name(title: str) -> str:
    return "".join(ch for ch in title if ch not in '/\\:*?"<>|').strip() or "breakdown"


def save(project_id: int) -> dict[str, Any]:
    """Upload the current render. Runs inside a control job (Stop-able)."""
    from ..drive_sync import get_drive_service, trash_drive_file

    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        if not p or not p.render:
            raise DriveStoreError("Render the video first")
        root = runner.project_dir(project_id)
        video = root / p.render["file"]
        thumb = root / p.render["thumbnail"] if p.render.get("thumbnail") else None
        if not video.exists():
            raise DriveStoreError("The rendered file is missing on the server — render again")
        old = dict(p.drive or {})
        p.drive = {**old, "status": "uploading", "error": None}
        s.commit()
        title = _safe_name(p.title)
        rendered_at = p.render.get("rendered_at") or ""

    try:
        service = get_drive_service()
        parent = folder_id(service)
        try:
            meta = service.files().get(fileId=parent, fields="name,driveId", **ALL).execute()
        except Exception as exc:
            raise DriveStoreError(f"Drive folder '{parent}' could not be accessed. Check LONGFORM_DRIVE_FOLDER_ID.") from exc
        if not meta.get("driveId"):
            # Google refuses every file a service account tries to store in a My Drive
            # folder (it has no storage of its own). Say exactly that instead of letting
            # the upload fail with a quota error (restored 2026-10-03; removing this
            # check did not "unblock" anything — the upload itself is what Google refuses).
            n = len(service.drives().list(pageSize=10).execute().get("drives", []))
            raise DriveStoreError(
                f"The folder \"{meta.get('name', parent)}\" was found, but it is in a personal My Drive, and the "
                f"robot account is a member of {n} Shared Drive(s). Google only lets a robot account save files "
                "inside a Shared Drive. Fix: in Google Drive → Shared drives → New, add "
                "omnistream-bot@manhwa-engine.iam.gserviceaccount.com as Content manager, then move the "
                "\"LongForm Studio\" folder into it (the folder ID stays the same). Then press Retry.")
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        v = _upload(service, video, parent, f"{title} — Trailer Breakdown ({stamp}).mp4")
        t = _upload(service, thumb, parent, f"{title} — Thumbnail ({stamp}){thumb.suffix}") if thumb and thumb.exists() else None
    except control.Cancelled:
        _set(project_id, {**old, "status": "stopped", "error": "stopped by user"})
        raise
    except Exception as exc:  # noqa: BLE001 — record a readable reason, keep the old copy
        reason = str(exc) if isinstance(exc, DriveStoreError) else _explain(exc)
        _set(project_id, {**old, "status": "error", "error": reason})
        logbus.log("error", "longform_drive_failed", f"Studio #{project_id}: {reason}", project=project_id)
        raise DriveStoreError(reason) from exc

    info = {"status": "saved", "error": None, "folder_id": parent,
            "file_id": v["id"], "link": v.get("webViewLink"),
            "thumb_id": t["id"] if t else None, "thumb_link": t.get("webViewLink") if t else None,
            "rendered_at": rendered_at,
            "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        p.drive = info
        if p.queue_item_id and (item := s.get(QueueItem, p.queue_item_id)) and item.status not in ("posted", "archived"):
            item.drive_link = info["link"]
        s.commit()
    # Replace, not pile up: the previous upload of this project goes to trash.
    for fid in (old.get("file_id"), old.get("thumb_id")):
        if fid and fid not in (info["file_id"], info["thumb_id"]):
            try:
                trash_drive_file(fid)
            except Exception as exc:  # noqa: BLE001
                logger.warning("could not trash old Drive copy %s: %s", fid, exc)
    logbus.log("info", "longform_drive_saved", f"Studio #{project_id}: saved to Drive '{FOLDER_NAME}'",
               project=project_id, link=info["link"])
    try:
        from .archive import free_footage
        free_footage(project_id)        # the video is safe in Drive: free its ~300 MB of footage
    except Exception as exc:  # noqa: BLE001 — freeing space must never fail the save
        logger.warning("could not free footage of #%s: %s", project_id, exc)
    return info


def _set(project_id: int, drive: dict) -> None:
    with SessionLocal() as s:
        p = s.get(StudioProject, project_id)
        if p:
            p.drive = drive
            s.commit()


def start(project_id: int, title: str, rid: int | None = None) -> str:
    """Background upload, recorded so a restart starts it again."""
    from .. import resume
    label = f"Save '{title}' to Drive" + (" (resumed after a restart)" if rid else "")
    return resume.tracked_thread("drive_save", label, "studio", {"project_id": project_id, "title": title},
                                 lambda: save(project_id), rid=rid, ref=project_id)


def _resume(p: dict, cp: dict, rid: int) -> str:
    return start(int(p["project_id"]), p.get("title", ""), rid)


from .. import resume as _resume_mod  # noqa: E402  (registered at import)
_resume_mod.launcher("drive_save")(_resume)
