"use client";

import React, { useEffect, useRef, useState } from "react";
import { StopButton } from "./StopButton";
import { api, type ChannelArchive, type ArchiveCapacity } from "../lib/api";

const MOVIE_CLIPS = "1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo";
const bytes = (n: number | null) => n === null ? "unavailable" : `${(n / 1024 ** 3).toFixed(2)} GiB`;

export function ChannelIngestPanel({ onIngested }: { onIngested: () => void }) {
  const [url, setUrl] = useState("");
  const [parent, setParent] = useState(MOVIE_CLIPS);
  const [formats, setFormats] = useState(["shorts"]);
  const [channels, setChannels] = useState<ChannelArchive[]>([]);
  const [capacity, setCapacity] = useState<Record<string, ArchiveCapacity>>({});
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [restored, setRestored] = useState(false);
  const alive = useRef(true);

  async function refresh() {
    const result = await api.archiveChannels();
    if (alive.current) setChannels(result.channels);
  }
  useEffect(() => {
    alive.current = true;
    try {
      const saved = JSON.parse(localStorage.getItem("archive_draft") || "null");
      if (saved) { setUrl(saved.url || ""); setParent(saved.parent || MOVIE_CLIPS); }
    } catch { /* malformed local draft cannot hide durable archives */ }
    setRestored(true);
    refresh().catch(e => setError(e.message));
    return () => { alive.current = false; };
  }, []);
  useEffect(() => {
    if (restored) localStorage.setItem("archive_draft", JSON.stringify({ url, parent }));
  }, [url, parent, restored]);
  useEffect(() => {
    if (!busy && !channels.some(c => c.active_lease)) return;
    const timer = setInterval(() => refresh().catch(() => {}), 3000);
    return () => clearInterval(timer);
  }, [busy, channels]);

  async function job(start: () => Promise<{ job_id: string }>) {
    setBusy(true); setError(""); setStatus("Starting…");
    try {
      const started = await start();
      if (alive.current) setJobId(started.job_id);
      await api.waitJob(started.job_id, j => { if (alive.current && j.message) setStatus(j.message); });
      if (alive.current) { setStatus("Operation completed. Review the durable counts and any errors below."); await refresh(); onIngested(); }
    } catch (e: unknown) {
      if (alive.current) { setError(e instanceof Error ? e.message : String(e)); await refresh().catch(() => {}); }
    } finally {
      if (alive.current) { setBusy(false); setJobId(null); }
    }
  }
  async function preflight(cid: string) {
    setError("");
    try { const result = await api.archiveCapacity(cid); if (alive.current) setCapacity(old => ({ ...old, [cid]: result })); }
    catch (e: unknown) { if (alive.current) setError(e instanceof Error ? e.message : String(e)); }
  }
  async function downloadManifest(cid: string) {
    try {
      const data = await api.archiveManifest(cid);
      const href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a"); link.href = href; link.download = `${cid}-manifest.json`; link.click();
      setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
  }

  return <section aria-label="Channel archives" style={{ padding: 20, display: "grid", gap: 16 }}>
    <h2>Channel archive to Google Drive</h2>
    <p>Inventory the entire available library first, then archive authorized video files by source channel. No video-count cap. Archives stay separate from the publishing queue.</p>
    <label>Channel URL<input aria-label="Archive channel URL" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://www.youtube.com/@ChannelName" /></label>
    <label>Drive parent folder ID<input aria-label="Archive Drive parent folder" value={parent} onChange={e => setParent(e.target.value)} /></label>
    <fieldset disabled={busy}><legend>Library formats</legend>
      {[["shorts", "Shorts"], ["videos", "Regular uploads"], ["streams", "Public archived livestreams"]].map(([key, label]) =>
        <label key={key} style={{ marginRight: 16 }}><input type="checkbox" checked={formats.includes(key)} onChange={e => setFormats(old => e.target.checked ? [...old, key] : old.filter(x => x !== key))} /> {label}</label>)}
    </fieldset>
    <button className="primary" disabled={busy || !url.trim() || !formats.length} onClick={() => job(() => api.inventoryArchive({ url: url.trim(), parent_folder_id: parent.trim(), formats }))}>Inventory complete channel</button>
    <p role="status" aria-live="polite">{status}</p>
    {error && <p role="alert" style={{ color: "var(--danger, #ef4444)" }}>{error}</p>}
    {jobId && <StopButton what="Verified Drive copies and resumable local files are retained" onStop={() => api.stopJob(jobId)} />}
    <button disabled={busy} onClick={() => refresh().catch(e => setError(e.message))}>Refresh archive progress</button>
    {channels.map(channel => <article key={channel.channel_id} aria-label={`Archive ${channel.title}`} style={{ border: "1px solid var(--border)", padding: 16 }}>
      <h3>{channel.title} {channel.handle}</h3>
      <p>{channel.channel_id} · {channel.formats.join(", ")} · {channel.status}</p>
      {channel.active_job && <p role="status">{channel.active_job.message}</p>}
      {channel.active_lease && <StopButton what="Verified copies and interrupted local files are retained" onStop={async () => { await api.stopArchive(channel.channel_id); await refresh(); }} />}
      <p data-testid="archive-counts">Discovered {channel.counts.discovered} · Downloaded {channel.counts.downloaded} · Uploaded {channel.counts.uploaded} · Skipped/restricted {channel.counts.skipped} · Failed {channel.counts.failed} · Remaining {channel.counts.remaining}</p>
      <p>Uploaded storage: {bytes(channel.uploaded_bytes)}. Inventory estimate: {bytes(channel.inventory.estimated_bytes)} known; {channel.inventory.unknown_size_count} sizes unavailable.</p>
      {channel.counts.missing_from_latest_inventory > 0 && <p>{channel.counts.missing_from_latest_inventory} retained records are missing from the latest inventory. They are preserved.</p>}
      {channel.error && <p role="alert">{channel.error}</p>}
      {channel.drive_folder_id && <p><a href={`https://drive.google.com/drive/folders/${encodeURIComponent(channel.drive_folder_id)}`} target="_blank" rel="noreferrer">Open channel Drive folder</a></p>}
      <button disabled={busy} onClick={() => preflight(channel.channel_id)}>Check storage capacity</button>{" "}
      <button disabled={busy || channel.active_lease || !channel.inventory.complete} onClick={() => job(() => api.runArchive(channel.channel_id))}>Archive missing files</button>{" "}
      <button disabled={busy || channel.active_lease || !channel.inventory.complete} onClick={() => job(() => api.runArchive(channel.channel_id, true))}>Retry failures / resume</button>{" "}
      <button disabled={busy || channel.active_lease || !channel.inventory.complete} onClick={async () => {
        try { await api.handoffArchive(channel.channel_id); await refresh(); setStatus("Waiting for the authorized Mac worker. Keep its local archive files until verified receipts arrive."); }
        catch (e: unknown) { setError(e instanceof Error ? e.message : String(e)); }
      }}>Send to Mac worker</button>{" "}
      {channel.status === "awaiting_worker" && <p>Mac worker command: <code>python scripts/archive_worker.py --channel {channel.channel_id}</code>. The configured local worker also processes explicit handoffs.</p>}
      <button onClick={() => downloadManifest(channel.channel_id)}>Download channel manifest</button>
      {capacity[channel.channel_id] && <p role="status">Local free: {bytes(capacity[channel.channel_id].local_free_bytes)}; Drive used: {bytes(capacity[channel.channel_id].drive_usage_bytes)}; Drive available: {bytes(capacity[channel.channel_id].drive_available_bytes)}. {capacity[channel.channel_id].quota_note}</p>}
    </article>)}
  </section>;
}
