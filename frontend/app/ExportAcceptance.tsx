"use client";

import { useState } from "react";
import { api, ExportAcceptanceView } from "../lib/api";

export function ExportAcceptance({ kind, id, label, disabled, onApproved }: {
  kind: "studio" | "countdown" | "native"; id: number | string; label: string; disabled: boolean; onApproved: () => void;
}) {
  const [view, setView] = useState<ExportAcceptanceView | null>(null);
  const [checks, setChecks] = useState<string[]>([]);
  const [decisions, setDecisions] = useState<Record<string, string>>({});
  const [rights, setRights] = useState("");
  const [ack, setAck] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const version = JSON.stringify([view, checks, decisions, rights]);
  const source = view?.source && typeof view.source === "object" ? view.source as Record<string, unknown> : null;
  const sourceLabel = source ? [source.title, source.url, source.source].filter(item => typeof item === "string").join(" · ") : "";
  const complete = !!view && Object.keys(view.checks).every(key => checks.includes(key)) &&
    view.findings.every(row => (decisions[row.id] || "").trim().length >= 20) && rights.trim().length >= 20;

  async function open() {
    setBusy(true); setMessage("");
    try {
      setView(await api.exportAcceptanceView(kind, id)); setChecks([]); setDecisions({}); setRights(""); setAck(null);
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Could not load export inspection"); }
    finally { setBusy(false); }
  }

  return <div>
    <button disabled={disabled || busy} onClick={() => void open()}>{label}</button>
    {message && <p role="status">{message}</p>}
    {view && <form aria-label="Exact export acceptance" style={{ display: "grid", gap: 10, marginTop: 12 }} onSubmit={async event => {
      event.preventDefault();
      if (!complete || ack !== version) return;
      setBusy(true); setMessage("");
      try {
        await api.approveReviewedExport(kind, id, { expected_fingerprint: view.fingerprint, checks,
          warning_decisions: decisions, rights_rationale: rights, reviewed: true });
        setView(null); setMessage("Editorial acceptance recorded for the exact inspected export."); onApproved();
      } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Export acceptance failed"); setAck(null); }
      finally { setBusy(false); }
    }}>
      <p>Inspect the complete delivered export above, including every image, post/poll option and all audio where present. Measurements cannot establish caption accuracy, relevance or permission. Changing the video, thumbnail, script, sources, plan or inspection requires a fresh review.</p>
      <p>Source context: {sourceLabel || "No source description was supplied; document all assets and permissions below."}</p>
      {view.requested_format && <p>Requested Countdown format: <strong>{view.requested_format.replace(/^top/, "Top ")}</strong>. Review the exact entry count, title and narration against this format.</p>}
      <details><summary>Inspection reference</summary><code style={{ overflowWrap: "anywhere" }}>{view.fingerprint}</code></details>
      {Object.entries(view.checks).map(([key, text]) => <label key={key}><input type="checkbox" disabled={busy} checked={checks.includes(key)} onChange={e => setChecks(e.target.checked ? [...checks, key] : checks.filter(item => item !== key))} /> {text}</label>)}
      {view.findings.map((row, index) => <label key={row.id}>{row.message}
        <textarea aria-label={`Editorial decision ${index + 1}`} required minLength={20} maxLength={4000} disabled={busy}
          value={decisions[row.id] || ""} onChange={e => setDecisions({ ...decisions, [row.id]: e.target.value })}
          placeholder="Explain why this measured warning or repeated visual is intentional and acceptable. Otherwise revise the export." />
      </label>)}
      <label>Rights and intended-use review <textarea required minLength={20} maxLength={4000} disabled={busy} value={rights} onChange={e => setRights(e.target.value)} /></label>
      <p>Rights are recorded as your attestation. The app has not verified licenses or permissions.</p>
      <label><input type="checkbox" disabled={busy || !complete} checked={ack === version} onChange={e => setAck(e.target.checked ? version : null)} /> I accept these exact files, checks and editorial decisions.</label>
      <button disabled={busy || !complete || ack !== version}>Record exact export acceptance</button>
      <button type="button" disabled={busy} onClick={() => setView(null)}>Cancel export review</button>
    </form>}
  </div>;
}
