"use client";

import { useState } from "react";
import { api, ExportAcceptanceView } from "../lib/api";

function lengthLabel(seconds: number | null) {
  return seconds === null ? "unconfirmed" : seconds < 60 ? `${seconds.toFixed(1)} seconds` : `${(seconds / 60).toFixed(1)} minutes`;
}

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

  // One-tap Approve (owner, 2026-10-19): you watched it, tap once. Serious problems open the full form.
  const [quickRights, setQuickRights] = useState<string | null>(null);
  async function quick(rights?: string) {
    if (kind === "native") return;
    setBusy(true); setMessage("");
    try {
      const v = await api.exportAcceptanceView(kind, id);
      const q = v.quick || { severe: [], saved_rights: "" };
      if (q.severe.length) {
        setView(v); setChecks([]); setDecisions({}); setRights(q.saved_rights); setAck(null);
        setMessage("Needs your decision first: " + q.severe.join("; ") + ". Fill in the detailed review below.");
        return;
      }
      if (!q.saved_rights && !(rights && rights.trim().length >= 20)) { setQuickRights(""); return; }
      await api.quickApproveExport(kind, Number(id), { expected_fingerprint: v.fingerprint, ...(rights ? { rights_rationale: rights } : {}) });
      setQuickRights(null); setMessage("✓ Approved."); onApproved();
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Approve failed"); }
    finally { setBusy(false); }
  }

  return <div>
    {kind !== "native" && <button className="primary" disabled={disabled || busy} onClick={() => void quick()}
      title="You've watched it: approve for the queue in one tap (serious problems still ask for a decision)">✓ Approve</button>}{" "}
    <button disabled={disabled || busy} onClick={() => void open()}>{kind === "native" ? label : "Detailed review"}</button>
    {quickRights !== null && <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
      <label>Your standard rights statement — written once, saved for every future Approve
        <textarea value={quickRights} onChange={e => setQuickRights(e.target.value)} minLength={20} maxLength={4000}
          placeholder="e.g. Commentary and news coverage using official trailers, posters and promotional stills; my own narration and edit." />
      </label>
      <button className="primary" disabled={busy || quickRights.trim().length < 20} onClick={() => void quick(quickRights)}>Save and approve</button>
    </div>}
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
      {view.requested_duration && <p>Requested length: approximately <strong>{lengthLabel(view.requested_duration.target_seconds)}</strong> ({view.requested_duration.basis}). Delivered length: <strong>{lengthLabel(view.requested_duration.actual_seconds)}</strong>. The {view.requested_duration.review_tolerance_percent}% band is a review aid; an intentional shorter or longer treatment needs your decision.</p>}
      <details><summary>Inspection reference</summary><code style={{ overflowWrap: "anywhere" }}>{view.fingerprint}</code></details>
      {Object.entries(view.checks).map(([key, text]) => <label key={key}><input type="checkbox" disabled={busy} checked={checks.includes(key)} onChange={e => { setChecks(e.target.checked ? [...checks, key] : checks.filter(item => item !== key)); setAck(null); }} /> {text}</label>)}
      {view.findings.map((row, index) => <label key={row.id}>{row.message}
        <textarea aria-label={`Editorial decision ${index + 1}`} required minLength={20} maxLength={4000} disabled={busy}
          value={decisions[row.id] || ""} onChange={e => { setDecisions({ ...decisions, [row.id]: e.target.value }); setAck(null); }}
          placeholder="Explain why this measured warning or repeated visual is intentional and acceptable. Otherwise revise the export." />
      </label>)}
      <label>Rights and intended-use review <textarea required minLength={20} maxLength={4000} disabled={busy} value={rights} onChange={e => { setRights(e.target.value); setAck(null); }} /></label>
      <p>Rights are recorded as your attestation. The app has not verified licenses or permissions.</p>
      <label><input type="checkbox" disabled={busy || !complete} checked={ack === version} onChange={e => setAck(e.target.checked ? version : null)} /> I accept these exact files, checks and editorial decisions.</label>
      <button disabled={busy || !complete || ack !== version}>Record exact export acceptance</button>
      <button type="button" disabled={busy} onClick={() => setView(null)}>Cancel export review</button>
    </form>}
  </div>;
}
