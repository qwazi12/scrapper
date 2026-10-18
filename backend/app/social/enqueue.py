"""Atomic one-queue-row claim for a Studio/Countdown project."""
from fastapi import HTTPException
from sqlalchemy import update
from ..models import QueueItem


def ensure_editable(item):
    if item.status not in ("review", "ready", "retry", "error") or any(
            e.get("state") in ("intent", "unknown", "accepted") for e in item.publish_requests or []):
        raise HTTPException(409, "This export already has a submitted/archived queue item; reconcile or review it first")


def claim(s, project, candidate):
    """The losing request discards its tentative row within the same transaction."""
    model = type(project)
    s.add(candidate)
    s.flush()
    changed = s.execute(update(model).where(model.id == project.id, model.queue_item_id.is_(None))
                        .values(queue_item_id=candidate.id),
                        execution_options={"synchronize_session": False}).rowcount
    if changed:
        s.refresh(project)
        return candidate, True
    s.delete(candidate)
    s.flush()
    s.refresh(project)
    existing = s.get(QueueItem, project.queue_item_id) if project.queue_item_id else None
    if not existing:
        raise HTTPException(409, "Queue link needs owner review; the previous queue record is missing")
    ensure_editable(existing)
    return existing, False


def rewrite_version(s,item):
    from ..budget_reservations import lock
    from .. import worker_leases, publication_journal
    lock(s);s.refresh(item);ensure_editable(item)
    if worker_leases.live(f'publication:{item.id}') or publication_journal.unresolved_late_receipt(s,item):
        raise HTTPException(409,'Submission unresolved; metadata cannot be rewritten')
    if (item.research or {}).get('native_export_id'):
        raise HTTPException(409,'Native copy is bound to its export approval')
    return publication_journal.digest([item.title,item.description,item.tags,item.research,item.accounts,
        item.status,item.video_path,item.thumb_path,item.clip_id,item.compilation_id])
