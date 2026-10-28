"""Owner 2026-10-28: changing a Ready LongForm's thumbnail from the queue gave "409 Studio source: content is
bound to its reviewed export". Now the new image goes onto the breakdown, is re-approved with the one-tap
rules and resent, updating the same queue row under a new binding revision."""
import io

from PIL import Image

from backend.app import queue_sources
from backend.app.db import SessionLocal
from backend.app.models import AppSetting, QueueItem
from backend.app.studio.export_acceptance import RIGHTS_KEY
from backend.tests.test_queue_source_binding import reviewed_source  # noqa: F401  (fixture)


def png():
    buf = io.BytesIO(); Image.new("RGB", (64, 36), (200, 30, 30)).save(buf, "PNG"); return buf.getvalue()


def _rights(text):
    with SessionLocal() as s:
        row = s.get(AppSetting, RIGHTS_KEY)
        if text is None:
            if row: s.delete(row)
        elif row: row.value = {"text": text}
        else: s.add(AppSetting(key=RIGHTS_KEY, value={"text": text}))
        s.commit()


def test_uploaded_thumbnail_on_a_bound_longform_row_is_reapproved_and_resent(reviewed_source, monkeypatch):
    from backend.app.studio import duration_intent
    monkeypatch.setattr(duration_intent, "comparison", lambda project: None)   # 1 s fixture vs a 3 min target
    client, pid, original, qa = reviewed_source()
    r = client.post(f"/api/studio/projects/{pid}/publish"); assert r.status_code == 200, r.text
    qid = r.json()["queue_item_id"]
    with SessionLocal() as s:
        item = s.get(QueueItem, qid); before = item.thumb_path; rev = queue_sources.head(s, item).revision
    _rights(None)
    refused = client.post(f"/api/queue/{qid}/thumbnail", files={"file": ("new.png", png(), "image/png")})
    assert refused.status_code == 409 and "rights statement" in refused.text      # never approved silently
    _rights("Original commentary and review using short excerpts for criticism (fair use).")
    try:
        ok = client.post(f"/api/queue/{qid}/thumbnail", files={"file": ("new.png", png(), "image/png")})
        assert ok.status_code == 200, ok.text
        with SessionLocal() as s:
            item = s.get(QueueItem, qid)
            assert item.status == "ready" and item.thumb_path and item.thumb_path != before
            assert queue_sources.head(s, item).revision == rev + 1                 # new approved export bound
            queue_sources.verify(s, item)                                          # and it verifies for posting
    finally:
        _rights(None)
