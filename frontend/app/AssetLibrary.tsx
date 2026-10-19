"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, apiBase, CatalogAsset } from "../lib/api";

import { useLocalDraft } from "../lib/localDraft";
import { LocalDraftStatus } from "./LocalDraftStatus";
import { assetCueDraft, AssetCueDraft, emptyAssetForm } from "../lib/assetDraft";

const field = { display: "grid", gap: 5 };

export function AssetLibrary() {
  const [file, setFile] = useState<File | null>(null);
  const draft = useLocalDraft(`screen-central:${apiBase()}:asset-import`, emptyAssetForm, { restoreTemplate: true });
  const { kind, license, description, attestation, tags, people } = draft.value;
  const parsedCues = assetCueDraft(draft.value.cues), cues = parsedCues || [];
  const malformed = !parsedCues || !["video", "audio"].includes(kind) || !["owner_created", "licensed"].includes(license);
  const change = (patch: Partial<typeof emptyAssetForm>) => draft.change({ ...draft.value, ...patch });
  const setKind = (kind: string) => change({ kind }), setLicense = (license: string) => change({ license });
  const setDescription = (description: string) => change({ description }), setAttestation = (attestation: string) => change({ attestation });
  const setTags = (tags: string) => change({ tags }), setPeople = (people: string) => change({ people });
  const setCues = (cues: AssetCueDraft[]) => change({ cues: JSON.stringify(cues) });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [rows, setRows] = useState<CatalogAsset[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const alive = useRef(true), readGeneration = useRef(0);
  const [readBusy, setReadBusy] = useState(false), [readError, setReadError] = useState("");
  const [hasLoaded, setHasLoaded] = useState(false);
  const [loadedQuery, setLoadedQuery] = useState({ query: "", kind: "" });

  async function search(text: string, mediaKind: string): Promise<{ current: boolean; error?: string }> {
    const generation = ++readGeneration.current; setReadBusy(true);
    try {
      const result = await api.assetSearch(text, mediaKind);
      if (!alive.current || generation !== readGeneration.current) return { current: false };
      setRows(result.assets); setTotal(result.total); setHasLoaded(true); setReadError(""); setLoadedQuery({ query: text, kind: mediaKind });
      return { current: true };
    } catch (error: unknown) {
      if (!alive.current || generation !== readGeneration.current) return { current: false };
      const message = error instanceof Error ? error.message : "Could not read the asset catalog";
      setReadError(message); return { current: true, error: message };
    } finally { if (alive.current && generation === readGeneration.current) setReadBusy(false); }
  }
  useEffect(() => {
    alive.current = true; void search("", "");
    return () => { alive.current = false; readGeneration.current++; };
  }, []);

  async function index(event: FormEvent) {
    event.preventDefault(); if (!file || malformed || draft.pending || draft.conflict) return;
    const submitted = draft.value;
    setBusy(true); setMessage("Inspecting and indexing local media…");
    try {
      const row = await api.assetImport(file, { kind, license, description, attestation, tags, people,
        caption_cues: JSON.stringify(cues.map(cue => ({ start: Number(cue.start), end: Number(cue.end), text: cue.text }))) });
      if (!alive.current) return;
      const receipt = row.duplicate ? "Duplicate media reused. Existing permissions and annotations were preserved; form metadata has not replaced that record." : `Media indexed locally with verified bytes and preview metadata. Asset #${row.id}.`;
      if (!row.duplicate) draft.saved(submitted);
      setMessage(receipt);
      const refreshed = await search(query, filter);
      if (alive.current && refreshed.current && refreshed.error) setMessage(`${receipt} Catalog refresh unavailable: ${refreshed.error}`);
    } catch (error: unknown) { if (alive.current) setMessage(error instanceof Error ? error.message : "Media import failed"); }
    finally { if (alive.current) setBusy(false); }
  }

  return <section style={{ display: "grid", gap: 14 }}>
    <h2>Authorized asset library</h2>
    <p>Import your permitted local media. Video/audio sources are limited to 50 MiB and 0.25–300 seconds. The library strips source metadata, decodes the normalized file, and keeps checksum-protected bytes. Nothing is fetched from a channel or published.</p>
    <p>Search uses tags, descriptions and your caption text. Scene identity, permissions and caption alignment are owner-supplied; no semantic model or face recognition has run.</p>
    {message && <p role="status">{message}</p>}
    <details><summary>Index a local video or audio asset</summary>
      <form aria-label="Authorized asset import" onSubmit={index} style={{ marginTop: 12 }}>
        <fieldset disabled={busy} style={{ border: 0, padding: 0, display: "grid", gap: 10 }}>
        <label style={field}>Asset kind<select aria-label="Asset kind" value={kind} onChange={e => setKind(e.target.value)}><option value="video">Video</option><option value="audio">Audio</option></select></label>
        <label style={field}>Local media file<input type="file" required accept="video/*,audio/*" onChange={e => { const selected = e.target.files?.[0] || null; setFile(selected); change({ fileHint: selected ? `${selected.name} (${selected.size} bytes)` : "" }); }} /></label>
        {!file && draft.value.fileHint && <p role="status">Previous selection: {draft.value.fileHint}. Choose the local file again; only form metadata was recovered.</p>}
        <label style={field}>Asset permission<select aria-label="Asset permission" value={license} onChange={e => setLicense(e.target.value)}><option value="owner_created">I created this media</option><option value="licensed">I hold a suitable license</option></select></label>
        <label style={field}>Asset rights and intended use<textarea aria-label="Asset rights and intended use" required minLength={20} maxLength={2000} value={attestation} onChange={e => setAttestation(e.target.value)} /></label>
        <label style={field}>Asset scene description<textarea aria-label="Asset scene description" required minLength={10} maxLength={2000} value={description} onChange={e => setDescription(e.target.value)} /></label>
        <label style={field}>Asset tags<input maxLength={2500} value={tags} onChange={e => setTags(e.target.value)} /></label>
        <label style={field}>People/entities (owner labels)<input maxLength={2000} value={people} onChange={e => setPeople(e.target.value)} /></label>
        <fieldset><legend>Owner-supplied caption intervals (optional)</legend>
          {cues.map((cue, index) => <div key={index} style={{ display: "grid", gap: 6, marginBottom: 12 }}>
            {(["start", "end", "text"] as const).map(key => <label key={key}>{`Cue ${index + 1} ${key}`}<input aria-label={`Cue ${index + 1} ${key}`} required type={key === "text" ? "text" : "number"} min={key === "text" ? undefined : 0} step={key === "text" ? undefined : 0.001} maxLength={key === "text" ? 500 : undefined} value={cue[key]} onChange={e => setCues(cues.map((row, at) => at === index ? { ...row, [key]: e.target.value } : row))} /></label>)}
            <button type="button" onClick={() => setCues(cues.filter((_, at) => at !== index))}>Remove cue {index + 1}</button>
          </div>)}
          <button type="button" disabled={cues.length >= 500} onClick={() => setCues([...cues, { start: "0", end: "1", text: "" }])}>Add caption interval</button>
        </fieldset>
        {malformed && <div role="alert"><p>Recovered asset-import data is malformed. The private caption words are retained below; repair or discard the draft before importing.</p><details><summary>Retained caption words</summary><pre style={{ whiteSpace: "pre-wrap" }}>{draft.value.cues.slice(0, 500000)}</pre></details></div>}
        <LocalDraftStatus draft={draft} noun="asset import template" destination="catalog through a new import" />
        <button disabled={busy || !file || malformed || draft.conflict || !!draft.pending}>Index local media</button>
        </fieldset>
      </form>
    </details>
    <form aria-label="Asset search" onSubmit={event => { event.preventDefault(); void search(query, filter); }}>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, display: "flex", gap: 10, flexWrap: "wrap" }}>
        <label>Search asset context<input value={query} maxLength={200} onChange={e => setQuery(e.target.value)} /></label>
        <label>Filter asset kind<select aria-label="Filter asset kind" value={filter} onChange={e => setFilter(e.target.value)}><option value="">All</option>{["image", "video", "audio"].map(value => <option key={value}>{value}</option>)}</select></label>
        <button>Search indexed assets</button>
        <button type="button" disabled={readBusy} onClick={() => void search(query, filter)}>Refresh asset catalog</button>
      </fieldset>
    </form>
    {readError && <p role="alert">Asset catalog unavailable. {hasLoaded ? "Showing the last loaded results; this list may be out of date." : "Catalog contents are unconfirmed."} {readError}</p>}
    {readBusy && <p role="status">Reading asset catalog…</p>}
    {hasLoaded && <p>{total} matching assets; showing the top {rows.length}. Last loaded search: {loadedQuery.query || "all context"} · {loadedQuery.kind || "all kinds"}. Keyword ranking is a retrieval aid, not proof of relevance or identity.</p>}
    {rows.map(row => <AssetCard key={row.id} row={row} />)}
  </section>;
}

function AssetCard({ row }: { row: CatalogAsset }) {
  const player = useRef<HTMLVideoElement & HTMLAudioElement>(null);
  const metadata = row.provenance.media;
  const [failed, setFailed] = useState(false);
  return <article data-testid={`indexed-asset-${row.id}`} style={{ padding: 14, border: "1px solid var(--border)", borderRadius: 10 }}>
    <h3>Asset #{row.id} · {row.kind}</h3><p>{row.provenance.description || row.tags.join(", ")}</p>
    <p>{row.tags.join(", ")} · {row.provenance.license} · {metadata ? `${metadata.seconds.toFixed(2)}s · ${metadata.bytes} bytes` : "indexed still"}</p>
    {row.kind === "image" ? <img onError={() => setFailed(true)} src={api.assetMedia(row.id)} alt={row.tags.join(", ") || "Owner indexed still"} style={{ maxWidth: 320, width: "100%" }} /> : row.kind === "video" ?
      <video ref={player} onError={() => setFailed(true)} controls preload="metadata" src={api.assetMedia(row.id)} style={{ maxWidth: 400, width: "100%" }} /> :
      <audio ref={player} onError={() => setFailed(true)} controls preload="metadata" src={api.assetMedia(row.id)} />}
    {!!metadata?.frames.length && <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{metadata.frames.map((frame, index) => <figure key={index} style={{ margin: 0 }}>
      <img onError={() => setFailed(true)} src={api.assetFrame(row.id, index)} alt={`Sample frame at ${frame.at.toFixed(2)} seconds; identity not inferred`} style={{ width: 140 }} /><figcaption>{frame.at.toFixed(2)}s</figcaption>
    </figure>)}</div>}
    {failed && <p role="alert">Media or preview could not be verified or loaded. Refresh the owner session, then recover the exact retained asset if integrity failed.</p>}
    {row.matching_spans.map((cue, index) => <p key={index}><button onClick={() => { if (player.current) { player.current.currentTime = cue.start; void player.current.play().catch(() => {}); } }}>Preview {cue.start.toFixed(2)}–{cue.end.toFixed(2)}s</button> {cue.text} (owner-supplied timing)</p>)}
    <details><summary>Rights and index provenance</summary><p>{row.provenance.attestation}</p><p>People/entities: {row.provenance.people?.join(", ") || "Not supplied"}. Owner labels and captions are unverified.</p><code style={{ overflowWrap: "anywhere" }}>{row.sha256}</code></details>
  </article>;
}
