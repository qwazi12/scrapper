"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, CatalogAsset } from "../lib/api";

const field = { display: "grid", gap: 5 };

export function AssetLibrary() {
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState("video");
  const [license, setLicense] = useState("owner_created");
  const [description, setDescription] = useState("");
  const [attestation, setAttestation] = useState("");
  const [tags, setTags] = useState("");
  const [people, setPeople] = useState("");
  const [cues, setCues] = useState<{ start: string; end: string; text: string }[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [rows, setRows] = useState<CatalogAsset[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const alive = useRef(true);

  async function search(text: string, mediaKind: string) {
    const result = await api.assetSearch(text, mediaKind);
    if (alive.current) { setRows(result.assets); setTotal(result.total); }
  }
  useEffect(() => {
    alive.current = true;
    search("", "").catch(e => { if (alive.current) setMessage(e.message); });
    return () => { alive.current = false; };
  }, []);

  async function index(event: FormEvent) {
    event.preventDefault(); if (!file) return;
    setBusy(true); setMessage("Inspecting and indexing local media…");
    try {
      const row = await api.assetImport(file, { kind, license, description, attestation, tags, people,
        caption_cues: JSON.stringify(cues.map(cue => ({ start: Number(cue.start), end: Number(cue.end), text: cue.text }))) });
      await search("", ""); setQuery(""); setFilter("");
      setMessage(row.duplicate ? "Duplicate media reused. Existing permissions and annotations were preserved." : "Media indexed locally with verified bytes and preview metadata.");
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Media import failed"); }
    finally { setBusy(false); }
  }

  return <section style={{ display: "grid", gap: 14 }}>
    <h2>Authorized asset library</h2>
    <p>Import your permitted local media. Video/audio sources are limited to 50 MiB and 0.25–300 seconds. The library strips source metadata, decodes the normalized file, and keeps checksum-protected bytes. Nothing is fetched from a channel or published.</p>
    <p>Search uses tags, descriptions and your caption text. Scene identity, permissions and caption alignment are owner-supplied; no semantic model or face recognition has run.</p>
    {message && <p role="status">{message}</p>}
    <details><summary>Index a local video or audio asset</summary>
      <form onSubmit={index} style={{ display: "grid", gap: 10, marginTop: 12 }}>
        <label style={field}>Asset kind<select aria-label="Asset kind" value={kind} onChange={e => setKind(e.target.value)}><option value="video">Video</option><option value="audio">Audio</option></select></label>
        <label style={field}>Local media file<input type="file" required accept="video/*,audio/*" onChange={e => setFile(e.target.files?.[0] || null)} /></label>
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
        <button disabled={busy}>Index local media</button>
      </form>
    </details>
    <form onSubmit={event => { event.preventDefault(); search(query, filter).catch(e => setMessage(e.message)); }} style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <label>Search asset context<input value={query} maxLength={200} onChange={e => setQuery(e.target.value)} /></label>
      <label>Filter asset kind<select aria-label="Filter asset kind" value={filter} onChange={e => setFilter(e.target.value)}><option value="">All</option>{["image", "video", "audio"].map(value => <option key={value}>{value}</option>)}</select></label>
      <button>Search indexed assets</button>
    </form>
    <p>{total} matching assets; showing the top {rows.length}. Keyword ranking is a retrieval aid, not proof of relevance or identity.</p>
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
