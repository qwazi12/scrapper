"use client";

import { useCallback, useEffect, useState } from "react";
import { CommentInbox } from "./CommentInbox";
import { FeedbackCard } from "./FeedbackCard";
import { api, AudienceFeedback, ExperimentSeed, LearningReport } from "@/lib/api";

const box = { border: "1px solid var(--border)", borderRadius: 12, padding: 16, background: "var(--panel)" };
const field = { display: "grid", gap: 5 };

export function AudienceLearning({ onFollowup, onEvidenceChanged, experimentSeed }: { experimentSeed?: ExperimentSeed | null; onFollowup: (question: AudienceFeedback) => void; onEvidenceChanged?: () => void }) {
  const [report, setReport] = useState<LearningReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [comments, setComments] = useState("");
  const [content, setContent] = useState("");
  const [platform, setPlatform] = useState("youtube");
  const [source, setSource] = useState("");
  const [experiment, setExperiment] = useState({ name: "", hypothesis: "", platform: "youtube", format: "short", control: "", variant: "", metric: "engaged_views", age: "24", controls: "", account: "" });
  useEffect(() => { if (experimentSeed) setExperiment(previous => ({ ...previous, ...experimentSeed,
    control: "", variant: "", controls: "", metric: experimentSeed.metric })); }, [experimentSeed]);
  const load = useCallback(async () => setReport(await api.editorialLearning()), []);
  useEffect(() => { load().catch(e => setMessage(e.message)); }, [load]);
  async function act(fn: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage("");
    try { await fn(); await load(); setMessage(success); }
    catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <div style={{ display: "grid", gap: 16 }}>
    <h2>Audience evidence & experiments</h2><p>Comments and reply drafts stay private. Classification and recurring-question groups need review.</p>
    {message && <p role="status">{message}</p>}
    <CommentInbox onFollowup={onFollowup} onEvidenceChanged={onEvidenceChanged} />
    <form style={{ ...box, display: "grid", gap: 10 }} onSubmit={e => {
      e.preventDefault(); act(() => api.editorialFeedback({ platform, content_id: content, source,
        comments: comments.split("\n").map(c => c.trim()).filter(Boolean) }), "Deidentified comments imported. Inspect for any remaining personal details before reusing them.");
    }}><h3>Import authorized comments</h3><p>Paste comment text only, one per line. Leave out authors, handles and contact details.</p>
      <label style={field}>Comment platform<select aria-label="Comment platform" value={platform} onChange={e => setPlatform(e.target.value)}>{["youtube", "instagram", "facebook"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Source content ID<input required maxLength={255} value={content} onChange={e => setContent(e.target.value)} /></label>
      <label style={field}>Comment export source<input required minLength={5} maxLength={1000} value={source} onChange={e => setSource(e.target.value)} /></label>
      <label style={field}>Comment texts<textarea required rows={5} maxLength={200000} value={comments} onChange={e => setComments(e.target.value)} /></label>
      <div><button disabled={busy}>Import private feedback</button></div>
    </form>
    {report?.feedback.map(row => <FeedbackCard key={row.feedback_identity} row={row} busy={busy} onChanged={load} onFollowup={onFollowup} onEvidenceChanged={onEvidenceChanged} />)}
    {!!report?.question_groups.length && <section style={box}><h3>Recurring question candidates</h3>{report.question_groups.map(g => <p key={g.topic_token}>{g.topic_token}: {g.n} imported comments. {g.suggestion}. Manual review required.</p>)}</section>}
    <form style={{ ...box, display: "grid", gap: 10 }} onSubmit={e => {
      e.preventDefault(); act(() => api.editorialExperiment({ name: experiment.name, hypothesis: experiment.hypothesis, platform: experiment.platform,
        format: experiment.format, control_ids: experiment.control.split(",").map(s => s.trim()).filter(Boolean),
        variant_ids: experiment.variant.split(",").map(s => s.trim()).filter(Boolean), metric: experiment.metric,
        age_hours: Number(experiment.age), minimum_per_group: 5, controls: experiment.controls, account_id: experiment.account || null, learning_decision_id: experimentSeed?.decision_id || null }), "Experiment registered. At least five comparable observations per group are needed for a descriptive comparison.");
    }}><h3>Register a creative experiment</h3>{experimentSeed && <p>Based on a private learning decision. Choose distinct control/variant content and document confounders before registration.</p>}
      <label style={field}>Experiment name<input required minLength={5} value={experiment.name} onChange={e => setExperiment({ ...experiment, name: e.target.value })} /></label>
      <label style={field}>Hypothesis<textarea required minLength={15} value={experiment.hypothesis} onChange={e => setExperiment({ ...experiment, hypothesis: e.target.value })} /></label>
      <label style={field}>Experiment platform<select aria-label="Experiment platform" disabled={!!experimentSeed} value={experiment.platform} onChange={e => setExperiment({ ...experiment, platform: e.target.value })}>{["youtube", "instagram", "facebook"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Experiment account/channel (blank for unlinked owner imports)<input disabled={!!experimentSeed} maxLength={128} value={experiment.account} onChange={e => setExperiment({ ...experiment, account: e.target.value })} /></label>
      <label style={field}>Experiment format<select aria-label="Experiment format" disabled={!!experimentSeed} value={experiment.format} onChange={e => setExperiment({ ...experiment, format: e.target.value })}>{["short", "reel", "longform", "carousel", "image", "community", "poll"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Control content IDs (comma separated)<input required value={experiment.control} onChange={e => setExperiment({ ...experiment, control: e.target.value })} /></label>
      <label style={field}>Variant content IDs (comma separated)<input required value={experiment.variant} onChange={e => setExperiment({ ...experiment, variant: e.target.value })} /></label>
      <label style={field}>Success measure<select aria-label="Success measure" value={experiment.metric} onChange={e => setExperiment({ ...experiment, metric: e.target.value })}>{["engaged_views", "views", "retention_percent", "average_view_seconds", "average_view_percent", "impressions", "click_through_percent"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Publication age<select aria-label="Publication age" disabled={!!experimentSeed} value={experiment.age} onChange={e => setExperiment({ ...experiment, age: e.target.value })}>{[24, 72, 168, 720].map(h => <option key={h} value={h}>{h} hours</option>)}</select></label>
      <label style={field}>Controls and known confounders<textarea required minLength={10} value={experiment.controls} onChange={e => setExperiment({ ...experiment, controls: e.target.value })} /></label>
      <div><button disabled={busy}>Register experiment</button></div>
    </form>
    {report?.experiments.map(x => <article key={x.id} style={box}><h3>{x.name}</h3><p>{x.hypothesis}</p>
      <p>{x.metric} at {x.age_hours}h · {x.result.design.replaceAll("_", " ")} · {x.result.status.replaceAll("_", " ")}</p>
      <p>Control: n={x.result.groups.control.n}, median {x.result.groups.control.median ?? "unavailable"} · Variant: n={x.result.groups.variant.n}, median {x.result.groups.variant.median ?? "unavailable"}</p>
      <p>{x.result.relative_median_change_percent === null ? "No comparable change estimate yet." : `Relative median difference: ${x.result.relative_median_change_percent}%.`}</p>
      <p>{x.result.next_action}. No causal conclusion or automatic winner.</p>
    </article>)}
  </div>;
}
