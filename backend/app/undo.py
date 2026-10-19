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

from .models import AppSetting, CountdownProject, QueueItem, StudioProject, UndoEntry

MODELS = {"queue_items": QueueItem, "studio_projects": StudioProject, "app_settings": AppSetting,
          "countdown_projects": CountdownProject}
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
    from .config import settings
    from .file_use import pin
    paths=[settings.data_path/'studio'/scope.split(':',1)[1]] if scope.startswith('studio:') else []
    with pin(paths,exclusive=True,session=s):return _undo_owned(s,scope)


def _undo_owned(s: Session, scope: str) -> str:
    from .budget_reservations import lock
    lock(s)
    entry = s.query(UndoEntry).filter(UndoEntry.scope == scope).order_by(desc(UndoEntry.id)).first()
    if not entry:
        raise NothingToUndo(f"Nothing to undo here")
    p = entry.payload or {}
    model = MODELS[p.get("model", "queue_items")]
    expected=StudioProject if scope.startswith('studio:') else CountdownProject if scope.startswith('countdown:') else QueueItem if scope=='queue' else AppSetting
    if model is not expected:
        from fastapi import HTTPException
        raise HTTPException(409,'Legacy undo snapshot has an incompatible model; current project was preserved')
    pk = _pk(model)
    fields = p.get("fields")
    from fastapi import HTTPException
    from . import publication_journal,worker_leases
    # Undo may reverse an edit, but cannot erase a provider submission or reuse
    # an old row snapshot after its numeric identity now belongs to another row.
    candidates=[(cid,None) for cid in p.get('created',[])]+[(data.get(pk),data) for data in p.get('rows',[])]
    for cid,data in candidates:
        current=s.get(model,cid,populate_existing=True)
        if current and model in (StudioProject,CountdownProject):
            key=('studio:' if model is StudioProject else 'countdown:')+str(cid)
            if current.stage_status in ('running','queued') or worker_leases.live(key):
                raise HTTPException(409,'A generation job owns this project; wait before undo')
        if model is QueueItem:
            if current and (current.status=='posting' or worker_leases.live(f'publication:{cid}') or current.publish_requests
                    or publication_journal.unresolved_late_receipt(s,current)):
                raise HTTPException(409,'Undo cannot change submitted publication evidence; reconcile the item first')
            if data and data.get('publish_requests'):
                raise HTTPException(409,'Undo cannot restore historical submission state')
        if current and data and data.get('created_at') and hasattr(current,'created_at'):
            previous=_dec(data['created_at']);now=current.created_at
            def aware(value):return value.replace(tzinfo=datetime.timezone.utc) if not value.tzinfo else value.astimezone(datetime.timezone.utc)
            if aware(previous)!=aware(now):raise HTTPException(409,'Undo snapshot belongs to a different row identity')
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
        upstream_changed=model is StudioProject and any(
            k not in (fields or vals) and _enc(getattr(obj,k))!=data.get(k)
            for k in ('facts','research','trailer','shots'))
        for k, v in vals.items():
            if k == pk or (fields and k not in fields):
                continue
            setattr(obj, k, v)
        if upstream_changed:
            from .studio.research_gate import invalidate
            obj.script=invalidate(obj.script,'Undo restored text after upstream evidence changed; check saved script again')
            obj.plan,obj.render=None,None
    label = entry.label
    s.delete(entry)
    s.commit()
    return label
