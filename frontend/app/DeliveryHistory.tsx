"use client";

import { useEffect, useState } from "react";
import { api, parseApiDate, DeliveryDetail, DeliveryHistory as History } from "../lib/api";

function safeLink(value?: string) {
  try { const url = new URL(value || ""); return url.protocol === "https:" ? url.href : undefined; }
  catch { return undefined; }
}

export function DeliveryHistory() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<History | null>(null);
  const [selected, setSelected] = useState<DeliveryDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  useEffect(() => { if (open && !data) void act(async () => setData(await api.deliveryHistory())); }, [open, data]);
  const snapshot = selected?.manifest.snapshot;
  return <details data-testid="delivery-history" open={open} onToggle={e => setOpen(e.currentTarget.open)}
    style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, margin: "10px 0" }}>
    <summary style={{ fontWeight: 700, cursor: "pointer" }}>Retained delivery history and Drive versions</summary>
    <p>Inspect the saved words, exact available media and recorded delivery evidence after a queue row is removed. Drive versions stay in place.</p>
    <button disabled={busy} onClick={() => void act(async () => setData(await api.deliveryHistory()))}>Refresh delivery history</button>
    {error && <p role="alert">{error}</p>}
    {data && !data.deliveries.length && <p>No queue deliveries have been reclaimed yet.</p>}
    <ol>{data?.deliveries.map(row => <li key={row.id} style={{ margin: "8px 0" }}>
      <strong>{row.title}</strong> · {row.pipeline} · queue #{row.queue_id} · recorded {row.status}
      <br />Retained {new Date(parseApiDate(row.created_at)).toLocaleString()}
      <button disabled={busy} aria-label={`Inspect retained delivery ${row.id}`}
        onClick={() => void act(async () => setSelected(await api.deliveryDetail(row.id)))}>Inspect delivery</button>
    </li>)}</ol>
    {snapshot && selected && <section data-testid="retained-delivery-detail">
      <h3>{snapshot.title}</h3><p>{selected.manifest.delivery_scope}</p>
      <p style={{ whiteSpace: "pre-wrap" }}>{snapshot.description}</p>
      <p>{snapshot.tags}</p><p>Targets recorded: {snapshot.accounts.join(", ")}</p>
      {snapshot.notes && <p>{snapshot.notes}</p>}
      <ul>{snapshot.publish_requests?.map((r, i) => <li key={i}>{r.profile || "Recorded request"} · {r.state || "legacy state unavailable"} · receipt {r.request_id || "unavailable"}</li>)}</ul>
      {selected.manifest.assets.map((asset, index) => <div key={index}>
        {asset.available ? <>
          <a href={api.deliveryFile(selected.id, index)} target="_blank" rel="noreferrer">Open retained {asset.role} {index + 1}</a>
          <p style={{ overflowWrap: "anywhere", fontSize: 11 }}>{asset.bytes} bytes · SHA-256 {asset.sha256}</p>
        </> : <p>{asset.role}: {asset.reason || "No local file was available when retained"}</p>}
      </div>)}
      {safeLink(snapshot.drive_link) && <a href={safeLink(snapshot.drive_link)} target="_blank" rel="noreferrer">Open retained Drive reference</a>}
    </section>}
    <details><summary>Remote copy evidence ({data?.remote_copies.length || 0})</summary>
      <p>A verified transfer receipt records the bytes checked at transfer time. Legacy references remain unverified. Copies are kept automatically.</p>
      <ul>{data?.remote_copies.map(copy => <li key={copy.id} style={{ margin: "8px 0", overflowWrap: "anywhere" }}>
        {copy.action.replaceAll("_", " ")} · {copy.evidence.role || "remote reference"} · {copy.file_id}
        <br />{copy.evidence.verification?.sha256 ? `Recorded SHA-256 ${copy.evidence.verification.sha256}` : "No byte verification recorded"}
        {safeLink(copy.evidence.link) && <> · <a href={safeLink(copy.evidence.link)} target="_blank" rel="noreferrer">Open copy reference</a></>}
      </li>)}</ul>
    </details>
    {data?.has_more && <button disabled={busy} onClick={() => void act(async () => {
      const more = await api.deliveryHistory(data.next_offset);
      setData({ ...more, deliveries: [...new Map([...data.deliveries, ...more.deliveries].map(row => [row.id, row])).values()],
        remote_copies: [...new Map([...data.remote_copies, ...more.remote_copies].map(row => [row.id, row])).values()] });
    })}>Load older delivery records</button>}
  </details>;
}
