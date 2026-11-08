"""The owner's "Posting Review (next 7 days)" sheet, every tab, full cell text (owner, 2026-11-07).
The ops poller's approve action reads the Decision column and the proposed copy from here. One sheet only.
apply-copy (owner, POSTING GATE 2026-11-07): a row whose Decision is Approve gets the sheet's per-platform copy, read
by the server itself, so Studio-bound rows (whose title/description stay locked) post the approved words too."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .auth import require_token
from .db import get_session

SHEET_ID = "1cTo8P43DiqqxV7oAtavHFOVBzm3vmYjEzVjA8q4CQsU"
router = APIRouter(prefix="/api/ops", dependencies=[Depends(require_token)])


def rows_by_tab(values_by_tab: dict[str, list[list[str]]]) -> dict[str, list[dict[str, str]]]:
    """{tab: [{header: cell}]} using each tab's first row that has a "Queue ID" header; tabs without one are skipped."""
    out = {}
    for tab, values in values_by_tab.items():
        head = next((i for i, r in enumerate(values) if "Queue ID" in r), None)
        if head is None:
            continue
        names = values[head]
        out[tab] = [{n: (r[i] if i < len(r) else "") for i, n in enumerate(names) if n}
                    for r in values[head + 1:] if any(c.strip() for c in r)]
    return out


def read() -> dict[str, list[dict[str, str]]]:
    from googleapiclient.discovery import build
    from .drive_sync import get_drive_service
    sheets = build("sheets", "v4", http=get_drive_service()._http, cache_discovery=False)   # same Drive grant
    meta = sheets.spreadsheets().get(spreadsheetId=SHEET_ID, fields="sheets.properties.title").execute()
    tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
    got = sheets.spreadsheets().values().batchGet(spreadsheetId=SHEET_ID, ranges=[f"'{t}'" for t in tabs]).execute()
    return rows_by_tab({t: v.get("values", []) for t, v in zip(tabs, got.get("valueRanges", []))})


@router.get("/posting-review")
def posting_review() -> dict:
    try:
        return {"sheet_id": SHEET_ID, "tabs": read()}
    except Exception as exc:  # noqa: BLE001 — read-only; say why
        raise HTTPException(502, f"Posting Review sheet unreadable: {str(exc)[:200]}")


HASHTAG = re.compile(r"#(\w+)", re.UNICODE)


def no_hashtags(text: str) -> str:
    """Sheet words without hashtags (trailing hashtag lines/runs dropped, inline '#Word' -> 'Word'); each
    platform's hashtags are added at send."""
    lines = (text or "").strip().split("\n")
    while lines and (not lines[-1].strip() or not HASHTAG.sub("", lines[-1]).strip()):
        lines.pop()
    if lines:
        lines[-1] = re.sub(r"(\s+#\w+)+\s*$", "", lines[-1])
    return HASHTAG.sub(r"\1", "\n".join(lines)).strip()


def sheet_copy(r: dict[str, str]) -> dict:
    """{platform: {field: words}} from one sheet row; Instagram "Same as Facebook…" = the Facebook caption."""
    fb = no_hashtags(r.get("Proposed Facebook caption", ""))
    ig = r.get("Proposed Instagram caption", "").strip()
    own = {"youtube": {"title": r.get("Proposed YouTube title", "").strip(),
                       "description": no_hashtags(r.get("Proposed YouTube description", ""))},
           "facebook": {"caption": fb},
           "instagram": {"caption": fb if ig.lower().startswith("same as facebook") else no_hashtags(ig)},
           "x": {"text": no_hashtags(r.get("Proposed X post", ""))}}
    return {n: {k: v for k, v in f.items() if v} for n, f in own.items() if any(f.values())}


@router.post("/posting-review/{item_id}/apply-copy")
def apply_copy(item_id: int, s: Session = Depends(get_session)) -> dict:
    from . import logbus, undo
    from .models import QueueItem
    from .social import platform_copy
    item = s.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "queue item not found")
    if item.status not in ("ready", "review"):
        raise HTTPException(409, f"#{item_id} is {item.status}, not ready/review")
    if any(e.get("state") in ("intent", "unknown", "accepted") for e in item.publish_requests or []):
        raise HTTPException(409, "Submission unresolved — reconcile with the provider before editing")
    try:
        tabs = read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Posting Review sheet unreadable: {str(exc)[:200]}")
    hits = [r for rows in tabs.values() for r in rows if r.get("Queue ID", "").strip() == str(item_id)]
    if len(hits) != 1:
        raise HTTPException(409, f"#{item_id} appears {len(hits)} times in the sheet")
    if hits[0].get("Decision", "").strip().lower() != "approve":
        raise HTTPException(409, f"#{item_id} Decision is {hits[0].get('Decision', '')!r}, not Approve")
    try:
        new = platform_copy.clean(sheet_copy(hits[0]))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    before = item.platform_copy
    if new != before:
        undo.record(s, "queue", f"Sheet copy #{item.id}", rows=[item])
        item.platform_copy = new
        s.commit()
        s.refresh(item)
        logbus.log("info", "queue_sheet_copy", f"Item #{item.id}: Posting Review copy applied ({', '.join(sorted(new or {}))})")
    return {"id": item.id, "changed": new != before, "platform_copy": item.platform_copy}
