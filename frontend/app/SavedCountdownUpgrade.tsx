"use client";

import { useEffect, useRef, useState } from "react";
import { api, CountdownProject } from "../lib/api";

export function SavedCountdownUpgrade({ project, onChanged }: { project: CountdownProject; onChanged: (value: CountdownProject) => void }) {
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  if (project.saved_contract !== "legacy" && project.saved_contract !== "unsupported") return null;
  const active = ["running", "waiting", "queued"].includes(project.stage_status);
  return <form aria-label="Saved Countdown upgrade" onSubmit={async event => {
    event.preventDefault();
    if (!reviewed || !project.contract_upgrade_version || busy) return;
    setBusy(true); setError("");
    try {
      const value = await api.countdownUpgradeSaved(project.id, project.contract_upgrade_version);
      if (mounted.current) onChanged(value);
    } catch (cause: unknown) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : "Upgrade could not be confirmed; reload the saved Countdown before retrying.");
    } finally { if (mounted.current) setBusy(false); }
  }} style={{ border: "1px solid #a16207", borderRadius: 10, padding: 16 }}>
    <h2 style={{ fontSize: 16, marginTop: 0 }}>Saved Countdown needs fresh review</h2>
    <p>Your saved words, ranking and historical exports remain available. Older checks cannot approve new narration or rendering.</p>
    {project.saved_contract === "unsupported" ? <p role="alert">This saved contract or format is unsupported. Its words and history stay readable. Review them in a compatible version or create a separate Top 5, Top 10 or Top 15 draft. This record stays unchanged.</p> : <>
      <p>Create a review draft with the same words and ranking. Then repair the entry count, recheck eligibility, make separate campaign segments and review the intro/outro and new export. This action makes no provider calls or posts.</p>
      <label><input type="checkbox" checked={reviewed} disabled={busy || active} onChange={event => setReviewed(event.target.checked)} /> I reviewed the saved words and ranking and understand that the titles, segments and export need new checks.</label>
      <div style={{ marginTop: 12 }}><button disabled={!reviewed || busy || active || !project.contract_upgrade_version}>Create current review draft</button></div>
      {active && <p>Stop or finish active work before upgrading this saved Countdown.</p>}
    </>}
    {error && <p role="alert">{error}</p>}
  </form>;
}
