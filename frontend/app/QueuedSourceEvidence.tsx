"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, QueueItem, QueuedSourceSnapshot, QueuedSourceView } from "../lib/api";

function Snapshot({ value }: { value: QueuedSourceSnapshot }) {
  const script = value.script && typeof value.script === "object" ? value.script as Record<string, unknown> : {};
  const words = Array.isArray(script.sentences)
    ? script.sentences.flatMap(sentence => sentence && typeof sentence.text === "string" ? [sentence.text] : [])
    : [script.intro, script.outro].filter((text): text is string => typeof text === "string" && text.length > 0);
  return <article style={{ borderTop: "1px solid #475569", marginTop: 16 }}>
    <h3>{value.title} · {value.kind === "studio" ? "LongForm" : `Countdown${value.format ? ` · ${value.format.replace(/^top/, "Top ")}` : ""}`}</h3>
    {value.requested_duration && <p>Retained requested length: {value.requested_duration.target_seconds === null ? "unconfirmed" : `${(value.requested_duration.target_seconds / 60).toFixed(1)} minutes`} ({value.requested_duration.basis}).</p>}
    <h4>Exact retained narration{value.kind === "countdown" ? " — intro and outro" : ""}</h4>
    {words.length ? words.map((text, index) => <p key={index} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{text}</p>) : <p>No narration text in this source snapshot.</p>}
    <details><summary>Complete retained script and timing</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(value.script, null, 2)}</pre></details>
    <details><summary>Retained facts and owner acceptance</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ facts: value.facts, approval: value.approval }, null, 2)}</pre></details>
    <details><summary>Retained media checksums</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(value.assets, null, 2)}</pre></details>
    <details><summary>Source identity and capture times</summary>
      <p>Retained {value.captured_at}. Source created {value.source_created_at}.</p>
      <p style={{ overflowWrap: "anywhere" }}>Export identity: {value.id}</p>
    </details>
  </article>;
}

export function QueuedSourceEvidence({ items }: { items: QueueItem[] }) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [view, setView] = useState<QueuedSourceView | null>(null);
  const [viewKey, setViewKey] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const selected = items.find(item => `${item.id}:${item.created_at}` === key);
  const id = selected?.id, stamp = selected?.created_at;
  const shown = viewKey === key && selected ? view : null;
  const stateLabels: Record<string, string> = { checks_passed: "Local checks passed", blocked: "Source checks blocked", legacy_unbound: "Legacy source needs review", native_contract: "Native campaign source", manual: "Manual or imported content" };

  const load = useCallback(async () => {
    const ticket = ++generation.current;
    pending.current?.abort();
    if (id === undefined || !stamp) return;
    const controller = new AbortController(); pending.current = controller;
    const deadline = window.setTimeout(() => controller.abort(), 15_000);
    setBusy(true); setError("");
    try {
      const result = await api.queueSourceContext(id, stamp, controller.signal);
      if (generation.current === ticket) { setView(result); setViewKey(`${id}:${stamp}`); }
    } catch (cause: unknown) {
      if (generation.current === ticket) setError(cause instanceof Error ? cause.message : "Source evidence unavailable");
    } finally { window.clearTimeout(deadline); if (generation.current === ticket) setBusy(false); }
  }, [id, stamp]);

  useEffect(() => {
    if (open) dialog.current?.showModal(); else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    setView(null); setError(""); setBusy(false);
    if (open && id !== undefined && stamp) void load();
    return () => { ++generation.current; pending.current?.abort(); };
  }, [open, id, stamp, load]);

  return <>
    <button style={{ fontSize: 11 }} onClick={() => {
      if (!selected) { const item = items[0]; setKey(item ? `${item.id}:${item.created_at}` : ""); }
      setOpen(true);
    }}>Queued source evidence</button>
    <dialog ref={dialog} aria-labelledby="queued-source-title" onCancel={() => setOpen(false)} style={{ color: "var(--text)", background: "var(--panel, #111827)", border: "1px solid #475569", borderRadius: 12, width: "min(850px, 92vw)", maxHeight: "85vh", overflow: "auto", padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <h2 id="queued-source-title">Queued source evidence</h2>
        <button onClick={() => setOpen(false)}>Close source evidence</button>
      </div>
      <p>This read-only view inspects the source attached to a queued export. It does not send or approve content.</p>
      <label>Source queue item <select aria-label="Source queue item" value={selected ? key : ""} onChange={event => setKey(event.target.value)}>
        <option value="">Choose an item</option>
        {items.map(item => <option key={`${item.id}:${item.created_at}`} value={`${item.id}:${item.created_at}`}>#{item.id} · {item.title || item.video_name} · {item.status}</option>)}
      </select></label>
      <button style={{ marginLeft: 12 }} disabled={!selected} onClick={() => void load()}>Refresh source checks</button>
      {busy && <p role="status">Checking retained source…</p>}
      {error && <p role="alert">{shown ? "Last retrieved evidence retained; current checks are unconfirmed. " : "Source checks unconfirmed. "}{error}</p>}
      {!selected && <p>Select an item from the current queue list. If an item was replaced, refresh that list first.</p>}
      {shown && <div data-testid="queued-source-context" aria-busy={busy}>
        <p><strong>{stateLabels[shown.state] || "Source checks unconfirmed"}</strong>: {shown.message}</p>
        <p>{shown.scope}</p>
        {shown.binding && <details><summary>Binding revision {shown.binding.revision}</summary><p style={{ overflowWrap: "anywhere" }}>{shown.binding.captured_at}<br />{shown.binding.id}</p></details>}
        {shown.source && <>
          <Snapshot value={shown.source} />
          {shown.source.current_project_matches && <button onClick={() => {
            setOpen(false);
            window.location.hash = `#${shown.source!.kind === "studio" ? "longform" : "countdowns"}/${shown.source!.project_id}`;
          }}>Open source draft (may differ from retained export)</button>}
        </>}
        {shown.segments.map(value => <Snapshot key={value.id} value={value} />)}
      </div>}
    </dialog>
  </>;
}
