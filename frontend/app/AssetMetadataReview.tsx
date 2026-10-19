"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, apiBase, AssetMetadata, AssetMetadataReview as Review } from "../lib/api";
import { useLocalDraft } from "../lib/localDraft";
import { LocalDraftStatus } from "./LocalDraftStatus";
import { assetCueDraft } from "../lib/assetDraft";

function fields(view: Review) {
  const m = view.metadata;
  return { version: view.version, license: String(m.license), attestation: m.attestation, description: m.description,
    tags: m.tags.join(", "), people: m.people.join(", "), cues: JSON.stringify(m.caption_cues.map(c => ({ start: String(c.start), end: String(c.end), text: c.text }))),
    use_status: String(m.use_status), rationale: "" };
}

export function AssetMetadataReview({ id, onChanged }: { id: number; onChanged: () => void }) {
  const [view, setView] = useState<Review | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const alive = useRef(true), generation = useRef(0);
  async function load() {
    const ticket = ++generation.current; setBusy(true);
    try { const next = await api.assetMetadataReview(id); if (alive.current && ticket === generation.current) { setView(next); setError(""); } }
    catch (e) { if (alive.current && ticket === generation.current) setError(e instanceof Error ? e.message : "Review unavailable"); }
    finally { if (alive.current && ticket === generation.current) setBusy(false); }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; generation.current++; }; }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  return <section aria-label={`Metadata review for asset ${id}`} style={{ display: "grid", gap: 10, marginTop: 12 }}>
    <h4>Review asset metadata and use permission</h4>
    <button type="button" disabled={busy} onClick={() => void load()}>Reload current metadata review</button>
    {error && <p role="alert">Current metadata could not be confirmed. Retained words remain private. {error}</p>}
    {view && <Editor view={view} readBusy={busy} onSaved={next => { setView(next); onChanged(); }} onReload={load} />}
  </section>;
}

function Editor({ view, readBusy, onSaved, onReload }: { view: Review; readBusy: boolean; onSaved: (next: Review) => void; onReload: () => Promise<void> }) {
  const draft = useLocalDraft(`screen-central:${apiBase()}:asset-review:${view.asset_identity}`, fields(view));
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(""), [ack, setAck] = useState("");
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const form = draft.value, parsed = assetCueDraft(form.cues), bound = JSON.stringify([view.version, form]);
  const malformed = !parsed || !["owner_created", "licensed"].includes(form.license) || !["permitted", "blocked"].includes(form.use_status);
  const change = (patch: Partial<typeof form>) => draft.change({ ...form, ...patch });
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy || readBusy || malformed || !parsed || draft.pending || draft.conflict || ack !== bound || form.version !== view.version) return;
    const submitted = { ...form }; setBusy(true); setMessage("Saving the exact reviewed decision…");
    try {
      const saved = await api.assetMetadataSave(view.asset_id, { license: form.license as AssetMetadata["license"],
        attestation: form.attestation, description: form.description, tags: form.tags.split(",").map(s => s.trim()).filter(Boolean),
        people: form.people.split(",").map(s => s.trim()).filter(Boolean), use_status: form.use_status as AssetMetadata["use_status"],
        caption_cues: parsed.map(c => ({ start: Number(c.start), end: Number(c.end), text: c.text })),
        rationale: form.rationale, expected_version: view.version, reviewed: true });
      if (!alive.current) return;
      const receipt = saved.is_current ? "Reviewed metadata saved as an immutable owner decision." : "This exact decision was saved earlier and has since been superseded. The newer decision remains current.";
      if (saved.is_current) draft.saved(fields(saved.view));
      // A superseded retry retains the submitted private words for explicit comparison.
      else draft.change(submitted);
      setAck(""); setMessage(receipt); onSaved(saved.view);
    } catch (e) {
      if (!alive.current) return;
      setAck(""); setMessage(e instanceof Error ? e.message : "Metadata save unconfirmed; reload and retry the exact decision.");
      await onReload();
    } finally { if (alive.current) setBusy(false); }
  }
  return <form aria-label={`Asset ${view.asset_id} metadata decision`} onSubmit={submit}>
    <p>Original bytes and import provenance stay retained. These labels, caption timings and permissions are your attestations. Blocking use prevents later controlled renders or release; it cannot recall an accepted or outstanding provider submission.</p>
    <p>Source #{view.asset_id} · {view.kind} · {view.seconds == null ? "still" : `${view.seconds.toFixed(3)}s`} · {view.history_count} retained decisions</p>
    <code style={{ overflowWrap: "anywhere" }}>{view.sha256}</code>
    {message && <p role="status">{message}</p>}
    <fieldset disabled={busy || readBusy} style={{ display: "grid", gap: 8 }}>
      <label>Reviewed permission<select aria-label="Reviewed permission" value={form.license} onChange={e => change({ license: e.target.value })}><option value="owner_created">I created this media</option><option value="licensed">I hold a suitable license</option></select></label>
      <label>Reviewed rights and use<textarea aria-label="Reviewed rights and use" required minLength={20} maxLength={2000} value={form.attestation} onChange={e => change({ attestation: e.target.value })} /></label>
      <label>Reviewed description<textarea aria-label="Reviewed description" required minLength={10} maxLength={2000} value={form.description} onChange={e => change({ description: e.target.value })} /></label>
      <label>Reviewed tags<input aria-label="Reviewed tags" maxLength={2500} value={form.tags} onChange={e => change({ tags: e.target.value })} /></label>
      <label>Reviewed people/entities<input aria-label="Reviewed people/entities" maxLength={2000} value={form.people} onChange={e => change({ people: e.target.value })} /></label>
      {view.kind !== "image" && <fieldset><legend>Reviewed caption intervals</legend>{(parsed || []).map((cue, at) => <div key={at}>
        {(["start", "end", "text"] as const).map(key => <label key={key}>{`Reviewed cue ${at + 1} ${key}`}<input aria-label={`Reviewed cue ${at + 1} ${key}`} required type={key === "text" ? "text" : "number"} min={key === "text" ? undefined : 0} step={key === "text" ? undefined : .001} value={cue[key]} onChange={e => change({ cues: JSON.stringify(parsed!.map((c, i) => i === at ? { ...c, [key]: e.target.value } : c)) })} /></label>)}
        <button type="button" onClick={() => change({ cues: JSON.stringify(parsed!.filter((_, i) => i !== at)) })}>Remove reviewed cue {at + 1}</button>
      </div>)}<button type="button" disabled={!parsed || parsed.length >= 500} onClick={() => change({ cues: JSON.stringify([...(parsed || []), { start: "0", end: "1", text: "" }]) })}>Add reviewed caption interval</button></fieldset>}
      <label>Use permission<select aria-label="Use permission" value={form.use_status} onChange={e => change({ use_status: e.target.value })}><option value="permitted">Permitted for reviewed intended use</option><option value="blocked">Block future use</option></select></label>
      <label>Reason for this exact decision<textarea aria-label="Reason for this exact decision" required minLength={20} maxLength={4000} value={form.rationale} onChange={e => change({ rationale: e.target.value })} /></label>
      {malformed && <div role="alert">Recovered review fields are malformed. Recover the private words or discard the draft.<pre style={{ whiteSpace: "pre-wrap" }}>{form.cues.slice(0, 500000)}</pre></div>}
      {form.version !== view.version && <div role="alert"><p>The source metadata version changed. Compare the current saved fields and original provenance with your retained draft.</p><button type="button" onClick={() => { change({ version: view.version }); draft.keepAfterReview(); setAck(""); }}>Use current source version with retained draft</button></div>}
      <LocalDraftStatus draft={draft} noun="asset metadata review" destination="immutable owner decision" />
      <label><input type="checkbox" checked={ack === bound} onChange={e => setAck(e.target.checked ? bound : "")} />I inspected this source and reviewed these exact metadata, permission and reason fields.</label>
      <button disabled={!view.identity_known || malformed || busy || readBusy || !!draft.pending || draft.conflict || ack !== bound || form.version !== view.version}>Save reviewed asset decision</button>
    </fieldset>
    <details><summary>Original import provenance</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(view.original, null, 2)}</pre></details>
    <details><summary>Immutable metadata/use history (latest 20)</summary>{view.history.map(row => <div key={row.id}><strong>Revision {row.revision} · {row.created_at}</strong><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(row.data, null, 2)}</pre></div>)}</details>
  </form>;
}
