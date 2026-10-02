"""Undo: snapshot rows before a change, restore them on undo.

Same idea as manhwa's storyboard undo: restore the state as it stood before
the change rather than applying an "opposite" operation (inverse ops are many
separate chances to drift). The snapshot is added to the SAME session as the
change, so it commits with it or not at all.

Scopes: "queue" (Posting Queue), "studio:<id>" (one Studio video),
"settings" (schedule, auto-post switch).

Not undoable (files are gone or outside our control): deleted scraped clips,
renders already overwritten, videos already posted, files uploaded to Drive.
"""

from __future__ import annotations

import datetime
from typing import Any, Iterable

from sqlalchemy import desc
from sqlalchemy.orm import Session

from .models import AppSetting, QueueItem, StudioProject, UndoEntry

MODELS = {"queue_items": QueueItem, "studio_projects": StudioProject, "app_settings": AppSetting}
KEEP = 50  # undo steps kept per scope


class NothingToUndo(Exception):
    pass


def _enc(v: Any) -> Any:
    return {"__dt__": v.isoformat()} if isinstance(v, datetime.datetime) else v


def _dec(v: Any) -> Any:
    return datetime.datetime.fromisoformat(v["__dt__"]) if isinstance(v, dict) and "__dt__" in v else v


def _row(obj) -> dict[str, Any]:
    return {c.name: _enc(getattr(obj, c.key)) for c in obj.__table__.columns}


def _pk(model) -> str:
    return list(model.__table__.primary_key.columns)[0].name


def record(s: Session, scope: str, label: str, *, rows: Iterable = (), created: Iterable = (),
           model: str = "queue_items", fields: list[str] | None = None) -> None:
    """Remember rows (before the change) and/or ids created by the change.
    Call BEFORE mutating; the caller's commit saves snapshot + change together."""
    rows, created = list(rows), list(created)
    if not rows and not created:
        return
    s.add(UndoEntry(scope=scope, label=label[:255], payload={
        "model": model, "fields": fields, "rows": [_row(r) for r in rows], "created": created}))
    s.flush()  # the new entry must count before trimming the oldest
    old = (s.query(UndoEntry.id).filter(UndoEntry.scope == scope)
           .order_by(desc(UndoEntry.id)).offset(KEEP).all())
    if old:
        s.query(UndoEntry).filter(UndoEntry.id.in_([i for (i,) in old])).delete(synchronize_session=False)


def add_created(s: Session, scope: str, label: str, ids: Iterable, model: str = "queue_items") -> None:
    record(s, scope, label, created=ids, model=model)


def stack(s: Session, scope: str, n: int = 10) -> list[dict[str, Any]]:
    rows = s.query(UndoEntry).filter(UndoEntry.scope == scope).order_by(desc(UndoEntry.id)).limit(n).all()
    return [{"id": r.id, "label": r.label, "created_at": r.created_at} for r in rows]


def undo(s: Session, scope: str) -> str:
    entry = s.query(UndoEntry).filter(UndoEntry.scope == scope).order_by(desc(UndoEntry.id)).first()
    if not entry:
        raise NothingToUndo(f"Nothing to undo here")
    p = entry.payload or {}
    model = MODELS[p.get("model", "queue_items")]
    pk = _pk(model)
    fields = p.get("fields")
    # 1. Rows the change created -> remove them.
    for cid in p.get("created", []):
        obj = s.get(model, cid)
        if obj is not None:
            s.delete(obj)
    # 2. Rows as they were -> put them back (update, or re-insert if deleted).
    for data in p.get("rows", []):
        vals = {k: _dec(v) for k, v in data.items()}
        obj = s.get(model, vals[pk])
        if obj is None:   # it was deleted -> re-insert with its original id
            s.add(model(**vals))
            continue
        for k, v in vals.items():
            if k == pk or (fields and k not in fields):
                continue
            setattr(obj, k, v)
    label = entry.label
    s.delete(entry)
    s.commit()
    return label
