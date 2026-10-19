"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ContentBinding, ExperimentSeed, EditorialInsights, EditorialOpportunity } from "@/lib/api";
import { allowLocalNavigation } from "../lib/navigationGuard";
import { askConfirm } from "@/lib/dialogs";
import { ContentLineage } from "./ContentLineage";
import { AssetLibrary } from "./AssetLibrary";
import { NativeCampaigns } from "./NativeCampaigns";
import { PerformanceDiagnostics } from "./PerformanceDiagnostics";
import { AudienceLearning } from "./AudienceLearning";

const formats = ["longform", "short", "reel", "carousel", "image", "community", "poll"];
const panel = { background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 12, padding: 18 };
const field = { display: "grid", gap: 5 };
const empty = { title: "", angle: "", audience: "", formats: ["short", "carousel"], url: "", excerpt: "", planned_for: "", feedback_refs: [] as number[] };

export function EditorialWorkspace() {
  const [rows, setRows] = useState<EditorialOpportunity[]>([]);
  const [insights, setInsights] = useState<EditorialInsights | null>(null);
  const [form, setForm] = useState(empty);
  const [editing, setEditing] = useState<number | undefined>();
  const [tab, setTab] = useState("plan");
  const [experimentSeed, setExperimentSeed] = useState<ExperimentSeed | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const load = useCallback(async () => {
    const [opportunities, report] = await Promise.all([api.editorialOpportunities(), api.editorialInsights()]);
    setRows(opportunities); setInsights(report);
  }, []);
  useEffect(() => { load().catch(e => setMessage(e.message)); }, [load]);

  async function action(fn: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage("");
    try { await fn(); await load(); setMessage(success); }
    catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const previous = rows.find(row => row.id === editing);
    await action(async () => {
      await api.editorialSave({ title: form.title, angle: form.angle, audience: form.audience, formats: form.formats,
        feedback_refs: form.feedback_refs,
        evidence: form.url && form.excerpt ? [{ url: form.url, excerpt: form.excerpt, entity: form.title,
          retrieved_at: previous?.evidence[0]?.url === form.url && previous?.evidence[0]?.excerpt === form.excerpt
            ? previous.evidence[0].retrieved_at : new Date().toISOString() }, ...(previous?.evidence.slice(1) || [])] : [],
        planned_for: form.planned_for ? new Date(form.planned_for).toISOString() : null,
        demand: previous?.demand ?? null, demand_source: previous?.demand_source ?? null,
        competition: previous?.competition || "Unknown", feasibility: previous?.feasibility || "Needs asset and narration review" }, editing);
      setForm(empty); setEditing(undefined);
    }, "Opportunity saved. Edits require a fresh research review.");
  }
  function edit(row: EditorialOpportunity) {
    setEditing(row.id);
    setForm({ title: row.title, angle: row.angle, audience: row.audience, formats: row.formats,
      feedback_refs: row.feedback_refs || [],
      url: row.evidence[0]?.url || "", excerpt: row.evidence[0]?.excerpt || "",
      planned_for: row.planned_for ? new Date(new Date(row.planned_for).getTime() - new Date(row.planned_for).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "" });
    setTab("plan");
  }
  return <div style={{ display: "grid", gap: 16 }}>
    <div><h1>Editorial desk</h1><p>Choose an original angle, review the research, and plan suitable formats. Calendar entries are drafts.</p></div>
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <button aria-pressed={tab === "plan"} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setTab("plan"); })}>Opportunities & calendar</button>
      <button aria-pressed={tab === "performance"} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setTab("performance"); })}>Performance evidence</button>
      <button aria-pressed={tab === "campaigns"} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setTab("campaigns"); })}>Native campaigns</button>
      <button aria-pressed={tab === "assets"} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setTab("assets"); })}>Asset library</button>
      <button aria-pressed={tab === "learning"} onClick={() => void allowLocalNavigation().then(allowed => { if (allowed) setTab("learning"); })}>Audience & experiments</button>
    </div>
    {message && <p role="status">{message}</p>}
    {tab === "assets" && <AssetLibrary />}
    {tab === "campaigns" && <NativeCampaigns opportunities={rows} />}
    {tab === "learning" && <AudienceLearning experimentSeed={experimentSeed} onEvidenceChanged={() => void load()} onFollowup={question => { void allowLocalNavigation().then(allowed => { if (!allowed) return;
      setEditing(undefined); setForm({ ...empty, title: question.text.slice(0, 255), angle: "Research a sourced response to this audience question; choose an original explanation.",
        audience: "Viewers asking: " + question.text.slice(0, 500), feedback_refs: [question.id] }); setTab("plan"); });
    }} />}
    {tab === "plan" && <>
      <form onSubmit={save} style={{ ...panel, display: "grid", gap: 12 }}>
        <h2>{editing ? "Edit opportunity" : "New opportunity"}</h2>
        <label style={field}>Topic<input required minLength={3} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></label>
        <label style={field}>Original angle<textarea required minLength={15} value={form.angle} onChange={e => setForm({ ...form, angle: e.target.value })} /></label>
        <label style={field}>Audience and question<input required minLength={5} value={form.audience} onChange={e => setForm({ ...form, audience: e.target.value })} /></label>
        <fieldset><legend>Suitable formats</legend><div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
          {formats.map(f => <label key={f}><input type="checkbox" checked={form.formats.includes(f)} onChange={e => setForm({ ...form,
            formats: e.target.checked ? [...form.formats, f] : form.formats.filter(x => x !== f) })} /> {f}</label>)}
        </div></fieldset>
        <label style={field}>Research source URL<input type="url" value={form.url} onChange={e => setForm({ ...form, url: e.target.value })} /></label>
        <label style={field}>Supporting excerpt<textarea minLength={20} value={form.excerpt} onChange={e => setForm({ ...form, excerpt: e.target.value })} /></label>
        <label style={field}>Planned time (your local timezone)<input type="datetime-local" value={form.planned_for} onChange={e => setForm({ ...form, planned_for: e.target.value })} /></label>
        <div><button disabled={busy || !form.formats.length}>{editing ? "Save opportunity" : "Create opportunity"}</button>
          {editing && <button type="button" onClick={() => { setForm(empty); setEditing(undefined); }}>Cancel edit</button>}</div>
      </form>
      {rows.map(row => <article key={row.id} style={panel}>
        <h2>{row.title}</h2><p>{row.angle}</p><p>For {row.audience} · {row.formats.join(", ")} · {row.status}</p>
        <p>{row.planned_for ? `Planned: ${new Date(row.planned_for).toLocaleString()}` : "No calendar time selected"}</p>
        <p>Demand: {row.demand === null ? "Unavailable" : `${row.demand} (${row.demand_source})`} · Selection: manual review</p>
        {row.evidence.map((source, i) => <p key={i}><a href={source.url} target="_blank" rel="noreferrer">Research source {i + 1}</a>: {source.excerpt}</p>)}
        <p style={{ color: "var(--muted)" }}>Supplied excerpts need owner review; source recognition does not verify a claim.</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button onClick={() => edit(row)} disabled={busy}>Edit & calendar</button>
          <button disabled={busy || !row.evidence.length || row.status === "approved"} onClick={async () => {
            if (await askConfirm("Have you reviewed these research excerpts and the original angle? This approves a campaign brief; each export needs its own inspection."))
              action(() => api.editorialApprove(row.id), "Research brief approved for campaign drafts.");
          }}>Approve research brief</button>
        </div>
      </article>)}
      {!rows.length && <p>No opportunities yet. Add research and an original angle above.</p>}
    </>}
    {tab === "performance" && insights && <>
      <ContentLineage onChanged={() => void load().catch(e => setMessage(e.message))} />
      {insights.adapters.map(a => <div style={panel} key={a.platform}><strong>{a.platform}: {a.status}</strong><p>{a.reason}</p>
        <p>{a.imported_observations} imported observations · Live metrics: unavailable</p></div>)}
      <PerformanceDiagnostics revision={insights.observation_version} onExperiment={seed => { void allowLocalNavigation().then(allowed => { if (allowed) { setExperimentSeed(seed); setTab("learning"); } }); }} />
      <ObservationForm busy={busy} onSave={data => action(() => api.editorialObservation(data), "Observation imported. Comparisons keep format and publication age separate.")} />
      <div style={{ ...panel, overflowX: "auto" }}><h2>Publication-age cohorts</h2><p>One observation per content item near 24h, 72h, 7d or 30d. Missing measures stay unavailable.</p>
        <table><thead><tr>{["Platform / format", "Age", "Items", "Median views", "Engaged views", "Confidence"].map(h => <th key={h}>{h}</th>)}</tr></thead>
          <tbody>{insights.cohorts.map(c => <tr key={`${c.platform}/${c.account_id}/${c.format}/${c.age_hours}`}>
            <td>{c.platform} / {c.format}<div style={{ fontSize: 11 }}>{c.account_id === "unspecified_owner_import" ? "Owner import (account unspecified)" : c.account_id}</div></td><td>{c.age_hours}h</td><td>{c.n}</td><td>{c.medians.views ?? "Unavailable"}</td>
            <td>{c.medians.engaged_views ?? "Unavailable"}</td><td>{c.confidence.replaceAll("_", " ")}</td>
          </tr>)}</tbody></table>
        {!insights.cohorts.length && <p>No comparable observation windows yet.</p>}<p>These are observational comparisons. They do not establish causation or predict growth.</p>
      </div>
    </>}
  </div>;
}

function ObservationForm({ busy, onSave }: { busy: boolean; onSave: (data: unknown) => void }) {
  const [bindings, setBindings] = useState<ContentBinding[]>([]);
  const [mapping, setMapping] = useState("");
  useEffect(() => { api.contentLineages().then(view => setBindings(view.bindings)).catch(() => {}); }, []);
  const [form, setForm] = useState({ platform: "youtube", format: "short", content_id: "", published: "", observed: "", views: "", engaged: "", source: "" });
  return <form style={{ ...panel, display: "grid", gap: 10 }} onSubmit={e => {
    e.preventDefault(); onSave({ platform: form.platform, format: form.format, content_id: form.content_id,
      published_at: bindings.find(row => row.id === mapping)?.data.decision.published_at || new Date(form.published).toISOString(), observed_at: new Date(form.observed).toISOString(),
      metrics: { views: form.views === "" ? null : Number(form.views), engaged_views: form.engaged === "" ? null : Number(form.engaged) }, source: form.source, lineage_id: mapping || null });
  }}><h2>Import an authorized observation</h2>
    <p>Enter a cumulative measurement from an export you are permitted to use. Importing does not connect an account.</p>
    <label style={field}>Observation content mapping<select aria-label="Observation content mapping" value={mapping} onFocus={() => api.contentLineages().then(view => setBindings(view.bindings)).catch(() => {})} onChange={e => {
      setMapping(e.target.value); const row = bindings.find(value => value.id === e.target.value);
      if (row) { const stamp = new Date(row.data.decision.published_at); setForm({ ...form, platform: row.platform, content_id: row.content_id,
        format: row.data.decision.format, published: new Date(stamp.getTime() - stamp.getTimezoneOffset() * 60000).toISOString().slice(0, 16) }); }
    }}><option value="">Unlinked authorized owner export</option>{bindings.map(row => <option key={row.id} value={row.id}>{row.account_id} · {row.content_id} · revision {row.revision}</option>)}</select></label>
    <label style={field}>Platform<select disabled={!!mapping} value={form.platform} onChange={e => setForm({ ...form, platform: e.target.value })}>{["youtube", "instagram", "facebook"].map(p => <option key={p}>{p}</option>)}</select></label>
    <label style={field}>Content format<select disabled={!!mapping} value={form.format} onChange={e => setForm({ ...form, format: e.target.value })}>{formats.map(f => <option key={f}>{f}</option>)}</select></label>
    <label style={field}>Content ID<input disabled={!!mapping} required value={form.content_id} onChange={e => setForm({ ...form, content_id: e.target.value })} /></label>
    <label style={field}>Published at<input disabled={!!mapping} required type="datetime-local" value={form.published} onChange={e => setForm({ ...form, published: e.target.value })} /></label>
    <label style={field}>Measured at<input required type="datetime-local" value={form.observed} onChange={e => setForm({ ...form, observed: e.target.value })} /></label>
    <label style={field}>Views (leave missing measures blank)<input type="number" min={0} value={form.views} onChange={e => setForm({ ...form, views: e.target.value })} /></label>
    <label style={field}>Engaged views<input type="number" min={0} value={form.engaged} onChange={e => setForm({ ...form, engaged: e.target.value })} /></label>
    <label style={field}>Export source and reporting period<input required minLength={5} value={form.source} onChange={e => setForm({ ...form, source: e.target.value })} /></label>
    <div><button disabled={busy}>Import observation</button></div>
  </form>;
}
