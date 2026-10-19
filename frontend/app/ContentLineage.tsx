"use client";

import { useEffect, useState } from "react";
import { api, AudienceAdapterStatus, ContentBinding, ContentSource } from "../lib/api";

const empty = { platform: "youtube", account_id: "", content_id: "", format: "short", published: "", source: "", evidence: "" };
const localTime = (value: string) => { const stamp = new Date(value); return new Date(stamp.getTime() - stamp.getTimezoneOffset() * 60000).toISOString().slice(0, 23); };
const field = { display: "grid", gap: 5 };

export function ContentLineage({ onChanged }: { onChanged: () => void }) {
  const [sources, setSources] = useState<ContentSource[]>([]);
  const [rows, setRows] = useState<ContentBinding[]>([]);
  const [history, setHistory] = useState<ContentBinding[]>([]);
  const [status, setStatus] = useState<AudienceAdapterStatus | null>(null);
  const [form, setForm] = useState(empty);
  const [editing, setEditing] = useState<string | null>(null);
  const [ack, setAck] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const selected = sources.find(row => `${row.kind}:${row.id}` === form.source);
  const version = JSON.stringify([form, selected?.fingerprint, editing]);

  async function load() {
    const [available, bindings, adapter] = await Promise.all([api.contentSources(), api.contentLineages(), api.audienceStatus()]);
    setSources(available.sources); setRows(bindings.bindings); setHistory(bindings.versions); setStatus(adapter);
  }
  useEffect(() => { let active = true;
    Promise.all([api.contentSources(), api.contentLineages(), api.audienceStatus()]).then(([available, bindings, adapter]) => {
      if (active) { setSources(available.sources); setRows(bindings.bindings); setHistory(bindings.versions); setStatus(adapter); }
    }).catch(e => { if (active) setMessage(e.message); });
    return () => { active = false; };
  }, []);

  async function act(fn: () => Promise<string>) {
    setBusy(true); setMessage("");
    try { setMessage(await fn()); await load(); onChanged(); }
    catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Content evidence operation failed"); }
    finally { setBusy(false); }
  }

  return <section style={{ display: "grid", gap: 12, border: "1px solid var(--border)", borderRadius: 10, padding: 16 }}>
    <h2>Content, performance and cost lineage</h2>
    <p>Map a published identifier to a retained export after comparing its exact media and inputs. Mappings are owner attestations; public bytes are not automatically compared. Corrections preserve earlier versions and their observations.</p>
    <p>YouTube adapter: {status?.status || "loading"}. {status?.reason} Reports use the provider&apos;s processed Pacific reporting days. Fetching today does not imply measurements through today.</p>
    <button disabled={busy} onClick={() => void act(async () => "Content sources and mappings refreshed.")}>Refresh content mappings</button>
    {message && <p role="status">{message}</p>}
    <form aria-label="Content mapping" style={{ display: "grid", gap: 10 }} onSubmit={event => {
      event.preventDefault(); if (!selected || ack !== version) return;
      void act(async () => {
        await api.bindContent({ platform: form.platform, account_id: form.account_id, content_id: form.content_id, format: form.format,
          published_at: editing && rows.find(row => row.id === editing) && form.published === localTime(rows.find(row => row.id === editing)!.data.decision.published_at) ? rows.find(row => row.id === editing)!.data.decision.published_at : new Date(form.published).toISOString(), source_kind: selected.kind, source_id: selected.id,
          expected_source_fingerprint: selected.fingerprint, expected_current_id: editing, mapping_evidence: form.evidence, reviewed: true });
        setForm(empty); setEditing(null); setAck(null); return "Exact content mapping recorded. No content was published.";
      });
    }}>
      <h3>{editing ? "Record a corrected mapping" : "Map a published item"}</h3>
      <label style={field}>Mapping platform<select aria-label="Mapping platform" value={form.platform} onChange={e => setForm({ ...form, platform: e.target.value })}>{["youtube", "instagram", "facebook"].map(value => <option key={value}>{value}</option>)}</select></label>
      <label style={field}>Account/channel ID<input required minLength={3} maxLength={128} value={form.account_id} onChange={e => setForm({ ...form, account_id: e.target.value })} /></label>
      <label style={field}>Published content ID<input required minLength={3} maxLength={255} value={form.content_id} onChange={e => setForm({ ...form, content_id: e.target.value })} /></label>
      <label style={field}>Mapped format<select aria-label="Mapped format" value={form.format} onChange={e => setForm({ ...form, format: e.target.value })}>{["longform", "short", "reel", "carousel", "image", "community", "poll"].map(value => <option key={value}>{value}</option>)}</select></label>
      <label style={field}>Mapped publication time<input required type="datetime-local" step="0.001" value={form.published} onChange={e => setForm({ ...form, published: e.target.value })} /></label>
      <label style={field}>Exact retained source<select aria-label="Exact retained source" required value={form.source} onChange={e => setForm({ ...form, source: e.target.value })}>
        <option value="">Choose a retained version</option>{sources.map(row => <option key={`${row.kind}:${row.id}`} value={`${row.kind}:${row.id}`}>{row.title} · {row.kind} · {row.id.slice(0, 10)}</option>)}</select></label>
      {!sources.length && <p>Capture a retained export in Studio history, or render a native export, then refresh these sources.</p>}
      {selected?.preview && (selected.preview.kind === "video" ? <video controls preload="metadata" src={api.contentPreview(selected)} style={{ maxWidth: 480, width: "100%" }} /> : <img alt="Selected exact retained export" src={api.contentPreview(selected)} style={{ maxWidth: 300, width: "100%" }} />)}
      {selected && !selected.preview && <p>This retained manifest has no available byte preview. The mapping remains your attestation.</p>}
      <label style={field}>Mapping evidence and correction rationale<textarea aria-label="Mapping evidence and correction rationale" required minLength={20} maxLength={4000} value={form.evidence} onChange={e => setForm({ ...form, evidence: e.target.value })} /></label>
      <label><input type="checkbox" checked={ack === version} onChange={e => setAck(e.target.checked ? version : null)} /> I reviewed this exact public identifier, publication time and retained source.</label>
      <button disabled={busy || !selected || ack !== version}>Record exact content mapping</button>
      {editing && <button type="button" onClick={() => { setEditing(null); setForm(empty); setAck(null); }}>Cancel mapping correction</button>}
    </form>
    {rows.map(row => <article key={row.id} data-testid={`content-mapping-${row.id}`} style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <h3>{row.platform} · {row.content_id} · mapping revision {row.revision}</h3>
      <p>Account: {row.account_id} · {row.data.decision.format} · {row.data.decision.published_at}</p>
      <p>Local project cost snapshot: {row.data.cost_snapshot.amount_usd === null ? "Unattributed" : `$${row.data.cost_snapshot.amount_usd.toFixed(6)}`}. {row.data.cost_snapshot.scope || row.data.cost_snapshot.reason}</p>
      {row.data.cost_snapshot.limitations?.map(text => <p key={text}>{text}</p>)}
      <button disabled={busy} onClick={() => {
        const decision = row.data.decision;
        setForm({ platform: row.platform, account_id: row.account_id, content_id: row.content_id, format: decision.format,
          published: localTime(decision.published_at),
          source: `${decision.source_kind}:${decision.source_id}`, evidence: "" }); setEditing(row.id); setAck(null);
      }}>Correct mapping for {row.content_id}</button>
      <button disabled={busy || row.platform !== "youtube" || !status || status.status === "unavailable"} onClick={() => void act(async () => {
        const result = await api.audienceSync(row.id);
        return result.status === "observed" ? `Read-only observation ${result.created ? "recorded" : "already retained"} through ${result.through_day}.` : `${result.status}: ${result.reason || "No usable measurements were returned."}`;
      })}>Sync read-only YouTube metrics for {row.content_id}</button>
    </article>)}
    <details><summary>Mapping history ({history.length} recent versions)</summary>{history.map(row => <p key={row.id}>{row.content_id} · revision {row.revision} · {row.data.decision.mapping_evidence}</p>)}</details>
  </section>;
}
