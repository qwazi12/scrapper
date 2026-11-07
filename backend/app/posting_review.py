"""Read-only: the owner's "Posting Review (next 7 days)" sheet, every tab, full cell text (owner, 2026-11-07).
The ops poller's approve action reads the Decision column and the proposed copy from here. One sheet only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_token

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
