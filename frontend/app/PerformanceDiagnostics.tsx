"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ExperimentSeed, LearningDecision, PerformanceDiagnostic } from "../lib/api";
import { allowLocalNavigation } from "../lib/navigationGuard";
import { useLocalDraft } from "../lib/localDraft";
import { LocalDraftStatus } from "./LocalDraftStatus";

const box = { border: "1px solid var(--border)", borderRadius: 10, padding: 14 };
const value = (n: number | null) => n === null ? "Unavailable" : n > 0 && n < .001 ? "<0.001" : Number(n.toFixed(3)).toLocaleString();
const dollars = (n: number) => n > 0 && n < .0001 ? "<$0.0001" : `$${n.toFixed(4)}`;

function DiagnosticCard({ row, onChanged, onExperiment }: { row: PerformanceDiagnostic; onChanged: () => Promise<void>; onExperiment: (seed: ExperimentSeed) => void }) {
  const draft = useLocalDraft(`screen-central:learning-decision:${row.key}`, { rationale: "" });
  const [action, setAction] = useState<"plan_experiment" | "defer" | "reject">("plan_experiment");
  const [ack, setAck] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<LearningDecision[] | null>(null);
  const version = JSON.stringify([row.version, action, draft.value.rationale]);
  return <article style={box} data-testid={`performance-diagnostic-${row.content_id}`}>
    <h3>{row.source_title || row.content_id}</h3><p>{row.platform} · {row.account_id === "unspecified_owner_import" ? "Owner import (account unspecified)" : row.account_id} · {row.format} · {row.age_hours}h cohort · {row.content_id}</p>
    <p>Measured through {new Date(row.observed_at).toLocaleString()}. Peers exclude this content item. Missing measures remain unavailable.</p>
    <div style={{ overflowX: "auto" }}><table><thead><tr>{["Measure", "This item", "Other items", "Peer median", "Review signal"].map(title => <th key={title}>{title}</th>)}</tr></thead><tbody>
      {row.measures.map(metric => <tr key={metric.metric}><td>{metric.metric.replaceAll("_", " ")}</td><td>{value(metric.value)}</td><td>{metric.peer_n}</td><td>{value(metric.peer_median)}</td><td>{metric.status.replaceAll("_", " ")}</td></tr>)}
    </tbody></table></div>
    <p>Recorded cost: {row.cost.amount_usd === null ? "Unavailable" : dollars(row.cost.amount_usd)} · {row.cost.status.replaceAll("_", " ")}
      {row.cost.per_1000 !== null && ` · ${dollars(row.cost.per_1000)} per 1,000 recorded ${row.cost.denominator?.replaceAll("_", " ")}`}</p>
    <p>{row.cost.scope}. {row.cost.as_of && `Snapshot recorded ${new Date(row.cost.as_of).toLocaleString()}. `}{row.cost.limitation}.</p>
    {row.suggestions.map(suggestion => <p key={suggestion.kind}><strong>{suggestion.kind.replaceAll("_", " ")}:</strong> {suggestion.text}</p>)}
    <details><summary>Evidence limits and review heuristics</summary>{[...row.unknowns, ...row.limitations].map(note => <p key={note}>{note}</p>)}</details>
    {row.last_decision && <p>Last private decision: {row.last_decision.action.replaceAll("_", " ")}. {row.last_decision.matches_current_evidence ? "Matches current evidence." : "Evidence changed since this decision."}</p>}
    <form onSubmit={async event => {
      event.preventDefault(); if (ack !== version) return; setBusy(true); setMessage("");
      try { await api.learningReview(row.key, { expected_version: row.version, action, rationale: draft.value.rationale, reviewed: true });
        draft.saved({ rationale: "" }); setAck(null); await onChanged(); setMessage("Private planning decision recorded. No action executed.");
      } catch (error: unknown) { setAck(null); setMessage(error instanceof Error ? error.message : "Decision failed"); }
      finally { setBusy(false); }
    }}>
      <label>Learning decision<select aria-label="Learning decision" value={action} onChange={event => setAction(event.target.value as typeof action)}>
        <option value="plan_experiment">Plan an experiment</option><option value="defer">Defer</option><option value="reject">Reject</option></select></label>
      <label>Decision rationale<textarea aria-label="Decision rationale" required minLength={20} maxLength={4000} value={draft.value.rationale} onChange={event => draft.change({ rationale: event.target.value })} /></label>
      <LocalDraftStatus draft={draft} noun="decision rationale" destination="private decision" />
      <label><input type="checkbox" checked={ack === version} disabled={draft.conflict || !!draft.pending} onChange={event => setAck(event.target.checked ? version : null)} /> I reviewed these exact measures, cost limits and confounders.</label>
      <div><button disabled={busy || draft.value.rationale.trim().length < 20 || ack !== version || draft.conflict || !!draft.pending}>Record private learning decision</button></div>
    </form>
    {row.last_decision?.matches_current_evidence && row.last_decision.action === "plan_experiment" && <button disabled={busy || (draft.dirty && draft.storageError)} onClick={() => onExperiment({
      decision_id: row.last_decision!.id, name: `Review ${row.content_id} creative treatment`,
      hypothesis: row.suggestions.find(suggestion => suggestion.kind === "packaging")?.text || row.suggestions[0].text,
      metric: row.suggestions.find(suggestion => suggestion.kind === "packaging")?.support_metric || row.suggestions[0].support_metric || "engaged_views",
      platform: row.platform, account: row.account_id === "unspecified_owner_import" ? "" : row.account_id, format: row.format, age: String(row.age_hours),
    })}>Draft experiment from reviewed evidence</button>}
    <button disabled={busy} onClick={async () => { setBusy(true); try { setHistory((await api.learningHistory(row.key)).decisions); }
      catch (error: unknown) { setMessage(error instanceof Error ? error.message : "History failed"); } finally { setBusy(false); } }}>Inspect private learning decisions</button>
    {history && <details open><summary>Most recent {history.length} decisions</summary>{history.map(decision => <p key={decision.id}>{decision.action.replaceAll("_", " ")} · {new Date(decision.created_at).toLocaleString()} · {decision.data.rationale}. No action executed.</p>)}</details>}
    {message && <p role="status">{message}</p>}
  </article>;
}

export function PerformanceDiagnostics({ onExperiment, revision }: { revision: string; onExperiment: (seed: ExperimentSeed) => void }) {
  const [rows, setRows] = useState<PerformanceDiagnostic[]>([]);
  const [q, setQ] = useState(""); const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0); const [total, setTotal] = useState(0); const [message, setMessage] = useState("");
  const selection = useRef(""); selection.current = JSON.stringify([q, offset]);
  const load = useCallback(async () => {
    const basis = JSON.stringify([q, offset]); const report = await api.learningDiagnostics(q, offset);
    if (selection.current === basis) { setRows(report.items); setTotal(report.total); }
  }, [q, offset]);
  useEffect(() => { load().catch(error => setMessage(error.message)); }, [load, revision]);
  return <section style={{ display: "grid", gap: 12 }}>
    <h2>Performance review & next experiments</h2>
    <p>These comparisons describe retained observations. Decisions stay private and grant no spending, publishing or automation changes.</p>
    <form onSubmit={async event => { event.preventDefault(); if (await allowLocalNavigation()) { setQ(query); setOffset(0); if (query === q && !offset) await load().catch(error => setMessage(error.message)); } }}>
      <label>Find diagnostic content ID<input maxLength={200} value={query} onChange={event => setQuery(event.target.value)} /></label><button>Find performance diagnostic</button>
    </form>
    <p>{total} comparable-age items; showing {offset + (rows.length ? 1 : 0)}–{offset + rows.length}.</p>
    <div><button disabled={!offset} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setOffset(Math.max(0, offset - 20)); })}>Previous diagnostic page</button>{" "}<button disabled={offset + rows.length >= total} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setOffset(offset + 20); })}>Next diagnostic page</button></div>
    {rows.map(row => <DiagnosticCard key={row.key} row={row} onChanged={load} onExperiment={onExperiment} />)}
    {!rows.length && <p>No retained observations match a supported publication-age window. Import or explicitly sync authorized measurements first.</p>}
    {message && <p role="status">{message}</p>}
  </section>;
}
