"use client";

import { useEffect, useState } from "react";
import { api, parseApiDate, RetainedDetail } from "../lib/api";
import { askConfirm } from "../lib/dialogs";

function words(script: any): string {
  if (!script) return "No saved script.";
  return script.sentences?.map((s: any) => s.text).join("\n\n") ||
    [script.title, script.intro, script.outro].filter(Boolean).join("\n\n");
}

export function ExportHistory({ kind, projectId, revision, currentScript, onRestored }: {
  kind?: "studio" | "countdown"; projectId?: number; revision?: string;
  currentScript?: any; onRestored?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.retainedVersions>> | null>(null);
  const [selected, setSelected] = useState<RetainedDetail | null>(null);
  const [comparedVersion, setComparedVersion] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() { setData(await api.retainedVersions(kind, projectId)); }
  useEffect(() => {
    if (open) void load().catch(e => setError(String(e.message || e)));
    // Idle polling can refresh summaries without changing the explicit comparison.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, kind, projectId, revision]);

  async function act(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try { await fn(); } catch (e: any) { setError(e.message || String(e)); } finally { setBusy(false); }
  }
  const changed = comparedVersion !== data?.project_version;
  const selectedAssets = data?.versions.find(v => v.id === selected?.id)?.assets || [];

  return <details data-testid="export-history" open={open} onToggle={e => setOpen(e.currentTarget.open)}
    style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 12, margin: "10px 0", background: "var(--panel)" }}>
    <summary style={{ cursor: "pointer", fontWeight: 700 }}>Retained export history</summary>
    <p style={{ fontSize: 12 }}>Previous media and scripts stay available after edits or project deletion. Restoring words creates a draft that needs checking and a new render.</p>
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
      <button disabled={busy} onClick={() => void act(load)}>Refresh history</button>
      {kind && projectId && <button disabled={busy} onClick={() => void act(async () => {
        await api.retainedCapture(kind, projectId); await load();
      })}>Retain current export</button>}
    </div>
    {error && <p role="alert" style={{ color: "var(--red)" }}>{error}</p>}
    {data && !data.versions.length && <p>No retained exports yet. New renders are saved automatically; an older current export can be retained above.</p>}
    <ol style={{ paddingLeft: 22, maxHeight: 240, overflow: "auto" }}>
      {data?.versions.map(v => <li key={v.id} style={{ margin: "10px 0" }}>
        <strong>{v.title}</strong> · {new Date(parseApiDate(v.created_at)).toLocaleString()} · {v.approval_recorded ? "inspection approval recorded" : "no approval recorded"}
        <div style={{ fontSize: 11, color: "var(--muted)" }}>{v.kind} #{v.project_id} · original project {new Date(parseApiDate(v.project_created_at)).toLocaleString()}</div>
        <button disabled={busy} aria-label={`Inspect retained export ${v.id}`} onClick={() => void act(async () => {
          setSelected(await api.retainedDetail(v.id)); setComparedVersion(data.project_version);
        })}>Inspect saved version</button>
      </li>)}
    </ol>
    {selected && <div data-testid="retained-version-detail" style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
      <h3 style={{ fontSize: 14 }}>{selected.manifest.title} — retained version</h3>
      {selectedAssets.some(a => a.role === "video" && a.available) && <>
        <video controls preload="metadata" src={api.retainedFile(selected.id, "video")}
          style={{ width: "100%", maxHeight: 360, background: "black" }} />
        <a href={api.retainedFile(selected.id, "video")} target="_blank" rel="noreferrer">Open retained video</a>
      </>}
      {selectedAssets.some(a => a.role === "thumbnail" && a.available) && <img src={api.retainedFile(selected.id, "thumbnail")}
        alt="Retained export thumbnail" style={{ maxWidth: 280, width: "100%", display: "block", marginTop: 10 }} />}
      {selectedAssets.some(a => !a.available) && <p>Some legacy files were already missing when this version was recorded.</p>}
      <details open><summary>Retained script</summary><pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{words(selected.manifest.snapshot.inputs.script)}</pre></details>
      {kind && projectId && <>
        <details><summary>Current saved script</summary><pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit" }}>{words(currentScript)}</pre></details>
        {changed && <p role="alert">The project changed since this comparison. Inspect the saved version again before restoring.</p>}
        <button disabled={busy || changed || !comparedVersion || !selected.manifest.snapshot.inputs.script} onClick={() => void act(async () => {
          if (!(await askConfirm("Restore these saved words as a draft? The current export stays in history. Check the restored draft against current sources and make a new render before release."))) return;
          await api.retainedRestore(selected.id, comparedVersion!); setSelected(null); await load(); onRestored?.();
        })}>Restore saved script as draft</button>
      </>}
    </div>}
  </details>;
}
