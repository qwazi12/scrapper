"""Retain delivery lineage and exact media before queue-row reclamation.

Remote copies are retained. An earlier Drive version may be the only remaining
copy of different bytes; a newer successful upload is not proof otherwise.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .auth import require_token
from .budget_reservations import lock
from .config import settings
from .db import SessionLocal
from .export_versions import _blob
from .file_response import FileResponse
from .models import DeliveryArchive, FileUse, QueueItem, RemoteCopyEvidence, WorkerLease
from .publication_journal import append, digest, identity, unresolved_late_receipt

router = APIRouter(prefix="/api/delivery-history", dependencies=[Depends(require_token)])
_transfer = ContextVar("remote_transfer_receipt", default=None)


@contextmanager
def transfer_scope(resource, project_id):
    token = _transfer.set((resource, project_id))
    try: yield
    finally: _transfer.reset(token)


def record_created(transfer, expected_sha256, expected_md5, size, name):
    from .worker_leases import bind
    from .file_use import suspend
    resource, project_id = _transfer.get() or ("UnassignedTransfer:" + expected_sha256, None)
    with bind(None), suspend(), SessionLocal() as s:
        remote_record(s, resource, str(transfer["id"]), "remote_creation_receipt",
            {"project_id": project_id, "link": transfer.get("webViewLink"), "name": name,
             "expected_sha256": expected_sha256, "expected_md5": expected_md5, "expected_bytes": size,
             "verification_recorded": False, "scope": "remote ID returned by upload; independent remote verification still required"})
        s.commit()



def media_type(path):
    with Path(path).open("rb") as file:
        head = file.read(32)
    if head.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if head.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP": return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"qt  "): return "video/mp4"
    if head.startswith(b"\x1aE\xdf\xa3"): return "video/webm"
    return "application/octet-stream"


def remote_record(s, resource: str, file_id: str, action: str, evidence: dict):
    if not file_id:
        return
    payload = json.loads(json.dumps(evidence, default=str))
    key = digest([resource, file_id, action, payload])
    if s.get(RemoteCopyEvidence, key) or any(isinstance(r, RemoteCopyEvidence) and r.id == key for r in s.new):
        return
    s.add(RemoteCopyEvidence(id=key, provider="drive", file_id=file_id, resource=resource,
                             action=action, evidence=payload))


def retain_remote_versions(s, project, info: dict, action: str):
    resource = f"{type(project).__name__}:{identity(project)}"
    for role, field, proof_field in (("video", "file_id", "verification"), ("thumbnail", "thumb_id", "thumb_verification")):
        if info.get(field):
            proof = info.get(proof_field) or {}
            remote_record(s, resource, str(info[field]), action, {"role": role, "project_id": project.id,
                "project_identity": identity(project), "rendered_at": info.get("rendered_at"),
                "link": info.get("link" if role == "video" else "thumb_link"),
                "verification": proof, "verification_recorded": bool(proof.get("sha256") and
                    proof.get("remote_file_id") == info[field]),
                "retention": "kept; automatic remote trash is disabled"})


def _readable(s, paths):
    """A short writer transaction excludes new cooperating file mutations.

    Existing exclusive users still win. Reader leases are compatible with a
    verified copy. No second writer session is opened while this mutex is held.
    """
    from .file_use import _active, overlap
    own = {h.resource for h in _active.get()}
    paths = [Path(p).resolve() for p in paths]
    from .worker_leases import clock
    now = clock(s)
    for use in s.query(FileUse).join(WorkerLease, WorkerLease.resource == FileUse.resource).filter(
            FileUse.exclusive.is_(True), WorkerLease.expires_at > now):
        if use.resource not in own and any(overlap(p, other) for p in paths for other in use.paths):
            raise HTTPException(409, "Media is being changed; retained delivery history and queue row were preserved")


def retain(s, item: QueueItem) -> str:
    from .worker_leases import live
    lock(s)
    if (live(f"publication:{item.id}") or unresolved_late_receipt(s, item) or
            any(e.get("state") in ("intent", "unknown", "accepted") for e in item.publish_requests or [])):
        raise HTTPException(409, "Reconcile the unresolved delivery before removing its queue row")
    snapshot = {c.key: getattr(item, c.key) for c in inspect(QueueItem).columns}
    snapshot = json.loads(json.dumps(snapshot, default=str))
    key = digest([identity(item), snapshot])
    if s.get(DeliveryArchive, key) or any(isinstance(r, DeliveryArchive) and r.id == key for r in s.new):
        return key
    assets = []
    candidates = [("video", item.video_path), ("thumbnail", item.thumb_path)]
    candidates.extend(("image", path) for path in item.media_files or [])
    paths = [settings.data_path / path for _, path in candidates if path and "://" not in path]
    _readable(s, paths)
    for role, raw in candidates:
        if not raw:
            continue
        if "://" in raw:
            asset = {"available": False, "reason": "remote media retained as a reference; no local byte claim"}
        else:
            asset = _blob(settings.data_path / raw, settings.data_path)
        assets.append({"role": role, "mime": media_type(asset["blob"]) if asset.get("available") else "application/octet-stream", **asset})
    manifest = {"schema": 1, "queue_identity": identity(item), "snapshot": snapshot, "assets": assets,
        "delivery_scope": "recorded provider observations and owner labels; archive is not a new publication confirmation",
        "remote_retention": "All Drive copies kept. Storage reclamation never assumes a newer upload replaces older bytes."}
    s.add(DeliveryArchive(id=key, queue_id=item.id, queue_identity=identity(item), manifest=manifest))
    if item.drive_link:
        from .drive_sync import extract_drive_file_id
        file_id = extract_drive_file_id(item.drive_link)
        if file_id:
            remote_record(s, "QueueItem:" + identity(item), file_id,
                "retained_queue_reference", {"queue_id": item.id, "link": item.drive_link,
                    "verification_recorded": False, "reason": "Queue cleanup retains the remote copy and historical reference"})
    append(s, item.id, "retention", "queue_archived", {"archive_id": key, "queue_identity": identity(item)})
    return key


@event.listens_for(Session, "before_flush")
def retain_deleted_deliveries(s, context, instances):
    for item in tuple(s.deleted):
        if isinstance(item, QueueItem) and (item.status in ("posted", "archived") or item.published_at or item.publish_requests):
            retain(s, item)


@router.get("")
def listing(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    with SessionLocal() as s:
        return {"retention": "Remote files kept; exact available local files and delivery evidence retained", "deliveries": [
            {"id": row.id, "queue_id": row.queue_id, "created_at": row.created_at,
             "title": row.manifest["snapshot"].get("title"), "pipeline": row.manifest["snapshot"].get("pipeline"),
             "published_at": row.manifest["snapshot"].get("published_at"),
             "status": row.manifest["snapshot"].get("status"), "asset_count": len(row.manifest["assets"])}
            for row in s.query(DeliveryArchive).order_by(DeliveryArchive.created_at.desc(), DeliveryArchive.id.desc()).offset(offset).limit(limit)],
            "remote_copies": [{"id": row.id, "resource": row.resource, "file_id": row.file_id,
                "action": row.action, "evidence": row.evidence, "created_at": row.created_at}
                for row in s.query(RemoteCopyEvidence).order_by(RemoteCopyEvidence.created_at.desc(), RemoteCopyEvidence.id.desc()).offset(offset).limit(limit)],
            "next_offset": offset + limit,
            "has_more": s.query(DeliveryArchive).count() > offset + limit or s.query(RemoteCopyEvidence).count() > offset + limit}


def _get(s, key):
    row = s.get(DeliveryArchive, key)
    if not row:
        raise HTTPException(404, "Retained delivery not found")
    return row


@router.get("/{key}")
def detail(key: str):
    with SessionLocal() as s:
        row = _get(s, key)
        return {"id": row.id, "manifest": row.manifest, "created_at": row.created_at}


@router.get("/{key}/file/{index}")
def file(key: str, index: int):
    from .studio.media_quality import file_hash
    with SessionLocal() as s:
        row = _get(s, key)
        assets = row.manifest["assets"]
        if not 0 <= index < len(assets) or not assets[index].get("available"):
            raise HTTPException(404, "This retained delivery has no local file for that asset")
        asset = assets[index]
        path = Path(asset["blob"]).resolve()
        if not path.is_relative_to((settings.data_path / "export_history" / "blobs").resolve()) or not path.is_file() or file_hash(path) != asset["sha256"]:
            raise HTTPException(409, "Retained delivery file failed its integrity check")
        mime = asset["mime"]
        safe = {"video/mp4", "video/webm", "image/jpeg", "image/png", "image/webp"}
        return FileResponse(path, media_type=mime if mime in safe else "application/octet-stream",
                            filename=None if mime in safe else "retained-asset.bin",
                            headers={"X-Content-Type-Options": "nosniff"})


def record_transfer(resource: str, project_id: int, transfer: dict, role: str):
    """A verified remote transfer receipt survives later content-owner loss."""
    from .worker_leases import bind
    from .file_use import suspend
    with bind(None), suspend(), SessionLocal() as s:
        remote_record(s, resource, str(transfer["id"]), "verified_transfer_receipt",
            {"project_id": project_id, "role": role, "link": transfer.get("webViewLink"),
             "verification": transfer.get("verification"),
             "scope": "independently read remote checksum/size against the pinned local bytes; no deletion authorized"})
        s.commit()
