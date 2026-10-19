"use client";

import { useEffect, useRef, useState } from "react";
import { api, AudienceFeedback, CommentCursorView, ContentBinding } from "../lib/api";
import { allowLocalNavigation } from "../lib/navigationGuard";
import { FeedbackCard } from "./FeedbackCard";

export function CommentInbox({ onFollowup, onEvidenceChanged }: { onFollowup: (row: AudienceFeedback) => void; onEvidenceChanged?: () => void }) {
  const [bindings, setBindings] = useState<ContentBinding[]>([]);
  const [lid, setLid] = useState("");
  const [parent, setParent] = useState("");
  const [cursor, setCursor] = useState<CommentCursorView | null>(null);
  const [configured, setConfigured] = useState(false);
  const [rows, setRows] = useState<AudienceFeedback[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const selection = useRef(""); selection.current = JSON.stringify([lid, parent, offset, appliedQuery]);
  const ticket = useRef(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [parentChoices, setParentChoices] = useState<AudienceFeedback[]>([]);
  const parents = [...new Map([...parentChoices, ...rows.filter(row => row.coverage)].map(row => [row.coverage!.parent_evidence_id, row])).values()];
  useEffect(() => { setParentChoices(previous => [...new Map([...previous, ...rows.filter(row => row.coverage)].map(row => [row.coverage!.parent_evidence_id, row])).values()]); }, [rows]);

  async function load() {
    if (!lid) return;
    const basis = JSON.stringify([lid, parent, offset, appliedQuery]);
    const [progress, feedback] = await Promise.all([api.commentProgress(lid, parent || undefined), api.feedbackList(lid, appliedQuery, offset)]);
    if (basis !== selection.current) return;
    setCursor(progress.cursor); setConfigured(progress.configured); setRows(feedback.feedback); setTotal(feedback.total);
  }
  useEffect(() => { let alive = true;
    api.contentLineages().then(view => { if (alive) setBindings(view.bindings.filter(row => row.platform === "youtube")); }).catch(e => { if (alive) setMessage(e.message); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { let alive = true; setCursor(null);
    if (lid) Promise.all([api.commentProgress(lid, parent || undefined), api.feedbackList(lid, appliedQuery, offset)]).then(([progress, feedback]) => {
      if (alive) { setCursor(progress.cursor); setConfigured(progress.configured); setRows(feedback.feedback); setTotal(feedback.total); }
    }).catch(e => { if (alive) setMessage(e.message); });
    return () => { alive = false; };
  }, [lid, parent, offset, appliedQuery]);

  async function action(kind: "page" | "restart" | "stop") {
    if (!lid || !cursor || !await allowLocalNavigation()) return;
    const sequence = ++ticket.current;
    if (kind !== "stop") setBusy(true);
    setMessage("");
    try {
      const current = kind === "stop" ? (await api.commentProgress(lid, parent || undefined)).cursor : cursor;
      const result = await api.commentAction(lid, kind, { expected_version: current.version, parent_id: parent || undefined, reviewed: true });
      if (sequence !== ticket.current) return;
      setCursor(result.cursor); await load();
      setMessage(kind === "stop" ? "Comment read stopped; prior entries and cursor were retained." : result.cursor.last_error || "Read-only page state saved. Entries and drafts remain private.");
    } catch (e: unknown) { if (sequence === ticket.current) { setMessage(e instanceof Error ? e.message : "Comment page operation failed"); await load().catch(() => {}); } }
    finally { if (sequence === ticket.current) setBusy(false); }
  }

  return <section style={{ display: "grid", gap: 12 }}>
    <h3>Published comment and reply inbox</h3>
    <p>Read one public page at a time for a reviewed YouTube content mapping. Top-level comments and replies have separate resumable cursors. No author fields are requested; source edits preserve private draft words and require a fresh review.</p>
    <label>Comment content mapping<select aria-label="Comment content mapping" value={lid} disabled={busy} onChange={async e => {
      const next = e.target.value; if (await allowLocalNavigation()) { setLid(next); setParent(""); setOffset(0); setRows([]); setParentChoices([]); }
    }}><option value="">Choose mapped content</option>{bindings.map(row => <option key={row.id} value={row.id}>{row.account_id} · {row.content_id} · revision {row.revision}</option>)}</select></label>
    {lid && <>
      <label>Comment page scope<select aria-label="Comment page scope" value={parent} disabled={busy} onChange={async e => { const next = e.target.value; if (await allowLocalNavigation()) setParent(next); }}>
        <option value="">Top-level public comments</option>{parents.map(row => <option key={row.coverage!.parent_evidence_id} value={row.coverage!.parent_evidence_id}>Replies to feedback #{row.id}</option>)}</select></label>
      <p>{cursor?.scope} · {cursor?.pages || 0} pages · {cursor?.seen_entries || 0} provider rows · {cursor?.new_versions || 0} new versions · {cursor?.duplicates || 0} duplicates · {cursor?.status || "loading"}</p>
      {!configured && <p>Read-only YouTube Data API access is not configured. Authorized text import remains available below.</p>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button disabled={busy || !configured || !cursor || cursor.pagination_exhausted} onClick={() => void action("page")}>Read next comment page</button>
        <button disabled={busy || !cursor} onClick={() => void action("restart")}>Restart reviewed comment scan</button>
        <button disabled={!cursor} onClick={() => void action("stop")}>Stop comment read</button>
      </div>
      <form onSubmit={async event => { event.preventDefault(); if (await allowLocalNavigation()) { if (!offset && query === appliedQuery) await load().catch(e => setMessage(e.message)); else { setOffset(0); setAppliedQuery(query); } } }}>
        <label>Search retained comments<input maxLength={200} value={query} onChange={e => setQuery(e.target.value)} /></label><button>Find retained feedback</button>
      </form>
      <p>{total} retained feedback entries; showing {offset + (rows.length ? 1 : 0)}–{offset + rows.length}.</p>
      <div><button disabled={!offset || busy} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setOffset(Math.max(0, offset - 50)); })}>Previous feedback page</button>{" "}<button disabled={offset + rows.length >= total || busy} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setOffset(offset + 50); })}>Next feedback page</button></div>
      {rows.map(row => <FeedbackCard key={row.feedback_identity} row={row} busy={busy} onChanged={load} onFollowup={onFollowup} onEvidenceChanged={onEvidenceChanged} />)}
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
