"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  CountdownEntryRow,
  CountdownProject,
  CountdownTopicCandidate,
  StudioProject,
  StudioTitle,
} from "../lib/api";
import { askConfirm, notify } from "../lib/dialogs";

// Countdowns Studio (owner, 2026-10-12): Top 5 / 10 / 15 videos built the same way
// as a LongForm breakdown — a project you open, five steps, review before render,
// then its own Countdowns posting queue.

const STEPS: { key: string; label: string; hint: string }[] = [
  { key: "research", label: "1. Research", hint: "AI ranks titles for the topic" },
  { key: "titles", label: "2. Titles", hint: "Check the ranking, fix or swap titles" },
  { key: "segments", label: "3. Segments", hint: "A short breakdown of each title" },
  { key: "script", label: "4. Script", hint: "Title, intro hook, closing question" },
  { key: "render", label: "5. Render", hint: "The 8–12 min video + thumbnails" },
];

const card: React.CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: "14px 16px",
  minWidth: 0,
};
const h2: React.CSSProperties = { margin: "0 0 10px", fontSize: 14, fontWeight: 700 };
const muted: React.CSSProperties = { color: "var(--muted)", fontSize: 11 };
const small: React.CSSProperties = { fontSize: 11, padding: "3px 10px" };
const FMT_LABEL: Record<string, string> = { top5: "Top 5", top10: "Top 10", top15: "Top 15" };

const busyStatus = (c: CountdownProject) => c.stage_status === "running" || c.stage_status === "waiting";
const money = (n: number) => (n < 1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`);
const errText = (e: any) => String(e?.message || e).replace(/^\d+: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");

/** What a card needs from the owner (drives the highlight + sort), like LongForm. */
function attention(c: CountdownProject): { label: string; color: string } | null {
  if (c.review?.delete_after) return null;
  if (c.stage_status === "error") return { label: "✕ Stopped — open it to see why", color: "#ef4444" };
  if (c.stage === "research" && c.stage_status === "done")
    return c.unmatched
      ? { label: `👀 ${c.unmatched} title(s) to swap or remove`, color: "#f59e0b" }
      : { label: "👀 Check the titles, then confirm", color: "#f59e0b" };
  if (c.stage === "segments" && c.stage_status === "done") return { label: "👀 Segments made — check the script, then render", color: "#f59e0b" };
  if (c.has.render && !c.queue_item_id) return { label: "📤 Video made — not in the queue yet", color: "#3b82f6" };
  return null;
}

function stateOf(c: CountdownProject): string {
  if (c.review?.delete_after) return "archived";
  if (c.queue_item_id) return "queued";
  if (c.has.render) return "rendered";
  if (busyStatus(c)) return "making";
  return "review";
}

function StageLine({ c }: { c: CountdownProject }) {
  const step = STEPS.find((s) => s.key === c.stage);
  const color = c.stage_status === "error" ? "var(--red)" : busyStatus(c) ? "var(--yellow)" : "var(--muted)";
  return (
    <span style={{ fontSize: 10, color }}>
      {busyStatus(c) ? "⏳ " : c.stage_status === "error" ? "✕ " : c.stage_status === "stopped" ? "■ " : ""}
      {c.stage === "new" ? "starting…" : `${step?.label || c.stage} — ${c.stage_status === "waiting" ? "waiting for Studio" : c.stage_status}`}
    </span>
  );
}

function Posters({ urls, size = 34 }: { urls: string[]; size?: number }) {
  if (!urls.length) return <div style={{ width: size * 2, height: size * 1.5, background: "var(--chip)", borderRadius: 4, flexShrink: 0 }} />;
  return (
    <div style={{ display: "flex", flexShrink: 0 }}>
      {urls.slice(-3).map((u, i) => (
        <img key={u + i} src={u.replace("/original/", "/w92/")} alt=""
             style={{ width: size, height: size * 1.5, objectFit: "cover", borderRadius: 4, marginLeft: i ? -size / 3 : 0,
                      border: "1px solid var(--panel)" }} />
      ))}
    </div>
  );
}

// --- the page ----------------------------------------------------------------------------
export function CountdownsStudio() {
  const [list, setList] = useState<CountdownProject[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [err, setErr] = useState("");

  async function load() {
    try {
      setList(await api.countdowns());
      setErr("");
    } catch (e: any) {
      setErr(errText(e));
    }
  }
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (openId !== null || !list.some(busyStatus)) return;
    const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => clearInterval(t);
  }, [list, openId]);

  if (openId !== null) {
    return <CountdownView id={openId} onBack={() => { setOpenId(null); load(); }} />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <div style={{ ...card, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ margin: 0, fontSize: 18 }}>🏆 Countdowns Studio</h1>
          <div style={muted}>Top 5 / 10 / 15 videos (8–12 min): research → titles → segments → script → video → Countdowns queue</div>
        </div>
      </div>
      {err && <div style={{ ...card, color: "var(--red)", fontSize: 12 }}>{err}</div>}
      <CountdownsList list={list} onOpen={setOpenId} onReload={load} />
      <NewCountdown onCreated={(c) => setOpenId(c.id)} />
    </div>
  );
}

// --- your countdowns -------------------------------------------------------------------
function CountdownsList({ list, onOpen, onReload }: { list: CountdownProject[]; onOpen: (id: number) => void; onReload: () => void }) {
  const [filter, setFilter] = useState("all");
  const pills: [string, string][] = [
    ["all", "All"], ["review", "Needs you"], ["making", "Making"], ["rendered", "Video made"], ["queued", "In queue"], ["archived", "🗄 Archived"],
  ];
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: list.length };
    list.forEach((x) => { const k = stateOf(x); c[k] = (c[k] || 0) + 1; });
    return c;
  }, [list]);
  const shown = list
    .filter((c) => filter === "all" || stateOf(c) === filter)
    .sort((a, b) => (attention(a) ? 0 : 1) - (attention(b) ? 0 : 1));
  const needs = list.filter((c) => attention(c)).length;

  return (
    <div style={card}>
      <h2 style={h2}>Your countdowns ({list.length})</h2>
      {needs > 0 && <div style={{ fontSize: 12, fontWeight: 700, color: "#f59e0b", marginBottom: 8 }}>👀 {needs} need you</div>}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        {pills.map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k)}
                  style={{ fontSize: 11, padding: "2px 9px", background: filter === k ? "var(--chip)" : "transparent" }}>
            {label} ({counts[k] || 0})
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <div style={muted}>{list.length ? "Nothing in this filter." : "Nothing yet. Start one below."}</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 260px), 1fr))", gap: 10 }}>
          {shown.map((c) => {
            const att = attention(c);
            return (
              <div key={c.id} style={{ background: "var(--row)", borderRadius: 8, minWidth: 0, display: "flex", flexDirection: "column",
                                       border: att ? `2px solid ${att.color}` : "1px solid transparent",
                                       boxShadow: att ? `0 0 0 3px ${att.color}22` : undefined }}>
                <button onClick={() => onOpen(c.id)}
                        style={{ textAlign: "left", padding: 8, background: "transparent", border: "none", display: "flex", gap: 10, minWidth: 0 }}>
                  <Posters urls={c.posters} />
                  <span style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                    <b style={{ fontSize: 12, overflowWrap: "anywhere" }}>{c.title}</b>
                    <span style={muted}>{FMT_LABEL[c.fmt] || c.fmt} · {c.entry_count} titles · ~{c.est_minutes} min</span>
                    <StageLine c={c} />
                    {c.segments_total > 0 && !c.has.render && (
                      <span style={muted}>Segments {c.segments_made} of {c.segments_total}</span>
                    )}
                    {c.queue_item_id && <span style={{ ...muted, color: "var(--accent)" }}>in Countdowns queue #{c.queue_item_id} · {c.queue_status}</span>}
                    {!!c.cost_usd && <span style={muted}>spent {money(c.cost_usd)}</span>}
                  </span>
                </button>
                {att && <div style={{ margin: "0 8px 4px", fontSize: 10, fontWeight: 700, color: att.color }}>{att.label}</div>}
                <div style={{ display: "flex", gap: 4, padding: "0 8px 8px", justifyContent: "flex-end", flexWrap: "wrap" }}>
                  {c.review?.delete_after && <span style={{ ...muted, color: "#f59e0b", marginRight: "auto" }}>🗄 deletes {new Date(c.review.delete_after).toLocaleDateString()}</span>}
                  <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={c.stage_status === "running"}
                          title={c.review?.delete_after ? "Keep it" : "Delete automatically in 5 days"}
                          onClick={async () => {
                            try { await api.countdownArchive(c.id, !c.review?.delete_after); onReload(); } catch (e: any) { notify(errText(e)); }
                          }}>
                    {c.review?.delete_after ? "↩ Keep" : "🗄 Archive"}
                  </button>
                  <button className="danger" style={{ fontSize: 10, padding: "1px 6px" }} title="Delete now"
                          onClick={async () => { if (await deleteCountdown(c)) onReload(); }}>🗑</button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

async function deleteCountdown(c: CountdownProject): Promise<boolean> {
  if (!(await askConfirm(`Delete "${c.title}" now?\n\nRemoves the countdown, its video, and the segments made only for it. ` +
    `Breakdowns from LongForm Studio that it reused are kept. If it's waiting to post, it leaves the queue too.`))) return false;
  try { await api.countdownDelete(c.id); return true; } catch (e: any) { notify(`Could not delete: ${errText(e)}`); return false; }
}

// --- start a new countdown --------------------------------------------------------------
function NewCountdown({ onCreated }: { onCreated: (c: CountdownProject) => void }) {
  const [tab, setTab] = useState<"topic" | "ideas" | "breakdowns">("topic");
  const [topic, setTopic] = useState("");
  const [fmt, setFmt] = useState("top10");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ideas, setIdeas] = useState<CountdownTopicCandidate[] | null>(null);
  const [mine, setMine] = useState<StudioProject[] | null>(null);
  const [picked, setPicked] = useState<number[]>([]);

  useEffect(() => {
    if (tab === "ideas" && !ideas) api.countdownTopics().then(setIdeas).catch((e) => setErr(errText(e)));
    if (tab === "breakdowns" && !mine) api.studioProjects().then((ps) => setMine(ps.filter((p) => p.has?.render))).catch((e) => setErr(errText(e)));
  }, [tab]);

  async function create() {
    if (!topic.trim()) return setErr("Type a topic, e.g. “Top 10 horror movies on Netflix”.");
    setBusy(true); setErr("");
    try { onCreated(await api.countdownCreate({ topic: topic.trim(), fmt, notes })); }
    catch (e: any) { setErr(errText(e)); }
    finally { setBusy(false); }
  }

  async function fromBreakdowns() {
    if (picked.length < 2) return setErr("Pick at least 2 breakdowns.");
    if (!topic.trim()) return setErr("Type a title for the countdown first.");
    setBusy(true); setErr("");
    try { onCreated(await api.countdownFromBreakdowns({ topic: topic.trim(), project_ids: picked })); }
    catch (e: any) { setErr(errText(e)); }
    finally { setBusy(false); }
  }

  return (
    <div style={card}>
      <h2 style={h2}>Start a new countdown</h2>
      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        {([["topic", "Your topic"], ["ideas", "💡 Topic ideas"], ["breakdowns", "From your breakdowns"]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{ ...small, background: tab === k ? "var(--chip)" : "transparent" }}>{label}</button>
        ))}
      </div>

      {tab !== "breakdowns" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. Top 10 horror movies on Netflix right now" />
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            {(["top5", "top10", "top15"] as const).map((f) => (
              <button key={f} onClick={() => setFmt(f)} style={{ ...small, background: fmt === f ? "var(--chip)" : "transparent" }}>{FMT_LABEL[f]}</button>
            ))}
            <button className="primary" style={{ fontSize: 12 }} disabled={busy} onClick={create}>
              {busy ? "Starting…" : "Research & rank"}
            </button>
          </div>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional: extra instructions (e.g. only films from the 2010s)" />
          <div style={muted}>Ranks real movies and shows only — each needs a trailer. Research is one paid AI call; you check the titles before anything else is made.</div>
        </div>
      )}

      {tab === "ideas" && (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
            <span style={muted}>Scored by demand, timing and debate. Kept for 3 days.</span>
            <button style={small} onClick={() => { setIdeas(null); api.countdownTopics(undefined, true).then(setIdeas).catch((e) => setErr(errText(e))); }}>↻ Refresh</button>
          </div>
          {!ideas ? <div style={muted}>Loading ideas…</div> : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 240px), 1fr))", gap: 8 }}>
              {ideas.map((t) => (
                <div key={t.id} style={{ background: "var(--row)", borderRadius: 8, padding: 8, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
                  <b style={{ fontSize: 12, overflowWrap: "anywhere" }}>{t.topic}</b>
                  <span style={muted}>{t.trigger_name} · score {Math.round(t.total_score)}</span>
                  <span style={{ ...muted, overflowWrap: "anywhere" }}>{t.why_now}</span>
                  <button style={{ fontSize: 11, marginTop: "auto" }} onClick={() => { setTopic(t.topic); setFmt(String(t.format).startsWith("top") ? String(t.format) : "top10"); setTab("topic"); }}>
                    Use this topic
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "breakdowns" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Countdown title, e.g. Top 5 movies coming this month" />
          <div style={muted}>Tick rendered breakdowns in ranking order: the first you tick is #1. No research step; they're used as they are.</div>
          {!mine ? <div style={muted}>Loading…</div> : mine.length === 0 ? <div style={muted}>No rendered breakdowns yet.</div> : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 220px), 1fr))", gap: 6 }}>
              {mine.map((p) => {
                const i = picked.indexOf(p.id);
                return (
                  <label key={p.id} style={{ display: "flex", gap: 8, alignItems: "center", background: "var(--row)", borderRadius: 6, padding: 6, minWidth: 0,
                                             border: i >= 0 ? "1px solid var(--accent)" : "1px solid transparent" }}>
                    <input type="checkbox" checked={i >= 0}
                           onChange={() => setPicked((x) => (x.includes(p.id) ? x.filter((y) => y !== p.id) : [...x, p.id]))} />
                    <span style={{ fontSize: 12, overflowWrap: "anywhere", minWidth: 0 }}>{i >= 0 ? <b>#{i + 1} </b> : null}{p.title}</span>
                  </label>
                );
              })}
            </div>
          )}
          <div><button className="primary" style={{ fontSize: 12 }} disabled={busy || picked.length < 2} onClick={fromBreakdowns}>
            Make a Top {picked.length || "N"} from these
          </button></div>
        </div>
      )}
      {err && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 8 }}>{err}</div>}
    </div>
  );
}

// --- one countdown --------------------------------------------------------------------------
function CountdownView({ id, onBack }: { id: number; onBack: () => void }) {
  const [c, setC] = useState<CountdownProject | null>(null);
  const [err, setErr] = useState("");
  const [working, setWorking] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  async function load() {
    try { setC(await api.countdown(id)); } catch (e: any) { setErr(errText(e)); }
  }
  useEffect(() => { load(); }, [id]);
  useEffect(() => {
    if (c && busyStatus(c)) {
      timer.current = setInterval(() => { if (!document.hidden) load(); }, 4000);
      return () => { if (timer.current) clearInterval(timer.current); };
    }
  }, [c?.stage_status, c?.stage]);

  async function act(fn: () => Promise<any>, ok?: string) {
    setErr(""); setWorking(true);
    try { const r = await fn(); if (r && typeof r === "object" && "id" in r && "stage" in r) setC(r); else await load(); if (ok) notify(ok); }
    catch (e: any) { setErr(errText(e)); }
    finally { setWorking(false); }
  }

  if (!c) return <div style={card}>{err || "Loading…"}</div>;
  const running = c.stage_status === "running";
  const confirmed = c.has.titles;
  const stepDone: Record<string, boolean> = {
    research: c.has.ranking, titles: confirmed, segments: c.has.segments, script: c.has.script && c.has.segments, render: c.has.render,
  };
  const active = c.stage === "research" && c.stage_status === "done" ? "titles" : c.stage === "segments" && c.stage_status === "done" ? "script" : c.stage;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <div style={{ ...card, display: "flex", gap: 14, alignItems: "flex-start", flexWrap: "wrap" }}>
        <button onClick={onBack} style={{ fontSize: 11 }}>← All countdowns</button>
        <Posters urls={c.posters} size={40} />
        <div style={{ flex: "1 1 220px", minWidth: 0 }}>
          <h1 style={{ margin: 0, fontSize: 18, overflowWrap: "anywhere" }}>{c.title}</h1>
          <div style={muted}>{FMT_LABEL[c.fmt] || c.fmt} · Countdown #{c.id} · {c.entry_count} titles · ~{c.est_minutes} min{c.cost_usd ? ` · spent ${money(c.cost_usd)}` : ""}</div>
          <div style={{ ...muted, overflowWrap: "anywhere" }}>Topic: {c.topic}</div>
          <div style={{ marginTop: 6, fontSize: 12, color: c.stage_status === "error" ? "var(--red)" : busyStatus(c) ? "var(--yellow)" : "var(--text)" }}>
            {busyStatus(c) ? "⏳ " : c.stage_status === "error" ? "✕ " : c.stage_status === "stopped" ? "■ " : c.stage_status === "done" ? "✓ " : ""}
            {c.stage_message || "Starting…"}
          </div>
          {err && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 4 }}>{err}</div>}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {busyStatus(c) && (
            <button className="danger" style={{ fontSize: 12 }} disabled={working}
                    onClick={() => act(() => api.countdownStop(c.id), "Stopped. Finished parts are kept.")}>■ Stop</button>
          )}
          <button className="danger" style={{ fontSize: 11 }} disabled={working}
                  onClick={async () => { if (await deleteCountdown(c)) onBack(); }}>Delete</button>
        </div>
      </div>

      {/* Step stepper, like LongForm */}
      <div style={{ ...card, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {STEPS.map((s) => (
          <div key={s.key} title={s.hint}
               style={{ flex: "1 1 140px", minWidth: 0, padding: 8, borderRadius: 8,
                        border: `1px solid ${active === s.key ? "var(--accent)" : "var(--border)"}`,
                        background: stepDone[s.key] ? "rgba(16,185,129,0.08)" : "var(--row)" }}>
            <div style={{ fontSize: 12, fontWeight: 700 }}>{stepDone[s.key] ? "✓ " : ""}{s.label}</div>
            <div style={{ ...muted, minHeight: 28 }}>{s.hint}</div>
            <div style={{ display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap" }}>
              {s.key === "research" && (
                <button style={{ fontSize: 10, padding: "2px 8px" }} disabled={working || running || confirmed}
                        title={confirmed ? "Titles are confirmed — start a new countdown instead" : "Research the topic again (paid)"}
                        onClick={async () => { if (!c.has.ranking || (await askConfirm("Research again? The current ranking is replaced."))) act(() => api.countdownResearch(c.id)); }}>
                  {c.has.ranking ? "Re-run" : "Run"}
                </button>
              )}
              {s.key === "titles" && (
                <button style={{ fontSize: 10, padding: "2px 8px" }} disabled={working || running || !c.has.ranking || confirmed || c.unmatched > 0}
                        title={c.unmatched ? "Swap or remove the unmatched titles first" : "Make a segment for each title"}
                        onClick={() => act(() => api.countdownConfirm(c.id), "Titles confirmed — segments start when Studio is free.")}>
                  {confirmed ? "✓ Confirmed" : "Confirm titles"}
                </button>
              )}
              {s.key === "segments" && (
                <button style={{ fontSize: 10, padding: "2px 8px" }} disabled={working || !confirmed || c.has.segments || busyStatus(c)}
                        title="Make the segments (also retries a failed one)"
                        onClick={() => act(() => api.countdownMake(c.id))}>
                  ▶ Make segments
                </button>
              )}
              {s.key === "render" && (
                <>
                  <button style={{ fontSize: 10, padding: "2px 8px" }} disabled={working || running || !c.has.segments}
                          onClick={() => act(() => api.countdownRender(c.id), "Rendering — it shows in the jobs bar.")}>
                    {c.has.render ? "Re-render" : "Render video"}
                  </button>
                  <button className="primary" style={{ fontSize: 10, padding: "2px 8px" }} disabled={working || !c.has.render}
                          onClick={async () => {
                            if (await askConfirm("Send to the Countdowns queue as Ready to Post on Screen Central? It takes the next Countdowns post time."))
                              act(async () => { const r = await api.countdownPublish(c.id); await load(); return r; }, "✓ Sent to the Countdowns queue (Ready to Post).");
                          }}>
                    📤 Send to queue
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {c.has.render && <VideoPart c={c} onChange={setC} />}
      <TitlesPart c={c} onChange={setC} locked={confirmed} />
      {confirmed && <SegmentsPart c={c} />}
      {confirmed && <ScriptPart c={c} onChange={setC} />}
    </div>
  );
}

function Part({ title, children, open = true }: { title: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} style={card}>
      <summary style={{ cursor: "pointer", fontSize: 14, fontWeight: 700 }}>{title}</summary>
      <div style={{ marginTop: 10, minWidth: 0 }}>{children}</div>
    </details>
  );
}

function VideoPart({ c, onChange }: { c: CountdownProject; onChange: (c: CountdownProject) => void }) {
  const r = c.render!;
  const bust = `${r.rendered_at}-${r.thumb_v || 0}`;
  return (
    <Part title={`🎬 Video (${r.minutes} min)`}>
      <video controls preload="metadata" src={api.countdownFileUrl(c.id, "video", r.rendered_at)}
             poster={api.countdownFileUrl(c.id, "thumbnail", bust)}
             style={{ width: "100%", maxHeight: 420, borderRadius: 8, background: "#000" }} />
      <div style={{ ...muted, margin: "10px 0 4px" }}>Thumbnail (click to use it)</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 160px), 1fr))", gap: 8 }}>
        {Object.keys(r.thumbnails || {}).map((k) => (
          <button key={k} onClick={async () => { try { onChange(await api.countdownThumbnail(c.id, k)); } catch (e: any) { notify(errText(e)); } }}
                  style={{ padding: 0, borderRadius: 6, overflow: "hidden", border: r.selected_thumbnail === k ? "2px solid var(--accent)" : "2px solid transparent" }}>
            <img src={api.countdownFileUrl(c.id, `thumb_${k}`, r.rendered_at)} alt="" style={{ width: "100%", display: "block" }} />
          </button>
        ))}
      </div>
      <div style={{ ...muted, margin: "10px 0 4px" }}>Chapters (go into the YouTube description)</div>
      <div style={{ fontFamily: "monospace", fontSize: 11, display: "flex", flexDirection: "column", gap: 2 }}>
        {r.chapters.map((ch, i) => <span key={i} style={{ overflowWrap: "anywhere" }}>{ch.time_formatted} {ch.title}</span>)}
      </div>
    </Part>
  );
}

function TitlesPart({ c, onChange, locked }: { c: CountdownProject; onChange: (c: CountdownProject) => void; locked: boolean }) {
  const entries = [...(c.ranking?.entries || [])].sort((a, b) => a.rank - b.rank);
  const [swapAt, setSwapAt] = useState<number | null>(null);   // index in entries, or -1 = add
  const [q, setQ] = useState("");
  const [results, setResults] = useState<StudioTitle[] | null>(null);

  if (!c.has.ranking) return <Part title="2. Titles"><div style={muted}>{c.stage_status === "running" ? "Researching…" : "Run research first."}</div></Part>;

  async function save(next: CountdownEntryRow[]) {
    if (locked && !(await askConfirm("The titles are confirmed. Changing them resets the segments step — continue?"))) return;
    try { onChange(await api.countdownEntries(c.id, next)); } catch (e: any) { notify(errText(e)); }
  }
  const move = (i: number, d: number) => {
    const n = [...entries]; const j = i + d;
    if (j < 0 || j >= n.length) return;
    [n[i], n[j]] = [n[j], n[i]];
    save(n);
  };
  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    try { setResults(await api.studioSearch(q)); } catch (er: any) { notify(errText(er)); }
  }
  function pick(t: StudioTitle) {
    const row: CountdownEntryRow = { rank: 0, title: t.title, year: (t.date || "").slice(0, 4), tmdb_id: t.tmdb_id, media_type: t.media_type, poster: t.poster };
    const n = [...entries];
    if (swapAt === -1) n.push(row); else if (swapAt !== null) n[swapAt] = { ...n[swapAt], ...row };
    setSwapAt(null); setResults(null); setQ("");
    save(n);
  }

  return (
    <Part title={`2. Titles (${entries.length})${c.unmatched ? ` — ${c.unmatched} to fix` : ""}`}>
      {c.ranking?.why_now && <div style={{ ...muted, marginBottom: 8 }}>Why now: {c.ranking.why_now}</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {entries.map((e, i) => (
          <div key={`${e.rank}-${e.title}`} style={{ display: "flex", gap: 8, alignItems: "flex-start", background: "var(--row)", borderRadius: 8, padding: 8, minWidth: 0,
                                                    border: e.tmdb_id ? "1px solid transparent" : "2px solid #f59e0b" }}>
            <b style={{ fontSize: 16, width: 34, flexShrink: 0, color: "var(--accent)" }}>#{i + 1}</b>
            {e.poster ? <img src={e.poster.replace("/original/", "/w92/")} alt="" style={{ width: 38, borderRadius: 4, flexShrink: 0 }} />
                      : <div style={{ width: 38, height: 57, background: "var(--chip)", borderRadius: 4, flexShrink: 0 }} />}
            <div style={{ flex: 1, minWidth: 0 }}>
              <b style={{ fontSize: 12, overflowWrap: "anywhere" }}>{e.title}{e.year ? ` (${e.year})` : ""}</b>
              {!e.tmdb_id && <div style={{ fontSize: 11, color: "#f59e0b", fontWeight: 700 }}>⚠ Not a movie or show on TMDB — no trailer to use. Swap or remove it.</div>}
              {e.reused && <div style={{ ...muted, color: "#93c5fd" }}>Uses your LongForm breakdown as it is</div>}
              {e.why_it_ranks && <div style={{ ...muted, overflowWrap: "anywhere" }}>{e.why_it_ranks}</div>}
              {(e.key_stat || e.where_to_watch) && <div style={{ ...muted, overflowWrap: "anywhere" }}>{[e.key_stat, e.where_to_watch && `Watch: ${e.where_to_watch}`].filter(Boolean).join(" · ")}</div>}
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
              <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={i === 0} onClick={() => move(i, -1)} title="Move up (higher rank)">↑</button>
              <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={i === entries.length - 1} onClick={() => move(i, 1)} title="Move down">↓</button>
              <button style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => { setSwapAt(i); setQ(e.title); setResults(null); }} title="Swap for another title">Swap</button>
              <button className="danger" style={{ fontSize: 10, padding: "1px 6px" }} disabled={entries.length <= 2}
                      onClick={() => save(entries.filter((_, k) => k !== i))} title="Remove">✕</button>
            </div>
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 8 }}>
        <button style={small} onClick={() => { setSwapAt(-1); setQ(""); setResults(null); }}>+ Add a title</button>
      </div>
      {swapAt !== null && (
        <div style={{ marginTop: 8, background: "var(--row)", borderRadius: 8, padding: 8 }}>
          <div style={{ ...muted, marginBottom: 6 }}>{swapAt === -1 ? "Add a title (it goes last; move it up after)" : `Swap #${swapAt + 1} for:`}</div>
          <form onSubmit={search} style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a movie or TV show…" style={{ flex: "1 1 180px", minWidth: 0 }} />
            <button type="submit">Search</button>
            <button type="button" onClick={() => { setSwapAt(null); setResults(null); }}>Cancel</button>
          </form>
          {results && (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6, maxHeight: 260, overflowY: "auto" }}>
              {results.length === 0 && <span style={muted}>No matches.</span>}
              {results.slice(0, 10).map((t) => (
                <button key={`${t.media_type}-${t.tmdb_id}`} onClick={() => pick(t)}
                        style={{ display: "flex", gap: 8, alignItems: "center", textAlign: "left", fontSize: 12 }}>
                  {t.poster ? <img src={t.poster.replace("/original/", "/w92/").replace("/w342/", "/w92/")} alt="" style={{ width: 28, borderRadius: 3 }} /> : null}
                  <span style={{ overflowWrap: "anywhere" }}>{t.title} · {t.media_type === "tv" ? "TV" : "Movie"} {(t.date || "").slice(0, 4)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {c.ranking?.honorable_mentions?.length ? (
        <div style={{ ...muted, marginTop: 8 }}>Honorable mentions: {c.ranking.honorable_mentions.join(" · ")}</div>
      ) : null}
    </Part>
  );
}

function SegmentsPart({ c }: { c: CountdownProject }) {
  const segs = [...(c.segments || [])].reverse();          // #1 first, like the titles list
  return (
    <Part title={`3. Segments (${c.segments_made} of ${c.segments_total} made)`} open={!c.has.segments}>
      <div style={{ ...muted, marginBottom: 8 }}>
        Each title gets a short breakdown, made one at a time and only while Studio has no LongForm work. They use the daily AI budget,
        not the daily breakdown limit. A failed one stops the line; fix it, then ▶ Make segments.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {segs.map((x) => (
          <div key={x.id} style={{ display: "flex", gap: 8, alignItems: "center", background: "var(--row)", borderRadius: 6, padding: 6, minWidth: 0 }}>
            <b style={{ width: 34, flexShrink: 0, color: "var(--accent)" }}>#{x.rank}</b>
            <span style={{ flex: 1, minWidth: 0, fontSize: 12, overflowWrap: "anywhere" }}>{x.title}{x.reused ? " (your breakdown)" : ""}</span>
            <span style={{ fontSize: 11, flexShrink: 0, color: x.rendered ? "var(--accent)" : x.stage_status === "error" || x.stage_status === "stopped" ? "var(--red)" : x.stage_status === "running" || x.stage_status === "queued" ? "var(--yellow)" : "var(--muted)" }}
                  title={x.stage_message || ""}>
              {x.rendered ? `✓ made${x.seconds ? ` (${Math.round(x.seconds)}s)` : ""}` : x.stage === "new" ? "not started" : `${x.stage} — ${x.stage_status}`}
            </span>
          </div>
        ))}
      </div>
    </Part>
  );
}

function ScriptPart({ c, onChange }: { c: CountdownProject; onChange: (c: CountdownProject) => void }) {
  const sc = c.script || {};
  const [title, setTitle] = useState(sc.title || "");
  const [intro, setIntro] = useState(sc.intro || "");
  const [outro, setOutro] = useState(sc.outro || "");
  const [saving, setSaving] = useState(false);
  const dirty = title !== (sc.title || "") || intro !== (sc.intro || "") || outro !== (sc.outro || "");
  return (
    <Part title="4. Script" open={c.has.segments && !c.has.render}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label style={{ fontSize: 12 }}>YouTube title
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={100} style={{ width: "100%" }} />
        </label>
        {(sc.title_options || []).length > 1 && (
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
            {(sc.title_options || []).map((t) => (
              <button key={t} style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => setTitle(t)}>{t}</button>
            ))}
          </div>
        )}
        <label style={{ fontSize: 12 }}>Intro hook (voiced over a preview montage; tease #1 without naming it)
          <textarea value={intro} onChange={(e) => setIntro(e.target.value)} rows={3} style={{ width: "100%" }} />
        </label>
        <label style={{ fontSize: 12 }}>Closing question (voiced in the outro, to start the comments)
          <textarea value={outro} onChange={(e) => setOutro(e.target.value)} rows={2} style={{ width: "100%" }} />
        </label>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button className="primary" style={{ fontSize: 12 }} disabled={!dirty || saving}
                  onClick={async () => { setSaving(true); try { onChange(await api.countdownScript(c.id, { title, intro, outro })); } catch (e: any) { notify(errText(e)); } finally { setSaving(false); } }}>
            {saving ? "Saving…" : "Save script"}
          </button>
          {c.has.render && dirty && <span style={muted}>Re-render to use the new intro/outro.</span>}
        </div>
      </div>
    </Part>
  );
}
