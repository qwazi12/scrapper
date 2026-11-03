"""Idempotent conditional public-copy edits under the publisher's existing lease.

Metadata only: no Ready route, scheduler wake, provider call or media mutation.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import JSON, String, cast, literal, or_, update
from sqlalchemy.orm import Session

from .auth import require_token
from .db import get_session
from .models import MetadataMutationReceipt, PublicationEvent, QueueItem
from . import publication_journal, queue_sources, undo, worker_leases
from .budget_reservations import lock

# Outside /api/queue: saves must not trigger the generic poster-wake middleware.
router = APIRouter(prefix="/api/pending-metadata", dependencies=[Depends(require_token)])
PUBLIC_FIELDS = ("title", "description", "tags")
SCHEMA_VERSION = "pending-metadata-v1"
OPERATION_PATTERN = r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
# Retain comparison proofs without exposing private links, research or signed URLs.
HASHED_FIELDS = {"source", "research", "video_path", "thumb_path", "drive_link",
                 "media_url", "media_files", "publish_requests", "catchup"}


class ApprovalContext(BaseModel):
    """Auditable exact proposal context; not a substitute for source approval."""
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(min_length=1, max_length=120)
    workbook_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    applied_public_copy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_evidence_id: str | None = Field(default=None, pattern=OPERATION_PATTERN)


class MetadataEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=OPERATION_PATTERN)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    title: str | None = None
    description: str | None = None
    tags: str | None = None
    approval_context: ApprovalContext | None = None

    @model_validator(mode="after")
    def public_strings_only(self):
        fields = self.model_fields_set - {"operation_id", "expected_fingerprint", "approval_context"}
        if not fields or any(getattr(self, name) is None for name in fields):
            raise ValueError("Supply at least one public metadata string; null is not an edit")
        if "title" in fields and not self.title.strip():
            raise ValueError("Public title cannot be blank")
        return self


def _json(value):
    if isinstance(value, dt.datetime):
        aware = value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value
        return aware.astimezone(dt.timezone.utc).isoformat()
    raise TypeError(f"Unsupported fingerprint value: {type(value).__name__}")


def canonical(value):
    return json.dumps(value, default=_json, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _binding(s, item):
    binding = queue_sources.head(s, item)
    return {"id": binding.id, "revision": binding.revision} if binding else None


def fingerprint(s, item):
    values = {c.key: getattr(item, c.key) for c in QueueItem.__table__.columns}
    return digest({"schema_version": SCHEMA_VERSION, "queue": values, "source_binding": _binding(s, item)})


def require_pending(s, item):
    if not item:
        raise HTTPException(404, "Queue item not found")
    if (item.status not in ("review", "ready") or not item.created_at
            or item.published_at is not None or item.outstand_post_id
            or item.publish_requests or item.catchup):
        raise HTTPException(409, "Only never-submitted pending metadata can be edited here")
    if s.query(PublicationEvent.id).filter_by(queue_id=item.id,
            queue_identity=publication_journal.identity(item)).first():
        raise HTTPException(409, "Retained publication history requires review before metadata edits")


def view(s, item):
    protected = {}
    for column in QueueItem.__table__.columns:
        name = column.key
        if name not in PUBLIC_FIELDS:
            value = getattr(item, name)
            protected[name] = {"sha256": digest(value)} if name in HASHED_FIELDS else value
    return json.loads(canonical({"id": item.id, "schema_version": SCHEMA_VERSION,
        "queue_identity": publication_journal.identity(item), "fingerprint": fingerprint(s, item),
        "protected_fields": protected, "source_binding": _binding(s, item),
        **{name: getattr(item, name) for name in PUBLIC_FIELDS}}))


def _same_snapshot(item):
    predicates = []
    for column in QueueItem.__table__.columns:
        value = getattr(item, column.key)
        if isinstance(column.type, JSON):
            if value is None:
                predicates.append(or_(column.is_(None), cast(column, String) == "null"))
            else:
                predicates.append(cast(column, String) == cast(literal(value, type_=column.type), String))
        else:
            predicates.append(column.is_(None) if value is None else column == value)
    return predicates


def _replay(s, operation_id, request_hash):
    receipt = s.get(MetadataMutationReceipt, operation_id, populate_existing=True)
    if not receipt:
        return None
    if not hmac.compare_digest(receipt.request_hash, request_hash):
        raise HTTPException(409, "Operation ID already records a different request; preserve the saved receipt")
    return {**receipt.data["response"], "replayed": True}


@router.get("/receipts/{operation_id}")
def inspect_receipt(operation_id: str, s: Session = Depends(get_session)):
    receipt = s.get(MetadataMutationReceipt, operation_id)
    if not receipt:
        raise HTTPException(404, "No committed metadata operation receipt found")
    # Historical evidence only: this never asserts the queue still has that state.
    return {"operation_id": receipt.operation_id, "queue_id": receipt.queue_id,
            "queue_identity": receipt.queue_identity, "created_at": _json(receipt.created_at),
            "request_hash": receipt.request_hash, **receipt.data,
            "response": {**receipt.data["response"], "replayed": True}}


@router.get("/{item_id}")
def inspect_metadata(item_id: int, s: Session = Depends(get_session)):
    item = s.get(QueueItem, item_id, populate_existing=True)
    require_pending(s, item)
    return view(s, item)


@router.patch("/{item_id}")
def edit_metadata(item_id: int, req: MetadataEdit, s: Session = Depends(get_session)):
    changes = req.model_dump(include=set(PUBLIC_FIELDS), exclude_unset=True)
    approval = req.approval_context.model_dump() if req.approval_context else None
    request_hash = digest({"schema_version": SCHEMA_VERSION, "item_id": item_id,
                          "expected_fingerprint": req.expected_fingerprint, "changes": changes,
                          **({"approval_context": approval} if approval else {})})
    try:
        replay = _replay(s, req.operation_id, request_hash)
        if replay:
            return replay
        s.rollback()  # End the read before the lease's independent transaction.
        with worker_leases.scope(f"publication:{item_id}", session=s):
            lock(s)
            # Recheck under serialization: another identical request may have committed.
            replay = _replay(s, req.operation_id, request_hash)
            if replay:
                s.rollback()  # Release the mutex before the lease cleanup transaction.
                return replay
            item = s.get(QueueItem, item_id, populate_existing=True)
            require_pending(s, item)
            if not hmac.compare_digest(req.expected_fingerprint, fingerprint(s, item)):
                raise HTTPException(409, "Queue metadata or protected fields changed; refresh before editing")
            if approval:
                public_copy = {key: changes.get(key, getattr(item, key)) for key in PUBLIC_FIELDS}
                if digest(public_copy) != approval["applied_public_copy_sha256"]:
                    raise HTTPException(409, "Applied public copy differs from its exact approved context")
                if approval["canonical_evidence_id"]:
                    from . import drama_controls
                    evidence = drama_controls.binding(s, item)
                    if not evidence or evidence.id != approval["canonical_evidence_id"]:
                        raise HTTPException(409, "Canonical episode evidence does not match this queue identity")
            before = view(s, item)
            actual = {key: value for key, value in changes.items() if value != getattr(item, key)}
            if actual:
                if (item.research or {}).get("native_export_id"):
                    raise HTTPException(409, "Native copy is bound to its approved media; revise its treatment")
                queue_sources.require_copy_editable(s, item)
                predicates = _same_snapshot(item)
                undo.record(s, "queue", f"Conditional metadata edit #{item.id}", rows=[item], fields=sorted(actual))
                changed = s.execute(update(QueueItem).where(*predicates).values(**actual),
                                    execution_options={"synchronize_session": False}).rowcount
                if changed != 1:
                    raise HTTPException(409, "Queue changed during edit; refresh before editing")
                s.refresh(item)
            after = view(s, item)
            response = {**after, "receipt_id": req.operation_id, "replayed": False,
                        "response_state": "saved_snapshot", "changed_fields": sorted(actual)}
            # Receipt + metadata + narrow undo commit atomically. No external I/O.
            s.add(MetadataMutationReceipt(operation_id=req.operation_id, queue_id=item_id,
                queue_identity=before["queue_identity"], request_hash=request_hash, data={
                    "schema_version": SCHEMA_VERSION, "scope": "pending_public_metadata_only",
                    "before": before, "after": after, "changed_fields": sorted(actual), "response": response,
                    **({"approval_context": approval, "approval_scope": "declared exact proposal context; original source/rights guards unchanged"} if approval else {})}))
            s.commit()
            return response
    except worker_leases.LeaseLost as exc:
        s.rollback()
        raise HTTPException(409, "Item operation is in progress; inspect the receipt before retrying unchanged") from exc
    except BaseException:
        s.rollback()
        raise
