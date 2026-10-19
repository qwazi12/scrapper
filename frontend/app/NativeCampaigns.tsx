"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api, apiBase, EditorialOpportunity, IndexedAsset, NativeBeat, NativeCampaign, NativeExport, SocialAccount } from "@/lib/api";
import { ExportAcceptance } from "./ExportAcceptance";
import { useLocalDraft } from "../lib/localDraft";
import { LocalDraftStatus } from "./LocalDraftStatus";
import { emptyNativeForm, nativeBeats, nativeFormats } from "../lib/nativeDraft";

const box = { border: "1px solid var(--border)", borderRadius: 12, padding: 16, background: "var(--panel)" };
const field = { display: "grid", gap: 5 };


export function NativeCampaigns({ opportunities }: { opportunities: EditorialOpportunity[] }) {
  const mounted = useRef(true);
  const approved = opportunities.filter(o => o.status === "approved");
  const [rows, setRows] = useState<NativeCampaign[]>([]);
  const [assets, setAssets] = useState<IndexedAsset[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const draft = useLocalDraft(`screen-central:${apiBase()}:native-campaign`, emptyNativeForm, { restoreTemplate: true });
  const { oid, title, shortCaption, carouselCaption, question } = draft.value;
  const decodedBeats = nativeBeats(draft.value.beats), decodedFormats = nativeFormats(draft.value.selected);
  const invalidDraft = !decodedBeats || !decodedFormats;
  const beats = decodedBeats || [], selected = decodedFormats || [];
  const copy = { reel_caption: draft.value.reel_caption, image_text: draft.value.image_text, image_caption: draft.value.image_caption,
    community_text: draft.value.community_text, poll_question: draft.value.poll_question, poll_options: draft.value.poll_options };
  const change = (patch: Partial<typeof emptyNativeForm>) => draft.change({ ...draft.value, ...patch });
  const setTitle = (title: string) => change({ title });
  const setBeats = (beats: NativeBeat[]) => change({ beats: JSON.stringify(beats) });
  const setSelected = (selected: string[]) => change({ selected: JSON.stringify(selected) });
  const setShortCaption = (shortCaption: string) => change({ shortCaption });
  const setCarouselCaption = (carouselCaption: string) => change({ carouselCaption });
  const setQuestion = (question: string) => change({ question });
  const setCopy = (next: typeof copy) => change(next);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [attestation, setAttestation] = useState("");
  const [tags, setTags] = useState("");
  const [license, setLicense] = useState("owner_created");
  const [file, setFile] = useState<File | null>(null);
  const incompatibleClips = beats.some(beat => assets.find(a => a.id === beat.asset_id)?.kind === "video") && selected.some(fmt => !["short", "reel"].includes(fmt));
  const incompatibleAudio = beats.some(beat => !!beat.background_audio_id) && selected.some(fmt => !["short", "reel"].includes(fmt));
  const missingAudio = beats.some(beat => !!beat.background_audio_id && !assets.some(asset => asset.id === beat.background_audio_id && asset.kind === "audio"));
  const sourceChanged = beats.some(beat => [[beat.asset_id, beat.asset_metadata_version], [beat.background_audio_id, beat.audio_metadata_version]].some(([id, version]) => {
    if (!id) return false;
    const asset = assets.find(a => a.id === id);
    return !asset || asset.use_status !== "permitted" || version !== asset.metadata_version;
  }));
  const [replacement, setReplacement] = useState<NativeCampaign | null>(null);
  function revised(c: NativeCampaign) {
    const b = c.brief;
    draft.change({ oid: String(c.opportunity_id), researchFingerprint: b.research_fingerprint || "", title: b.title, beats: JSON.stringify(b.beats), selected: JSON.stringify(b.formats),
      shortCaption: b.short_caption, carouselCaption: b.carousel_caption, question: b.community_question || "", reel_caption: b.reel_caption || "", image_text: b.image_text || "",
      image_caption: b.image_caption || "", community_text: b.community_text || "", poll_question: b.poll_question || "", poll_options: (b.poll_options || []).join("\n") });
    setReplacement(null); setMessage(`Campaign #${c.id} copied into a private revised treatment. Review current research and source metadata before creating its new version.`);
  }
  const chosen = approved.find(o => String(o.id) === oid);
  const researchChanged = !!oid && (!chosen?.approval?.fingerprint || draft.value.researchFingerprint !== chosen.approval.fingerprint);
  async function load() {
    const [campaigns, media] = await Promise.all([api.nativeCampaigns(), api.nativeAssets()]);
    if (mounted.current) { setRows(campaigns); setAssets(media); }
  }
  useEffect(() => { let alive = true; mounted.current = true;
    Promise.all([api.nativeCampaigns(), api.nativeAssets(), api.socialAccounts()]).then(([c, a, ac]) => {
      if (alive) { setRows(c); setAssets(a); setAccounts(ac.accounts || []); }
    }).catch(e => alive && setMessage(e.message));
    return () => { alive = false; mounted.current = false; };
  }, []);
  async function act(fn: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage("Working…");
    try {
      await fn(); if (!mounted.current) return; setMessage(success);
      try { await load(); } catch (error: unknown) { if (mounted.current) setMessage(`${success} Refresh unavailable: ${error instanceof Error ? error.message : "Try the view again"}`); }
    }
    catch (e) { if (mounted.current) setMessage(e instanceof Error ? e.message : String(e)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function create(e: FormEvent) {
    e.preventDefault();
    if (draft.pending || draft.conflict || invalidDraft || researchChanged || sourceChanged || !chosen) return;
    act(async () => {
      await api.nativeCreate({ opportunity_id: Number(oid), expected_research_fingerprint: draft.value.researchFingerprint, title, formats: selected, beats,
        short_caption: shortCaption, carousel_caption: carouselCaption, community_question: question, ...copy, poll_options: copy.poll_options.split("\n").filter(text => text.trim()) });
      if (mounted.current) draft.saved(emptyNativeForm);
    }, "Campaign draft saved. Render each selected treatment, then inspect its complete output.");
  }
  return <div style={{ display: "grid", gap: 16 }}>
    <h2>Native campaigns</h2><p>Use reviewed research for Short/Reel, carousel, single-image, community and poll treatments. Images use original cards and optional cleared stills. Short/Reel visuals can use explicit authorized video intervals; source audio stays muted. Optional background audio is selected separately with its own permissions and mix review. Community/polls produce private manual packages; they cannot create publishing queue items.</p>
    {message && <p role="status">{message}</p>}
    <div><button disabled={busy} onClick={async () => { setBusy(true); try { await load(); setMessage("Native campaign view refreshed."); }
      catch (error: unknown) { setMessage(error instanceof Error ? error.message : "Could not refresh native campaigns"); } finally { setBusy(false); } }}>Refresh native campaigns</button></div>
    <details style={box}><summary>Import a cleared still</summary><form style={{ display: "grid", gap: 10, marginTop: 12 }} onSubmit={e => {
      e.preventDefault(); if (file) act(() => api.nativeAssetUpload(file, license, attestation, tags), "Still indexed by content hash; duplicate imports reuse the existing asset.");
    }}>
      <label style={field}>Still image<input type="file" accept="image/png,image/jpeg,image/webp" required onChange={e => setFile(e.target.files?.[0] || null)} /></label>
      <label style={field}>Permission<select value={license} onChange={e => setLicense(e.target.value)}><option value="owner_created">I created this asset</option><option value="licensed">I hold a suitable license</option></select></label>
      <label style={field}>Rights record<textarea minLength={20} required value={attestation} onChange={e => setAttestation(e.target.value)} placeholder="Describe creation or license, permitted use and attribution" /></label>
      <label style={field}>Scene tags<input value={tags} onChange={e => setTags(e.target.value)} placeholder="e.g. original shapes, animation" /></label>
      <div><button disabled={busy}>Index cleared still</button></div>
    </form></details>
    <form aria-label="Native campaign draft" onSubmit={create} style={box}>
      <fieldset disabled={busy} style={{ border: 0, padding: 0, display: "grid", gap: 12 }}>
      <h3>Draft a treatment</h3>
      <label style={field}>Approved research<select aria-label="Approved research" required value={oid} onChange={e => {
        const row = approved.find(x => String(x.id) === e.target.value);
        change({ oid: e.target.value, researchFingerprint: row?.approval?.fingerprint || "", title: row?.title.slice(0, 72) || "",
          selected: JSON.stringify(["short", "carousel"].filter(f => row?.formats.includes(f))) });
      }}><option value="">Choose a reviewed brief</option>{approved.map(o => <option key={o.id} value={o.id}>{o.title}</option>)}</select></label>
      {researchChanged && <div role="alert"><p>The selected research changed or is no longer available as an approved brief. Your treatment words remain private; review the current research before saving.</p>
        {chosen && <><details><summary>Current approved research context</summary><p>{chosen.title} · {chosen.angle}</p>{chosen.evidence.map((source, index) => <p key={index}>{source.url} · {source.excerpt}</p>)}</details>
          <button type="button" onClick={() => change({ researchFingerprint: chosen.approval?.fingerprint || "" })}>Use current approved research for this draft</button></>}
      </div>}
      {!approved.length && <p>Approve an opportunity&apos;s research brief first.</p>}
      <label style={field}>Campaign title<input aria-label="Campaign title" required maxLength={72} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <fieldset><legend>Treatments to make</legend>{["short", "reel", "carousel", "image", "community", "poll"].filter(f => chosen?.formats.includes(f)).map(f => <label key={f} style={{ marginRight: 16 }}>
        <input type="checkbox" checked={selected.includes(f)} onChange={e => setSelected(e.target.checked ? [...selected, f] : selected.filter(x => x !== f))} /> {f}</label>)}</fieldset>
      {beats.map((beat, i) => <fieldset key={i}><legend>{beat.role === "body" ? "Main insight" : beat.role === "hook" ? "Opening hook" : "Final payoff"}</legend>
        <label style={field}>Beat text {i + 1}<textarea aria-label={`Beat text ${i + 1}`} required minLength={12} maxLength={140} value={beat.text} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, text: e.target.value } : b))} /></label>
        <label style={field}>Research excerpt {i + 1}<select aria-label={`Research excerpt ${i + 1}`} value={beat.source_refs[0]} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, source_refs: [Number(e.target.value)] } : b))}>
          {(chosen?.evidence || []).map((e, n) => <option key={n} value={n + 1}>{n + 1}: {e.excerpt.slice(0, 80)}</option>)}</select></label>
        <label style={field}>Visual for beat {i + 1}<select aria-label={`Visual for beat ${i + 1}`} value={beat.asset_id || ""} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, asset_id: e.target.value ? Number(e.target.value) : null, asset_metadata_version: assets.find(a => String(a.id) === e.target.value)?.metadata_version || null, clip_start: null, clip_end: null, clip_end_policy: null } : b))}>
          <option value="">Original graphic</option>{assets.filter(a => ["image", "video"].includes(a.kind)).map(a => <option key={a.id} value={a.id}>#{a.id} · {a.kind}: {a.tags.join(", ") || a.provenance.license}</option>)}</select></label>
        {(["visual", "audio"] as const).map(kind => {
          const id = kind === "visual" ? beat.asset_id : beat.background_audio_id;
          const version = kind === "visual" ? beat.asset_metadata_version : beat.audio_metadata_version;
          const asset = assets.find(a => a.id === id);
          return id ? <div key={kind}><p>Selected {kind} #{id} · use permission: {asset?.use_status || "unavailable"}</p>
            {asset && <details><summary>{`Current ${kind} provenance for beat ${i + 1}`}</summary><p>{asset.provenance.description}</p><p>{asset.provenance.license} · {asset.provenance.attestation}</p><code style={{ overflowWrap: "anywhere" }}>{asset.sha256}</code></details>}
            {(!asset || asset.use_status !== "permitted" || version !== asset.metadata_version) && <div role="alert"><p>This source is unavailable, blocked or has changed since selection. Retained words and timing remain private.</p>
              {asset?.use_status === "permitted" && <button type="button" onClick={() => setBeats(beats.map((b, j) => j === i ? { ...b, [kind === "visual" ? "asset_metadata_version" : "audio_metadata_version"]: asset.metadata_version } : b))}>{`Use current ${kind} metadata for beat ${i + 1}`}</button>}</div>}
          </div> : null;
        })}
        {assets.find(a => a.id === beat.asset_id)?.kind === "video" && <div style={{ display: "grid", gap: 8 }}>
          <video controls preload="metadata" style={{ maxWidth: 360, width: "100%" }} src={api.assetMedia(beat.asset_id!)} />
          <p>Indexed duration: {assets.find(a => a.id === beat.asset_id)?.provenance.media?.seconds.toFixed(3)}s. Select 0.25–12 seconds. The complete frame appears in an inset; source audio is excluded. Review all narration, text and visuals after rendering.</p>
          {(["clip_start", "clip_end"] as const).map(key => <label key={key}>{`Video ${key === "clip_start" ? "start" : "end"} for beat ${i + 1}`}<input aria-label={`Video ${key === "clip_start" ? "start" : "end"} for beat ${i + 1}`} type="number" required min={0} max={assets.find(a => a.id === beat.asset_id)?.provenance.media?.seconds} step="0.001" value={beat[key] ?? ""} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, [key]: e.target.value === "" ? null : Number(e.target.value) } : b))} /></label>)}
          <label>Video end treatment for beat {i + 1}<select aria-label={`Video end treatment for beat ${i + 1}`} required value={beat.clip_end_policy || ""} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, clip_end_policy: e.target.value as "hold" | "loop" || null } : b))}>
            <option value="">Choose intentionally</option><option value="hold">Hold the last frame if narration continues</option><option value="loop">Loop this interval if narration continues</option>
          </select></label>
        </div>}
        <label>Background audio for beat {i + 1}<select aria-label={`Background audio for beat ${i + 1}`} value={beat.background_audio_id || ""} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, background_audio_id: e.target.value ? Number(e.target.value) : null, audio_metadata_version: assets.find(a => String(a.id) === e.target.value)?.metadata_version || null, audio_start: null, audio_end: null, audio_end_policy: null, audio_gain_db: null } : b))}>
          <option value="">No background audio</option>{assets.filter(asset => asset.kind === "audio").map(asset => <option key={asset.id} value={asset.id}>#{asset.id} · {asset.tags.join(", ") || asset.provenance.license}</option>)}</select></label>
        {beat.background_audio_id && assets.some(asset => asset.id === beat.background_audio_id && asset.kind === "audio") && <div style={{ display: "grid", gap: 8 }}>
          <audio controls preload="metadata" src={api.assetMedia(beat.background_audio_id)} />
          <p>Preview is the indexed source, before mixing. Choose 0.25–12 seconds and a relative level from −36 to −12 dB. The stems are leveled before this background reduction; inspect the complete final soundtrack for masking and repetition. Narration remains complete, and background audio fades at the beat end.</p>
          {(["audio_start", "audio_end"] as const).map(key => <label key={key}>{`Background ${key === "audio_start" ? "start" : "end"} for beat ${i + 1}`}<input aria-label={`Background ${key === "audio_start" ? "start" : "end"} for beat ${i + 1}`} required type="number" min={0} max={assets.find(asset => asset.id === beat.background_audio_id)?.provenance.media?.seconds} step="0.001" value={beat[key] ?? ""} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, [key]: e.target.value === "" ? null : Number(e.target.value) } : b))} /></label>)}
          <label>Background relative level for beat {i + 1} (dB)<input aria-label={`Background relative level for beat ${i + 1} (dB)`} required type="number" min={-36} max={-12} step="0.5" value={beat.audio_gain_db ?? ""} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, audio_gain_db: e.target.value === "" ? null : Number(e.target.value) } : b))} /></label>
          <label>Background end treatment for beat {i + 1}<select aria-label={`Background end treatment for beat ${i + 1}`} required value={beat.audio_end_policy || ""} onChange={e => setBeats(beats.map((b, j) => j === i ? { ...b, audio_end_policy: e.target.value as "silence" | "loop" || null } : b))}>
            <option value="">Choose intentionally</option><option value="silence">Finish the excerpt, then leave background silence</option><option value="loop">Loop this interval if narration continues</option>
          </select></label>
        </div>}
      </fieldset>)}
      <label style={field}>Short caption<textarea aria-label="Short caption" required={selected.includes("short")} minLength={10} maxLength={2000} value={shortCaption} onChange={e => setShortCaption(e.target.value)} /></label>
      <label style={field}>Carousel caption<textarea aria-label="Carousel caption" required={selected.includes("carousel")} minLength={10} maxLength={2000} value={carouselCaption} onChange={e => setCarouselCaption(e.target.value)} /></label>
      {selected.includes("reel") && <label style={field}>Reel caption<textarea aria-label="Reel caption" required minLength={10} maxLength={2000} value={copy.reel_caption} onChange={e => setCopy({ ...copy, reel_caption: e.target.value })} /></label>}
      {selected.includes("image") && <>
        <label style={field}>Single-image text<textarea aria-label="Single-image text" required minLength={12} maxLength={140} value={copy.image_text} onChange={e => setCopy({ ...copy, image_text: e.target.value })} /></label>
        <label style={field}>Single-image caption<textarea aria-label="Single-image caption" required minLength={10} maxLength={2000} value={copy.image_caption} onChange={e => setCopy({ ...copy, image_caption: e.target.value })} /></label>
      </>}
      {selected.includes("community") && <label style={field}>Community post text<textarea aria-label="Community post text" required minLength={10} maxLength={4000} value={copy.community_text} onChange={e => setCopy({ ...copy, community_text: e.target.value })} /></label>}
      {selected.includes("poll") && <>
        <label style={field}>Poll question<textarea aria-label="Poll question" required minLength={10} maxLength={255} value={copy.poll_question} onChange={e => setCopy({ ...copy, poll_question: e.target.value })} /></label>
        <label style={field}>Poll options (one per line, 2–4)<textarea aria-label="Poll options (one per line, 2–4)" required value={copy.poll_options} onChange={e => setCopy({ ...copy, poll_options: e.target.value })} /></label>
      </>}
      <label style={field}>Community question draft<textarea aria-label="Community question draft" maxLength={1000} value={question} onChange={e => setQuestion(e.target.value)} /></label>
      {incompatibleClips && <p role="alert">Video intervals currently require a Short/Reel-only campaign. Choose stills for image, carousel or manual formats.</p>}
      {incompatibleAudio && <p role="alert">Background audio currently requires a Short/Reel-only campaign.</p>}
      {missingAudio && <p role="alert">A selected background source is unavailable in the current audio library. Choose a current source or remove that selection.</p>}
      {invalidDraft && <div role="alert"><p>Recovered treatment data is malformed. It remains on this browser; recover the words below or discard the draft before creating a campaign.</p><details><summary>Retained treatment words</summary><pre style={{ whiteSpace: "pre-wrap" }}>{draft.value.beats.slice(0, 100000)}</pre></details></div>}
      <LocalDraftStatus draft={draft} noun="native treatment template" destination="private campaign draft" />
      <div><button disabled={busy || !oid || !selected.length || incompatibleClips || incompatibleAudio || missingAudio || invalidDraft || researchChanged || sourceChanged || draft.conflict || !!draft.pending}>Create native campaign</button></div>
      </fieldset>
    </form>
    {rows.map(c => <article key={c.id} style={box} data-testid={`campaign-${c.id}`}>
      <h3>{c.brief.title}</h3><p>Campaign #{c.id} · {c.stage_status} · {c.stage_message}</p>
      <p><strong>Short:</strong> {c.brief.short_caption}</p>{c.brief.reel_caption && <p><strong>Reel:</strong> {c.brief.reel_caption}</p>}<p><strong>Carousel:</strong> {c.brief.carousel_caption}</p>
      {c.brief.community_question && <p><strong>Private community draft:</strong> {c.brief.community_question}</p>}
      <button disabled={busy} onClick={() => { if (draft.dirty || draft.pending || draft.conflict) setReplacement(c); else revised(c); }}>Draft revised treatment</button>
      {replacement?.id === c.id && <div role="alert"><p>A different private treatment draft is present. Copying this campaign replaces those browser draft fields.</p><button type="button" onClick={() => revised(c)}>Replace private draft with this campaign</button><button type="button" onClick={() => setReplacement(null)}>Keep current private draft</button></div>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{c.brief.formats.map(f => <button key={f} disabled={busy || c.stage_status === "running"}
        onClick={() => act(() => api.nativeRender(c.id, f), `${f} export rendered. Watch the whole video or inspect every slide before approving.`)}>Render {f}</button>)}</div>
      {c.exports.map(exported => <ExportReview key={exported.id} item={exported} accounts={accounts} busy={busy} act={act} />)}
    </article>)}
  </div>;
}

function ExportReview({ item, accounts, busy, act }: { item: NativeExport; accounts: SocialAccount[]; busy: boolean;
  act: (fn: () => Promise<unknown>, success: string) => Promise<void> }) {
  const [targets, setTargets] = useState<string[]>([]);
  const q = item.manifest.quality;
  const manual = item.manifest.delivery === "manual_only";
  const available = accounts.filter(a => !a.id.startsWith("mk:") && (!["carousel", "image"].includes(item.format) || ["instagram", "facebook"].includes(a.id.split(":").pop() || "")));
  return <section style={{ marginTop: 20, paddingTop: 12, borderTop: "1px solid var(--border)" }} data-testid={`native-export-${item.format}`}>
    <h4>{item.format} export · {item.id.slice(0, 8)}</h4>
    <p>Final decode: {q.decode} · {q.width ? `${q.width} × ${q.height}` : "1080 × 1350 images"}{q.seconds ? ` · ${q.seconds.toFixed(2)}s` : ""}</p>
    {q.errors.map(e => <p key={e} role="alert">Blocked: {e}</p>)}{q.warnings.map(w => <p key={w}>Review: {w}</p>)}
    {item.manifest.video && <video controls preload="metadata" style={{ width: "100%", maxWidth: 300, aspectRatio: "9/16", background: "black" }} src={api.nativeFile(item.id, item.manifest.video.file)} />}
    <div style={{ display: "flex", gap: 12, overflowX: "auto", padding: "12px 0" }}>{item.manifest.cards.map((card, i) => <figure key={card.file} style={{ margin: 0, minWidth: 170, maxWidth: 220 }}>
      <img src={api.nativeFile(item.id, card.file)} alt={card.alt} style={{ width: "100%", borderRadius: 8 }} /><figcaption>Card {i + 1}: {card.alt}</figcaption>
    </figure>)}</div>
    {!!item.manifest.visual_sources?.length && <div><strong>Selected video intervals</strong><ul>{item.manifest.visual_sources.map(source => <li key={source.card}>Card {source.card} · asset #{source.asset_id} · {source.source_start.toFixed(3)}–{source.source_end.toFixed(3)}s · {source.end_policy === "hold" ? "hold last frame" : "loop selected interval"} · {source.presentation_seconds.toFixed(3)}s presentation · source audio excluded</li>)}</ul></div>}
    {!!item.manifest.audio_sources?.length && <div><strong>Selected background audio</strong><ul>{item.manifest.audio_sources.map(source => <li key={source.card}>Card {source.card} · audio asset #{source.asset_id} · {source.source_start.toFixed(3)}–{source.source_end.toFixed(3)}s · relative level {source.gain_db.toFixed(1)}dB · {source.end_policy === "loop" ? "loop if needed" : "finish, then background silence"} · faded at the beat end</li>)}</ul></div>}
    {item.manifest.platform_copy && <p><strong>Format copy:</strong> {item.manifest.platform_copy}</p>}
    {item.manifest.manual_post && <div><p>{item.manifest.manual_post.text}</p><ol>{item.manifest.manual_post.options.map(option => <li key={option}>{option}</li>)}</ol>
      <p>Private manual export. No account, queue item, scheduled upload or publication has been created.</p></div>}
    <p>Inspect every frame or slide, narration, captions, source support, rights and platform copy. A passed decode does not verify those.</p>
    <ExportAcceptance kind="native" id={item.id} label={`Approve inspected ${item.format}`} disabled={busy || !!q.errors.length}
      onApproved={() => { void act(async () => undefined, "Exact editorial acceptance saved. Delivery remains a separate action."); }} />
    {manual ? <div>{item.manifest.manual_assets?.map(file => <button key={file.file} disabled={busy || !item.approval} onClick={() => act(async () => {
      const blob = await api.nativeManualDownload(item.id, file.file);
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = file.file.split("/").pop() || "manual-post"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "Manual posting package downloaded. Nothing has been published.")}>Download {file.file.endsWith(".json") ? "JSON" : "text"} manual package</button>)}</div> : <>
      <fieldset style={{ marginTop: 12 }}><legend>Destinations for this export</legend>{available.map(a => <label key={a.id} style={{ display: "block" }}>
        <input type="checkbox" checked={targets.includes(a.id)} onChange={e => setTargets(e.target.checked ? [...targets, a.id] : targets.filter(x => x !== a.id))} /> {a.id}
      </label>)}</fieldset>
      <button disabled={busy || !item.approval || !targets.length || !!item.queue_item_id} onClick={() => act(() => api.nativeEnqueue(item.id, targets), "Added to the review queue. Posting remains a separate approval.")}>Add {item.format} to review queue</button>
    </>}
    {item.queue_item_id && <p>Queue item #{item.queue_item_id}. Open SocialPilot → Posting Queue to review and approve.</p>}
  </section>;
}
