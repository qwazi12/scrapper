"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, PublicationEvidenceView, QueueItem } from "../lib/api";

export function PublicationEvidence({ items, onChanged }: { items: QueueItem[]; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const [qid, setQid] = useState("");
  const [view, setView] = useState<PublicationEvidenceView | null>(null);
  const [attempt, setAttempt] = useState("");
  const [outcome, setOutcome] = useState<"accepted" | "not_accepted" | "unresolved">("unresolved");
  const [receipt, setReceipt] = useState("");
  const [url, setUrl] = useState("");
  const [rationale, setRationale] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [absent, setAbsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selected = view?.current.find(v => v.entry.client_request_id === attempt);
  const entry = selected?.entry;
  const account = entry?.profile && entry.platforms?.length === 1 ? `${entry.profile}:${entry.platforms[0]}` : "batch";

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);

  useEffect(() => {
    if (!open || !qid) { setView(null); return; }
    let cancelled = false;
    setView(null); setMessage(""); setAttempt(""); setReviewed(false); setAbsent(false);
    setOutcome("unresolved"); setReceipt(""); setUrl(""); setRationale("");
    api.publicationEvidence(Number(qid)).then(v => {
      if (cancelled) return;
      setView(v); setAttempt(v.current.find(a => a.resolvable)?.entry.client_request_id || "");
    }).catch(e => { if (!cancelled) setMessage(e.message); });
    return () => { cancelled = true; };
  }, [open, qid]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!selected || !reviewed) return;
    setBusy(true); setMessage("");
    try {
      await api.publicationDecision(Number(qid), {
        request_key: crypto.randomUUID(), attempt_id: attempt, expected_version: selected.version,
        account, outcome, provider_request_id: outcome === "accepted" ? receipt : null,
        evidence_url: url, rationale, reviewed_account: true, absence_confirmed: absent,
      });
      setView(await api.publicationEvidence(Number(qid)));
      setReviewed(false); setAbsent(false);
      setMessage("Evidence saved. Accepted receipts need reconciliation. Confirmed nonacceptance returns to review.");
      onChanged();
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Could not record evidence"); }
    finally { setBusy(false); }
  }

  async function exportLedger() {
    try {
      const data = await api.publicationLedger();
      const objectUrl = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a"); link.href = objectUrl; link.download = "scrapper-publication-ledger.json"; link.click();
      URL.revokeObjectURL(objectUrl);
      setMessage(`Exported ${data.length} most recent journal events (maximum 2000).`);
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Export failed"); }
  }

  return <>
    <button style={{ fontSize: 11 }} onClick={() => {
      if (!qid) setQid(String(items.find(i => i.status === "posting")?.id || items[0]?.id || ""));
      setOpen(true);
    }}>Publication evidence</button>
    <dialog ref={dialog} aria-labelledby="publication-evidence-title" onCancel={() => setOpen(false)} style={{ color: "var(--text)", background: "var(--panel, #111827)", border: "1px solid #475569", borderRadius: 12, width: "min(850px, 92vw)", maxHeight: "85vh", overflow: "auto", padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <h2 id="publication-evidence-title">Publication evidence</h2>
        <button onClick={() => setOpen(false)}>Close evidence</button>
      </div>
      <p>Review uncertain submissions against the exact provider account. Saving evidence does not submit a post. Elapsed time alone cannot prove nonacceptance.</p>
      <label>Queue item <select aria-label="Queue item" value={qid} onChange={e => setQid(e.target.value)} disabled={busy}>
        <option value="">Choose an item</option>
        {items.map(i => <option key={i.id} value={i.id}>#{i.id} · {i.title || i.video_name} · {i.status}</option>)}
      </select></label>
      <button onClick={exportLedger} style={{ marginLeft: 12 }}>Export journal</button>
      {message && <p role="status">{message}</p>}
      {view && <>
        <p>Current queue state: {view.status}. {view.decisions.length} recorded owner decisions.</p>
        <label>Uncertain attempt <select aria-label="Uncertain attempt" value={attempt} onChange={e => { setAttempt(e.target.value); setReviewed(false); setAbsent(false); setReceipt(""); }} disabled={busy}>
          <option value="">Choose an unresolved attempt</option>
          {view.current.filter(v => v.resolvable).map(v => <option key={v.entry.client_request_id} value={v.entry.client_request_id}>{v.entry.profile || "batch"} · {v.entry.platforms?.join(", ") || "before platform dispatch"} · {v.entry.state}</option>)}
        </select></label>
        {selected && <form onSubmit={save} style={{ display: "grid", gap: 12, marginTop: 14 }}>
          <p>Account to inspect: <strong>{account}</strong>. Attempt: {attempt}</p>
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(entry, null, 2)}</pre>
          <label>Provider review outcome <select aria-label="Provider review outcome" value={outcome} onChange={e => setOutcome(e.target.value as typeof outcome)}>
            <option value="unresolved">Still uncertain</option>
            <option value="accepted" disabled={account === "batch"}>Accepted — attach individual receipt</option>
            <option value="not_accepted">Provider confirmed nonacceptance</option>
          </select></label>
          {outcome === "accepted" && <label>Provider request ID <input required value={receipt} maxLength={160} onChange={e => setReceipt(e.target.value)} /></label>}
          <label>Provider evidence URL <input type="url" required value={url} maxLength={2048} placeholder="https://provider.example/receipt" onChange={e => setUrl(e.target.value)} /></label>
          <label>Review rationale <textarea required minLength={40} maxLength={4000} value={rationale} onChange={e => setRationale(e.target.value)} /></label>
          <label><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} /> I reviewed this exact account, time, media and submission evidence.</label>
          {outcome === "not_accepted" && <label><input type="checkbox" checked={absent} onChange={e => setAbsent(e.target.checked)} /> The provider confirms it did not accept this submission.</label>}
          <button disabled={busy || !reviewed || (outcome === "not_accepted" && !absent)}>Save submission evidence</button>
        </form>}
        <details style={{ marginTop: 20 }}><summary>Append-only history ({view.events.length} events)</summary>
          {view.events.map(e => <article key={e.id} style={{ borderTop: "1px solid #334155", paddingTop: 8 }}>
            <strong>{e.kind}</strong> · <time>{e.at}</time> · {e.attempt_id}
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(e.payload, null, 2)}</pre>
          </article>)}
        </details>
      </>}
    </dialog>
  </>;
}
