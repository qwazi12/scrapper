"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, FinancialHold, FinancialHoldView } from "../lib/api";

export function FinancialHolds({ bounds, onChanged }: { bounds: Record<string, number>; onChanged: () => void }) {
  const [view, setView] = useState<FinancialHoldView | null>(null);
  const [selected, setSelected] = useState("");
  const [actual, setActual] = useState("");
  const [receipt, setReceipt] = useState("");
  const [rationale, setRationale] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [interrupted, setInterrupted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [contract, setContract] = useState("");
  const [ceiling, setCeiling] = useState("");
  const hold = view?.holds.find(h => h.id === selected);

  const load = async () => { setView(await api.financialHolds()); };
  useEffect(() => {
    let stopped = false;
    api.financialHolds().then(v => { if (!stopped) setView(v); }).catch(e => { if (!stopped) setMessage(e.message); });
    return () => { stopped = true; };
  }, []);

  function select(id: string) {
    setSelected(id); setActual(""); setReceipt(""); setRationale(""); setReviewed(false); setInterrupted(false); setMessage("");
  }

  async function resolve(event: FormEvent) {
    event.preventDefault();
    if (!hold || !reviewed) return;
    setBusy(true); setMessage("");
    try {
      await api.resolveFinancialHold({ request_key: crypto.randomUUID(), reservation_id: hold.id, kind: hold.kind,
        expected_version: hold.version, actual: Number(actual), receipt, rationale, reviewed: true, confirm_interrupted: interrupted });
      await load(); select(""); setMessage("Provider evidence recorded; the local ledger has been reconciled."); onChanged();
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Reconciliation failed"); }
    finally { setBusy(false); }
  }

  async function changeBounds(next: Record<string, number>) {
    setBusy(true); setMessage("");
    try { await api.setCostSettings({ request_bounds: next }); onChanged(); setMessage("Request ceilings saved locally."); }
    catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Could not save request ceilings"); }
    finally { setBusy(false); }
  }

  const description = (h: FinancialHold) => `${h.service} · ${h.state} · ${h.kind === "usd" ? `$${h.reserved.toFixed(6)}` : `${h.reserved} credits`} reserved`;
  return <details style={{ margin: "14px 0", border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
    <summary>Unsettled requests &amp; cost contracts{view ? ` (${view.holds.length} holds)` : ""}</summary>
    <p>Unknown charges remain reserved across day and month resets. A worker timeout does not prove a request was free. Reconcile only against provider evidence for this exact request.</p>
    <button disabled={busy} onClick={() => load().catch(e => setMessage(e.message))}>Refresh financial holds</button>
    {message && <p role="status">{message}</p>}
    {view && <>
      {!view.holds.length && <p>No unsettled local requests.</p>}
      <label>Unsettled request <select aria-label="Unsettled request" value={selected} disabled={busy} onChange={e => select(e.target.value)}>
        <option value="">Choose a hold to inspect</option>
        {view.holds.map(h => <option key={h.id} value={h.id}>{description(h)}</option>)}
      </select></label>
      {hold && <form onSubmit={resolve} style={{ display: "grid", gap: 10, marginTop: 12 }}>
        <p>{description(hold)}. Reference: {hold.ref || hold.id}. {hold.worker_live ? "Request worker still holds a live lease." : hold.orphan ? "Interrupted request — explicit provider review required." : "Awaiting receipt reconciliation."}</p>
        <label>Confirmed actual amount ({hold.kind === "usd" ? "USD" : "credits"}) <input type="number" required min={0} max={1000000} step={hold.kind === "usd" ? "0.000001" : "1"} value={actual} onChange={e => setActual(e.target.value)} /></label>
        <label>Provider receipt reference <input required minLength={5} maxLength={2048} value={receipt} onChange={e => setReceipt(e.target.value)} /></label>
        <label>Financial reconciliation rationale <textarea required minLength={20} maxLength={4000} value={rationale} onChange={e => setRationale(e.target.value)} /></label>
        <label><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} /> I reviewed this exact request against provider billing evidence.</label>
        {hold.orphan && <label><input type="checkbox" checked={interrupted} onChange={e => setInterrupted(e.target.checked)} /> I confirm the interrupted request worker and its provider outcome were reviewed.</label>}
        <button disabled={busy || !hold.can_resolve || !reviewed || (hold.orphan && !interrupted)}>Record financial reconciliation</button>
      </form>}
      <details style={{ marginTop: 12 }}><summary>Receipt history (latest {view.history.length})</summary>
        {view.history.map(r => <p key={r.id}>{r.kind} · {r.reservation_id} · {r.amount} {r.kind === "usd" ? "micro-USD" : "credits"} · {r.receipt} · {r.rationale}</p>)}
      </details>
    </>}
    <details style={{ marginTop: 16 }}><summary>Per-request USD ceilings</summary>
      <p>Set a verified maximum cost per provider request. Admission reserves the whole ceiling before dispatch. Unknown models, image/audio requests and search grounding require an explicit ceiling. These values do not verify a provider invoice.</p>
      {Object.entries(bounds).map(([name, value]) => <p key={name}>{name}: ${value.toFixed(6)} <button disabled={busy} onClick={() => { const next = { ...bounds }; delete next[name]; void changeBounds(next); }}>Remove ceiling for {name}</button></p>)}
      <form onSubmit={event => {
        event.preventDefault(); const value = Number(ceiling);
        if (!Number.isFinite(value) || value <= 0) { setMessage("Enter a finite positive ceiling."); return; }
        void changeBounds({ ...bounds, [contract.trim()]: value });
      }} style={{ display: "grid", gap: 8 }}>
        <label>Provider service and model <input required maxLength={160} value={contract} placeholder="gemini:your-model" onChange={e => setContract(e.target.value)} /></label>
        <label>Maximum USD per request <input type="number" required min="0.000001" max="1000000" step="0.000001" value={ceiling} onChange={e => setCeiling(e.target.value)} /></label>
        <button disabled={busy}>Save request ceiling</button>
      </form>
    </details>
  </details>;
}
