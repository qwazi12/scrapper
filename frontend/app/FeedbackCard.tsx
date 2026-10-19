"use client";

import { useState } from "react";
import { api, AudienceFeedback, FeedbackContext } from "../lib/api";
import { useLocalDraft } from "../lib/localDraft";
import { LocalDraftStatus } from "./LocalDraftStatus";

const box = { border: "1px solid var(--border)", borderRadius: 12, padding: 16, background: "var(--panel)" };
const field = { display: "grid", gap: 5 };

export function FeedbackCard({ row, busy, onChanged, onFollowup, onEvidenceChanged }: {
  row: AudienceFeedback; busy: boolean; onChanged: () => Promise<void>; onFollowup: (row: AudienceFeedback) => void; onEvidenceChanged?: () => void;
}) {
  const draft = useLocalDraft(`screen-central:feedback-reply:${row.feedback_identity}`, { text: row.reply_draft });
  const [detail, setDetail] = useState<FeedbackContext | null>(null);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [ack, setAck] = useState<string | null>(null);
  const [coverage, setCoverage] = useState("");
  const [claim, setClaim] = useState("");
  const [proposal, setProposal] = useState({ text: "", url: "", excerpt: "", rationale: "" });
  const [proposalAck, setProposalAck] = useState<string | null>(null);
  const text = draft.value.text;
  const currentDetail = detail?.reply_version === row.reply_version;
  const partialVisible = !!detail && detail.thread_members.length < detail.thread_total;
  const limited = !!row.coverage?.requires_decision || partialVisible;
  const version = JSON.stringify([row.reply_version, text, coverage, detail?.reply_version]);
  const correctionVersion = JSON.stringify([row.reply_version, detail?.reply_version, claim, proposal]);
  const canReview = !row.context_lineage_id || currentDetail;

  async function loadContext(more = false) {
    setWorking(true); setMessage("");
    try {
      const next = await api.feedbackContext(row.id, more && detail ? Math.max(detail.thread_members.length, detail.claims.length) : 0);
      if (more && detail && detail.reply_version === next.reply_version) setDetail({ ...next,
        thread_members: [...detail.thread_members, ...next.thread_members], claims: [...detail.claims, ...next.claims] });
      else setDetail(next);
      await onChanged();
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Could not load feedback context"); }
    finally { setWorking(false); }
  }

  async function save(approved: boolean) {
    if (approved && (ack !== version || !canReview)) return;
    setWorking(true); setMessage("");
    try {
      const result = await api.editorialReply(row.id, { text, expected_version: row.reply_version,
        approved_for_manual_use: approved, reviewed: approved, context_lineage_id: row.context_lineage_id, coverage_rationale: coverage });
      draft.saved({ text: result.reply_draft }); setAck(null);
      if (detail) setDetail(await api.feedbackContext(row.id));
      await onChanged(); setMessage(approved ? "Reviewed for manual use. Nothing sent." : "Private draft saved for review.");
    } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Draft save failed"); setAck(null); }
    finally { setWorking(false); }
  }

  return <article style={box} data-testid={`feedback-${row.id}`}>
    <h3>{row.category.replaceAll("_", " ")}</h3><blockquote>{row.text}</blockquote><p>{row.privacy_note}</p>
    {row.context_title && <p>Retained context: {row.context_title}. {row.context_mapping_superseded && "Source mapping was corrected; review the current retained context."}</p>}
    {row.coverage && <p>Reported replies: {row.coverage.reported_replies ?? "unknown"} · Retained replies: {row.coverage.retained_replies} · Reply page sequence: {row.coverage.pagination_exhausted ? "exhausted at its read time" : "not exhausted"}</p>}
    <button disabled={working} onClick={() => void loadContext()}>Inspect thread &amp; correction targets</button>
    {detail && <details open><summary>Current retained context and thread</summary>
      <p>{detail.thread_members.length} of {detail.thread_total} thread entries visible · {detail.claims.length} of {detail.claim_total} retained statements loaded</p>
      {detail.thread_members.map(member => <blockquote key={member.evidence_id}>{member.reply ? "Reply" : "Top-level comment"}: {member.text}</blockquote>)}
      {(detail.thread_members.length < detail.thread_total || detail.claims.length < detail.claim_total) && <button disabled={working} onClick={() => void loadContext(true)}>Load more retained context</button>}
      {!currentDetail && <p role="alert">Context changed. Reload the thread before marking this draft reviewed.</p>}
    </details>}
    <label style={field}>Private reply draft #{row.id}<textarea aria-label={`Private reply draft #${row.id}`} minLength={5} maxLength={2000} value={text} onChange={e => draft.change({ text: e.target.value })} /></label>
    <LocalDraftStatus draft={draft} noun="reply draft" destination="private reply" />
    {limited && <label style={field}>Limited thread-context decision<textarea aria-label="Limited thread-context decision" minLength={20} maxLength={4000} value={coverage} onChange={e => setCoverage(e.target.value)} /></label>}
    <p>{row.approved_for_manual_use ? "Reviewed for manual use. Nothing sent." : "Owner review required."}</p>
    <label><input type="checkbox" checked={ack === version} disabled={!canReview || draft.conflict || !!draft.pending} onChange={e => setAck(e.target.checked ? version : null)} /> I reviewed this exact private draft and current source/thread context.</label>
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <button disabled={busy || working || text.trim().length < 5 || draft.conflict || !!draft.pending} onClick={() => void save(false)}>Save private draft</button>
      <button disabled={busy || working || text.trim().length < 5 || !canReview || ack !== version || draft.conflict || !!draft.pending || (limited && coverage.trim().length < 20)} onClick={() => void save(true)}>Mark reviewed for manual use</button>
      <button disabled={busy || working || (draft.dirty && draft.storageError)} onClick={() => onFollowup(row)}>Draft follow-up opportunity</button>
    </div>
    {message && <p role="status">{message}</p>}
    {detail && detail.claims.length > 0 && <details><summary>Propose a source correction</summary>
      <form style={{ display: "grid", gap: 8, marginTop: 10 }} onSubmit={async event => {
        event.preventDefault(); if (!currentDetail || proposalAck !== correctionVersion) return;
        setWorking(true); setMessage("");
        try {
          const result = await api.feedbackCorrection(row.id, { expected_version: row.reply_version, claim_key: claim,
            proposal: proposal.text, evidence_url: proposal.url, evidence_excerpt: proposal.excerpt, rationale: proposal.rationale,
            reviewed: true, create_followup: true });
          setProposalAck(null); setDetail(await api.feedbackContext(row.id)); await onChanged(); onEvidenceChanged?.();
          setMessage(`Private correction proposal saved for independent source review. Draft follow-up #${result.followup_opportunity_id}.`);
        } catch (e: unknown) { setMessage(e instanceof Error ? e.message : "Correction proposal failed"); }
        finally { setWorking(false); }
      }}>
        <p>Choose the exact retained statement. The proposal starts a source-review draft; it does not certify a correction.</p>
        <label style={field}>Exact retained correction target<select aria-label="Exact retained correction target" required value={claim} onChange={e => setClaim(e.target.value)}>
          <option value="">Choose a retained statement</option>{detail.claims.map(target => <option key={target.key} value={target.key}>{target.kind}: {target.text}</option>)}</select></label>
        <label style={field}>Proposed clarification<textarea aria-label="Proposed clarification" required minLength={15} maxLength={2000} value={proposal.text} onChange={e => setProposal({ ...proposal, text: e.target.value })} /></label>
        <label style={field}>Correction reference URL<input type="url" required value={proposal.url} onChange={e => setProposal({ ...proposal, url: e.target.value })} /></label>
        <label style={field}>Correction reference excerpt<textarea aria-label="Correction reference excerpt" required minLength={20} maxLength={4000} value={proposal.excerpt} onChange={e => setProposal({ ...proposal, excerpt: e.target.value })} /></label>
        <label style={field}>Correction proposal rationale<textarea aria-label="Correction proposal rationale" required minLength={20} maxLength={4000} value={proposal.rationale} onChange={e => setProposal({ ...proposal, rationale: e.target.value })} /></label>
        <label><input type="checkbox" checked={proposalAck === correctionVersion} onChange={e => setProposalAck(e.target.checked ? correctionVersion : null)} /> I reviewed this exact retained target and the unverified proposal evidence.</label>
        <button disabled={working || !currentDetail || !detail.claims.some(target => target.key === claim) || proposalAck !== correctionVersion}>Record private correction proposal</button>
      </form>
    </details>}
  </article>;
}
