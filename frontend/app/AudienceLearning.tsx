"use client";

import { useCallback, useEffect, useState } from "react";
import { CommentInbox } from "./CommentInbox";
import { useLocalDraft } from "../lib/localDraft";
import { LocalDraftStatus } from "./LocalDraftStatus";
import { FeedbackCard } from "./FeedbackCard";
import { api, AudienceFeedback, ExperimentSeed, LearningReport } from "@/lib/api";

const box = { border: "1px solid var(--border)", borderRadius: 12, padding: 16, background: "var(--panel)" };
const field = { display: "grid", gap: 5 };
const emptyComments = { comments: "", content: "", platform: "youtube", source: "" };
const emptyExperiment = { seed_id: "", name: "", hypothesis: "", platform: "youtube", format: "short", control: "", variant: "", metric: "engaged_views", age: "24", controls: "", account: "" };

export function AudienceLearning({ onFollowup, onEvidenceChanged, experimentSeed, onExperimentSeedCleared }: { onExperimentSeedCleared?: () => void; experimentSeed?: ExperimentSeed | null; onFollowup: (question: AudienceFeedback) => void; onEvidenceChanged?: () => void }) {
  const [report, setReport] = useState<LearningReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const commentsDraft = useLocalDraft("screen-central:comment-import", emptyComments, { restoreTemplate: true });
  const { comments, content, platform, source } = commentsDraft.value;
  const experimentTemplate = experimentSeed ? { ...emptyExperiment, seed_id: experimentSeed.decision_id,
    name: experimentSeed.name, hypothesis: experimentSeed.hypothesis, platform: experimentSeed.platform, format: experimentSeed.format,
    age: experimentSeed.age, metric: experimentSeed.metric, account: experimentSeed.account } : emptyExperiment;
  const experimentDraft = useLocalDraft("screen-central:creative-experiment", experimentTemplate, { restoreTemplate: true });
  const experiment = experimentDraft.value;
  const setExperiment = experimentDraft.change;
  const load = useCallback(async () => setReport(await api.editorialLearning()), []);
  useEffect(() => { load().catch(e => setMessage(e.message)); }, [load]);
  async function act(fn: () => Promise<unknown>, success: string) {
    setBusy(true); setMessage("");
    try {
      await fn(); setMessage(success);
      try { await load(); } catch (error: unknown) { setMessage(`${success} Refresh unavailable: ${error instanceof Error ? error.message : "Try the view again"}`); }
    }
    catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  return <div style={{ display: "grid", gap: 16 }}>
    <h2>Audience evidence & experiments</h2><p>Comments and reply drafts stay private. Classification and recurring-question groups need review.</p>
    {message && <p role="status">{message}</p>}
    <div><button disabled={busy} onClick={async () => { setBusy(true); try { await load(); setMessage("Audience evidence refreshed."); }
      catch (error: unknown) { setMessage(error instanceof Error ? error.message : "Could not refresh audience evidence"); } finally { setBusy(false); } }}>Refresh audience evidence</button></div>
    <CommentInbox onFollowup={onFollowup} onEvidenceChanged={onEvidenceChanged} />
    <form aria-label="Authorized comment import" style={{ ...box, display: "grid", gap: 10 }} onSubmit={e => {
      e.preventDefault(); if (commentsDraft.conflict || commentsDraft.pending) return;
      act(async () => { await api.editorialFeedback({ platform, content_id: content, source,
        comments: comments.split("\n").map(c => c.trim()).filter(Boolean) }); commentsDraft.saved(emptyComments); }, "Deidentified comments imported. Inspect for any remaining personal details before reusing them.");
    }}><h3>Import authorized comments</h3><p>Paste comment text only, one per line. Leave out authors, handles and contact details.</p>
      <label style={field}>Comment platform<select aria-label="Comment platform" value={platform} onChange={e => commentsDraft.change({ ...commentsDraft.value, platform: e.target.value })}>{["youtube", "instagram", "facebook"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Source content ID<input required maxLength={255} value={content} onChange={e => commentsDraft.change({ ...commentsDraft.value, content: e.target.value })} /></label>
      <label style={field}>Comment export source<input required minLength={5} maxLength={1000} value={source} onChange={e => commentsDraft.change({ ...commentsDraft.value, source: e.target.value })} /></label>
      <label style={field}>Comment texts<textarea aria-label="Comment texts" required rows={5} maxLength={200000} value={comments} onChange={e => commentsDraft.change({ ...commentsDraft.value, comments: e.target.value })} /></label>
      <LocalDraftStatus draft={commentsDraft} noun="comment import template" destination="private feedback" />
      <div><button disabled={busy || commentsDraft.conflict || !!commentsDraft.pending}>Import private feedback</button></div>
    </form>
    {report?.feedback.map(row => <FeedbackCard key={row.feedback_identity} row={row} busy={busy} onChanged={load} onFollowup={onFollowup} onEvidenceChanged={onEvidenceChanged} />)}
    {!!report?.question_groups.length && <section style={box}><h3>Recurring question candidates</h3>{report.question_groups.map(g => <p key={g.topic_token}>{g.topic_token}: {g.n} imported comments. {g.suggestion}. Manual review required.</p>)}</section>}
    <form aria-label="Creative experiment" style={{ ...box, display: "grid", gap: 10 }} onSubmit={e => {
      e.preventDefault(); if (experimentDraft.conflict || experimentDraft.pending) return;
      act(async () => { await api.editorialExperiment({ name: experiment.name, hypothesis: experiment.hypothesis, platform: experiment.platform,
        format: experiment.format, control_ids: experiment.control.split(",").map(s => s.trim()).filter(Boolean),
        variant_ids: experiment.variant.split(",").map(s => s.trim()).filter(Boolean), metric: experiment.metric,
        age_hours: Number(experiment.age), minimum_per_group: 5, controls: experiment.controls, account_id: experiment.account || null, learning_decision_id: experiment.seed_id || null }); experimentDraft.saved(emptyExperiment); onExperimentSeedCleared?.(); }, "Experiment registered. At least five comparable observations per group are needed for a descriptive comparison.");
    }}><h3>Register a creative experiment</h3>{experiment.seed_id && <p>Based on a private learning decision. Choose distinct control/variant content and document confounders before registration.</p>}
      <label style={field}>Experiment name<input required minLength={5} value={experiment.name} onChange={e => setExperiment({ ...experiment, name: e.target.value })} /></label>
      <label style={field}>Hypothesis<textarea aria-label="Hypothesis" required minLength={15} value={experiment.hypothesis} onChange={e => setExperiment({ ...experiment, hypothesis: e.target.value })} /></label>
      <label style={field}>Experiment platform<select aria-label="Experiment platform" disabled={!!experiment.seed_id} value={experiment.platform} onChange={e => setExperiment({ ...experiment, platform: e.target.value })}>{["youtube", "instagram", "facebook"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Experiment account/channel (blank for unlinked owner imports)<input disabled={!!experiment.seed_id} maxLength={128} value={experiment.account} onChange={e => setExperiment({ ...experiment, account: e.target.value })} /></label>
      <label style={field}>Experiment format<select aria-label="Experiment format" disabled={!!experiment.seed_id} value={experiment.format} onChange={e => setExperiment({ ...experiment, format: e.target.value })}>{["short", "reel", "longform", "carousel", "image", "community", "poll"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Control content IDs (comma separated)<input required value={experiment.control} onChange={e => setExperiment({ ...experiment, control: e.target.value })} /></label>
      <label style={field}>Variant content IDs (comma separated)<input required value={experiment.variant} onChange={e => setExperiment({ ...experiment, variant: e.target.value })} /></label>
      <label style={field}>Success measure<select aria-label="Success measure" value={experiment.metric} onChange={e => setExperiment({ ...experiment, metric: e.target.value })}>{["engaged_views", "views", "retention_percent", "average_view_seconds", "average_view_percent", "impressions", "click_through_percent"].map(p => <option key={p}>{p}</option>)}</select></label>
      <label style={field}>Publication age<select aria-label="Publication age" disabled={!!experiment.seed_id} value={experiment.age} onChange={e => setExperiment({ ...experiment, age: e.target.value })}>{[24, 72, 168, 720].map(h => <option key={h} value={h}>{h} hours</option>)}</select></label>
      <label style={field}>Controls and known confounders<textarea aria-label="Controls and known confounders" required minLength={10} value={experiment.controls} onChange={e => setExperiment({ ...experiment, controls: e.target.value })} /></label>
      <LocalDraftStatus draft={experimentDraft} noun="experiment template" destination="private experiment" />
      <div><button disabled={busy || experimentDraft.conflict || !!experimentDraft.pending}>Register experiment</button></div>
    </form>
    {report?.experiments.map(x => <article key={x.id} style={box}><h3>{x.name}</h3><p>{x.hypothesis}</p>
      <p>{x.metric} at {x.age_hours}h · {x.result.design.replaceAll("_", " ")} · {x.result.status.replaceAll("_", " ")}</p>
      <p>Control: n={x.result.groups.control.n}, median {x.result.groups.control.median ?? "unavailable"} · Variant: n={x.result.groups.variant.n}, median {x.result.groups.variant.median ?? "unavailable"}</p>
      <p>{x.result.relative_median_change_percent === null ? "No comparable change estimate yet." : `Relative median difference: ${x.result.relative_median_change_percent}%.`}</p>
      <p>{x.result.next_action}. No causal conclusion or automatic winner.</p>
    </article>)}
  </div>;
}
