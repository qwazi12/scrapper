"use client";

export function LocalDraftStatus({ draft, noun = "script", destination = "project" }: { noun?: string; destination?: string; draft: {
  dirty: boolean; pending: unknown; conflict: boolean; storageError: boolean;
  restore: () => void; discard: () => void; keepAfterReview: () => void; server: Record<string, string>;
} }) {
  if (!draft.dirty && !draft.pending) return null;
  return <div style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, margin: "10px 0" }}>
    {draft.pending ? <>
      <p role="status">A recovered unsaved draft is available on this browser.</p>
      <button type="button" onClick={draft.restore}>Restore unsaved draft</button>{" "}
      <button type="button" onClick={draft.discard}>Discard local draft</button>
    </> : draft.dirty && <p role="status">{draft.storageError ? "Unsaved changes. Save before leaving this page." : `Unsaved draft stored on this browser. Save to update the ${destination}.`}</p>}
    {draft.dirty && !draft.pending && <button type="button" onClick={draft.discard}>Discard unsaved changes</button>}
    {draft.conflict && <div role="alert">
      <p>{`The saved ${noun} also changed. Compare it with your draft before choosing which version to use.`}</p>
      <details><summary>Current saved {noun}</summary>
        {Object.entries(draft.server).map(([name, value]) => <div key={name}><strong>{name}</strong><pre style={{ whiteSpace: "pre-wrap" }}>{value}</pre></div>)}
      </details>
      <button type="button" onClick={draft.keepAfterReview}>Keep my draft after review</button>{" "}
      <button type="button" onClick={draft.discard}>Use saved version</button>
    </div>}
    {draft.storageError && <p role="alert">Browser draft storage is unavailable. Keep this page open until you save.</p>}
  </div>;
}
