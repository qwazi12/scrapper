"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  CountdownAuto,
  CountdownEntryRow,
  CountdownProject,
  CountdownTopic,
  CountdownTopics,
  StudioArchive,
  StudioProject,
  StudioStatus,
  StudioTitle,
  VidIQStatus,
} from "../lib/api";
import { askConfirm, notify } from "../lib/dialogs";
import { ProjectView } from "./StudioPanel";
import { UndoButton } from "./UndoButton";

// Countdowns Studio (owner, 2026-10-13): "exactly like LongForm, but creating videos for
// the countdowns". Same page shape as LongForm Studio: status chips, archive rule, the
// automation card, Your countdowns (review, render, send in batches), Start a new
// countdown (fresh checked & ranked topics), and a project page with steps.

const STEPS: { key: string; label: string; hint: string }[] = [
  { key: "research", label: "1. Research", hint: "AI ranks real titles for the topic" },
  { key: "titles", label: "2. Titles", hint: "Checked on TMDB; swap or re-order" },
  { key: "segments", label: "3. Segments", hint: "A short breakdown of each title" },
  { key: "script", label: "4. Script", hint: "Title, intro hook, closing question" },
  { key: "render", label: "5. Render", hint: "The 8–12 min video + thumbnails" },
];

const card: React.CSSProperties = { background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 10, padding: "14px 16px", minWidth: 0 };
const h2: React.CSSProperties = { margin: "0 0 10px", fontSize: 14, fontWeight: 700 };
const muted: React.CSSProperties = { color: "var(--muted)", fontSize: 11 };
const small: React.CSSProperties = { fontSize: 11, padding: "3px 10px" };
const tiny: React.CSSProperties = { fontSize: 10, padding: "2px 8px" };
const FMT: Record<string, string> = { top5: "Top 5", top10: "Top 10", top15: "Top 15" };
const ago = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : "never");
const money = (n: number) => (n < 1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`);
const errText = (e: any) => String(e?.message || e).replace(/^\d+: /, "").replace(/^\{"detail":"(.*)"\}$/, "$1");
const busyStatus = (c: CountdownProject) => c.stage_status === "running" || c.stage_status === "waiting";
const short = (n?: number) => (!n ? "0" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n));

/** What a card needs from the owner (highlight + sort), like LongForm's Your videos. */
function attention(c: CountdownProject): { kind: "review" | "send" | "fix"; label: string; color: string } | null {
  if (c.review?.delete_after || c.queue_item_id) return null;
  if (c.stage_status === "error") return { kind: "fix", label: "✕ Stopped — open it to see why", color: "#ef4444" };
  if (busyStatus(c)) return null;
  if (c.has.render) return { kind: "send", label: "📤 Rendered — not in the queue yet", color: "#3b82f6" };
  if (c.has.segments) return { kind: "review", label: "👀 Ready for your review — then render", color: "#f59e0b" };
  if (c.stage === "research" && c.stage_status === "done")
    return { kind: "review", label: c.unmatched ? `👀 ${c.unmatched} title(s) to swap or remove` : "👀 Check the titles, then confirm", color: "#f59e0b" };
  return null;
}

type Filter = "all" | "review" | "making" | "reviewed" | "rendered" | "queued" | "archived";
function stateOf(c: CountdownProject): Filter {
  if (c.review?.delete_after) return "archived";
  if (c.queue_item_id) return "queued";
  if (c.has.render) return "rendered";
  if (c.review?.reviewed_at) return "reviewed";
  if (busyStatus(c) || c.stage_status === "paused") return "making";
  return "review";
}

function StageLine({ c }: { c: CountdownProject }) {
  const step = STEPS.find((s) => s.key === c.stage);
  const color = c.stage_status === "error" ? "var(--red)" : busyStatus(c) ? "var(--yellow)" : c.stage_status === "paused" ? "#f59e0b" : "var(--muted)";
  const what = c.stage === "segments" && c.segments_total ? `${c.segments_made} of ${c.segments_total}` : c.stage_status === "waiting" ? "waiting" : c.stage_status;
  return (
    <span style={{ fontSize: 10, color }}>
      {busyStatus(c) ? "⏳ " : c.stage_status === "error" ? "✕ " : c.stage_status === "stopped" ? "■ " : c.stage_status === "paused" ? "⏸ " : ""}
      {c.stage === "new" ? "starting…" : `${step?.label || c.stage} — ${what}`}
    </span>
  );
}

function Posters({ urls, size = 34 }: { urls: string[]; size?: number }) {
  if (!urls.length) return <div style={{ width: size * 1.6, height: size * 1.5, background: "var(--chip)", borderRadius: 4, flexShrink: 0 }} />;
  return (
    <div style={{ display: "flex", flexShrink: 0 }}>
      {urls.slice(-3).map((u, i) => (
        <img key={u + i} src={u.replace("/original/", "/w92/").replace("/w342/", "/w92/")} alt=""
             style={{ width: size, height: size * 1.5, objectFit: "cover", borderRadius: 4, marginLeft: i ? -size / 3 : 0, border: "1px solid var(--panel)" }} />
      ))}
    </div>
  );
}

function Chips({ checks }: { checks: Record<string, { status: string; why: string }> }) {
  const label: Record<string, string> = { new: "New", titles: "Titles", demand: "Demand", timely: "Timely", debate: "Debate" };
  return (
    <div style={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
      {Object.entries(checks).map(([k, v]) => (
        <span key={k} title={v.why}
              style={{ fontSize: 9, padding: "1px 5px", borderRadius: 8,
                       background: v.status === "pass" ? "#064e3b" : v.status === "warn" ? "#422006" : "#450a0a",
                       color: v.status === "pass" ? "#34d399" : v.status === "warn" ? "#fcd34d" : "#fca5a5" }}>
          {v.status === "pass" ? "✓" : v.status === "warn" ? "⚠" : "✕"} {label[k] || k}
        </span>
      ))}
    </div>
  );
}

// --- the page ---------------------------------------------------------------------------------
export function CountdownsStudio() {
  const [list, setList] = useState<CountdownProject[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [vq, setVq] = useState<VidIQStatus | null>(null);
  const [err, setErr] = useState("");
  const [lineV, setLineV] = useState(0);

  async function load() {
    try { setList(await api.countdowns()); setErr(""); } catch (e: any) { setErr(errText(e)); }
  }
  useEffect(() => {
    load();
    api.studioStatus().then(setStatus).catch(() => {});
    api.vidiqStatus().then(setVq).catch(() => {});
  }, []);
  useEffect(() => {
    if (openId !== null || !list.some((c) => busyStatus(c) || c.stage_status === "paused")) return;
    const t = setInterval(() => { if (!document.hidden) load(); }, 8000);
    return () => clearInterval(t);
  }, [list, openId]);

  if (openId !== null) return <CountdownView id={openId} status={status} onBack={() => { setOpenId(null); load(); }} />;
  const chip = (ok: boolean | undefined, label: string, why: string) => (
    <span title={why} style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: ok ? "#064e3b" : "#450a0a", color: ok ? "#34d399" : "#fca5a5" }}>
      {ok ? "●" : "○"} {label}
    </span>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <div style={{ ...card, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ margin: 0, fontSize: 18 }}>🏆 Countdowns Studio</h1>
          <div style={muted}>Top 5 / 10 / 15 countdowns (8–12 min): research → titles → segments → script → video → Countdowns queue</div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {status && chip(status.tmdb, "TMDB", "TMDB key on the server")}
          {status && chip(status.gemini, "Gemini", "Gemini key on the server")}
          {status && chip(status.tts, "Voice", "Voice key on the server")}
          {vq && chip(vq.configured && vq.connected !== false, "vidIQ",
            vq.configured ? `vidIQ credits used today ${vq.usage.today} of ${vq.settings.daily_credits} · this month ${vq.usage.month} of ${vq.settings.monthly_credits}` : "VIDIQ_API_KEY isn't set")}
        </div>
      </div>
      <CountdownArchiveRule />
      {err && <div style={{ ...card, color: "var(--red)", fontSize: 12 }}>{err}</div>}
      <AutomationCard key={lineV} onChange={load} />
      <CountdownsList list={list} onOpen={setOpenId} onReload={load} />
      <NewCountdown onCreated={(c) => setOpenId(c.id)} onLined={() => setLineV((v) => v + 1)} />
    </div>
  );
}

function CountdownArchiveRule() {
  const [a, setA] = useState<StudioArchive | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.studioArchive().then(setA).catch(() => {}); }, []);
  if (!a) return null;
  async function save(patch: { enabled?: boolean; days?: number }) {
    setBusy(true);
    try { setA(await api.setStudioArchive(patch)); } catch (e: any) { notify(errText(e)); } finally { setBusy(false); }
  }
  return (
    <div style={{ ...card, fontSize: 12, padding: "10px 14px" }}>
      <span>📦 <b>Archive finished countdowns</b> </span>
      <label style={{ marginLeft: 6 }}>
        <input type="checkbox" checked={a.settings.enabled} disabled={busy} onChange={(e) => save({ enabled: e.target.checked })} /> on
      </label>
      <span style={muted}> · </span>
      <select style={{ width: "auto", fontSize: 11, padding: "1px 6px" }} value={a.settings.days} disabled={busy}
              onChange={(e) => save({ days: Number(e.target.value) })}>
        {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
      </select>
      <span style={muted}> after posting, once the Drive copy is confirmed. Deletes the video on the server (it stays in Drive; titles, script and thumbnails are kept). Same setting as LongForm.</span>
    </div>
  );
}

// --- 🤖 automation card (same shape as LongForm's) --------------------------------------------
function AutomationCard({ onChange }: { onChange: () => void }) {
  const [a, setA] = useState<CountdownAuto | null>(null);
  const [perDay, setPerDay] = useState("1");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const load = () => api.countdownAuto().then((d) => { setA(d); setErr(""); }).catch((e) => setErr(errText(e)));
  useEffect(() => {
    load();
    const t = setInterval(() => !document.hidden && load(), 20000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { if (a) setPerDay(String(a.settings.per_day)); }, [a?.settings.per_day]);
  async function run(fn: () => Promise<CountdownAuto>, ok?: string) {
    setSaving(true);
    try { setA(await fn()); onChange(); if (ok) notify(ok); } catch (e: any) { notify(errText(e)); } finally { setSaving(false); }
  }
  if (err) return <div style={{ ...card, color: "var(--red)", fontSize: 12 }}>Automation: {err}</div>;
  if (!a) return <div style={card}><span style={muted}>Loading automation…</span></div>;
  const paused = a.settings.paused_until && new Date(a.settings.paused_until) > new Date();
  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ ...h2, margin: 0 }}>🤖 Countdown automation</h2>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700 }}>
          <input type="checkbox" checked={a.settings.enabled} disabled={saving}
                 onChange={async (e) => {
                   const on = e.target.checked;
                   if (on && !(await askConfirm(`Turn on countdown automation?\n\nIt makes up to ${a.settings.per_day} countdown a day from the top of the checked topic list: research, title check, every segment, the script — then it stops before the final render so you can review.`))) return;
                   run(() => api.countdownAutoSet({ enabled: on }));
                 }} />
          {a.settings.enabled ? <span style={{ color: "var(--accent)" }}>ON</span> : <span style={{ color: "var(--muted)" }}>OFF</span>}
        </label>
        <span style={muted}>Today: {a.today} of {a.settings.per_day} started</span>
      </div>
      <div style={{ fontSize: 12, marginTop: 8, lineHeight: 1.7 }}>
        <div>
          <b>Next:</b>{" "}
          {!a.settings.enabled ? <span style={muted}>nothing — automation is off</span>
            : a.waiting ? <span style={{ color: "var(--yellow)" }}>waiting — {a.waiting}</span>
            : a.next ? <>{a.next.topic} <span style={muted}>({FMT[a.next.fmt]}{a.next.from_line ? ", from your line" : `, score ${a.next.score}`}, ~${a.next.estimate_usd.toFixed(2)}) at the next check (every 5 min)</span></>
            : <span style={{ color: "var(--yellow)" }}>no topic passes the checks — ↻ Refresh topics below</span>}
          {a.settings.enabled && a.waiting && a.next && <div style={muted}>Then: {a.next.topic} ({FMT[a.next.fmt]})</div>}
        </div>
        <div>
          <b>Last started:</b>{" "}
          {a.state.last_started ? <>#{a.state.last_started.countdown_id} {a.state.last_started.topic} <span style={muted}>· {ago(a.state.last_started.at)}</span></> : <span style={muted}>none yet</span>}
        </div>
        <div style={muted}>Last check: {ago(a.state.last_check_at)}{a.state.last_result ? ` — ${a.state.last_result}` : ""}</div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "6px 0" }}>
          <button style={small} disabled={saving || !a.settings.enabled || !a.next}
                  onClick={async () => { if (a.next && (await askConfirm(`Skip "${a.next.topic}"? It moves to "Didn't make the list" (un-skip it there any time).`))) run(() => api.countdownAutoSkipNext()); }}>
            ⏭ Skip next pick
          </button>
          {paused ? (
            <button style={small} disabled={saving} onClick={() => run(() => api.countdownAutoPauseToday(false))}>
              ▶ Resume today (paused until {new Date(a.settings.paused_until!).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })})
            </button>
          ) : (
            <button style={small} disabled={saving || !a.settings.enabled} title="Nothing new starts until midnight"
                    onClick={() => run(() => api.countdownAutoPauseToday(true))}>⏸ Pause for today</button>
          )}
        </div>
        <div style={muted}>Undo: switch it off — nothing new starts. A countdown already being made carries on; press ■ Stop on it to halt it.</div>
      </div>
      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 700 }}>Up next — your line ({a.line.length})</div>
        {a.line.length === 0 ? (
          <div style={muted}>Empty. Press “+ Add to line” on a topic (or next to Research &amp; rank) to have it made before the automation's own pick.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
            {a.line.map((it, i) => (
              <div key={it.id} style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", background: "var(--row)", borderRadius: 6, padding: "4px 6px", fontSize: 12 }}>
                <b style={{ color: "var(--accent)" }}>{i + 1}.</b>
                <span style={{ flex: "1 1 160px", minWidth: 0, overflowWrap: "anywhere" }}>{it.topic} <span style={muted}>· {FMT[it.fmt]}</span></span>
                <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={saving || i === 0} onClick={() => run(() => api.countdownLineUp(it.id))}>↑</button>
                <button className="primary" style={{ fontSize: 10, padding: "1px 6px" }} disabled={saving} title="Start it now instead of waiting for the daily slot"
                        onClick={async () => {
                          if (!(await askConfirm(`Make "${it.topic}" now? It starts research right away (paid) and counts toward today's limit.`))) return;
                          setSaving(true);
                          try { const c = await api.countdownLineStart(it.id); notify(`✓ Started countdown #${c.id}.`); load(); onChange(); }
                          catch (e: any) { notify(errText(e)); } finally { setSaving(false); }
                        }}>▶ Make now</button>
                <button className="danger" style={{ fontSize: 10, padding: "1px 6px" }} disabled={saving} onClick={() => run(() => api.countdownLineRemove(it.id))}>✕</button>
              </div>
            ))}
          </div>
        )}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 8, fontSize: 11 }}>
        <span style={muted}>Per day (counts countdowns you start too):</span>
        <label>Countdowns <input style={{ width: 48, fontSize: 11 }} inputMode="numeric" value={perDay} onChange={(e) => setPerDay(e.target.value)} /></label>
        <button style={small} disabled={saving || Number(perDay) === a.settings.per_day}
                onClick={() => {
                  const n = Number(perDay);
                  if (!Number.isInteger(n) || n < 0 || n > 5) return notify("The limit must be a whole number 0–5.");
                  run(() => api.countdownAutoSet({ per_day: n }));
                }}>Save limit</button>
        <span style={muted}>Segments get Studio from 12–3 PM, and any time LongForm is done for the day.</span>
      </div>
    </div>
  );
}

// --- your countdowns (same as LongForm's Your videos) --------------------------------------------
function CountdownsList({ list, onOpen, onReload }: { list: CountdownProject[]; onOpen: (id: number) => void; onReload: () => void }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [working, setWorking] = useState(false);
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: list.length };
    list.forEach((x) => { const k = stateOf(x); c[k] = (c[k] || 0) + 1; });
    return c;
  }, [list]);
  const rank = (c: CountdownProject) => { const a = attention(c); return a ? (a.kind === "fix" ? 0 : a.kind === "review" ? 1 : 2) : 3; };
  const shown = list.filter((c) => filter === "all" || stateOf(c) === filter).sort((a, b) => rank(a) - rank(b));
  const chosen = list.filter((c) => sel.has(c.id));
  const needs = list.filter((c) => attention(c)?.kind === "review").length;
  const toSend = list.filter((c) => attention(c)?.kind === "send").length;
  const toggle = (id: number) => setSel((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  async function batch(action: string, ids: number[], confirm?: string) {
    if (!ids.length) return;
    if (confirm && !(await askConfirm(confirm))) return;
    setWorking(true);
    try {
      const r = await api.countdownBatch(action, ids);
      const bad = r.results.filter((x) => !x.ok);
      notify(`✓ ${r.done} of ${r.results.length} done.` + (bad.length ? `\nNot done: ${bad.map((b) => `#${b.id} (${b.why})`).join("; ")}` : ""));
      setSel(new Set());
      onReload();
    } catch (e: any) { notify(errText(e)); } finally { setWorking(false); }
  }
  const pills: [Filter, string][] = [["all", "All"], ["review", "Needs review"], ["making", "Being made"], ["reviewed", "Reviewed"],
    ["rendered", "Rendered"], ["queued", "In queue"], ["archived", "🗄 Archived"]];
  const renderable = chosen.filter((c) => c.has.segments && !c.has.render);
  const sendable = chosen.filter((c) => c.has.render && !c.queue_item_id);
  return (
    <div style={card}>
      <h2 style={h2}>Your countdowns ({list.length})</h2>
      {(needs > 0 || toSend > 0) && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8, fontSize: 12, fontWeight: 700 }}>
          {needs > 0 && <span style={{ color: "#f59e0b" }}>👀 {needs} ready for your review</span>}
          {toSend > 0 && <span style={{ color: "#3b82f6" }}>📤 {toSend} rendered, not in the queue yet</span>}
        </div>
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
        {pills.map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k)} style={{ fontSize: 11, padding: "2px 9px", background: filter === k ? "var(--chip)" : "transparent" }}>
            {label} ({counts[k] || 0})
          </button>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 10, fontSize: 11 }}>
        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input type="checkbox" checked={shown.length > 0 && shown.every((c) => sel.has(c.id))}
                 onChange={(e) => setSel(e.target.checked ? new Set([...sel, ...shown.map((c) => c.id)]) : new Set([...sel].filter((id) => !shown.some((c) => c.id === id))))} />
          Select all shown
        </label>
        <button style={small} disabled={working || !renderable.length}
                onClick={() => batch("render", renderable.map((c) => c.id), `Render ${renderable.length} countdown(s)? Each is stitched in the background.`)}>
          🎞 Render selected ({renderable.length})
        </button>
        <button style={small} disabled={working || !sendable.length}
                onClick={() => batch("publish", sendable.map((c) => c.id), `Send ${sendable.length} countdown(s) to the Countdowns queue as Ready to Post (Screen Central)?`)}>
          📤 Send to queue ({sendable.length})
        </button>
        <button style={small} disabled={working || !chosen.length}
                onClick={() => batch("archive", chosen.map((c) => c.id), `Archive ${chosen.length} countdown(s)? They're deleted automatically in 5 days.`)}>
          🗄 Archive ({chosen.length})
        </button>
        {chosen.some((c) => c.review?.delete_after) && <button style={small} disabled={working} onClick={() => batch("unarchive", chosen.map((c) => c.id))}>↩ Un-archive</button>}
        <button className="danger" style={small} disabled={working || !chosen.length}
                onClick={() => batch("delete", chosen.map((c) => c.id), `Delete ${chosen.length} countdown(s) now? Their videos and the segments made only for them are removed. Can't be undone.`)}>
          🗑 Delete ({chosen.length})
        </button>
        {sel.size > 0 && <button style={small} onClick={() => setSel(new Set())}>Clear</button>}
      </div>
      {shown.length === 0 ? (
        <div style={muted}>{list.length ? "Nothing in this filter." : "Nothing yet. Turn on the automation above, or start one below."}</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 250px), 1fr))", gap: 10 }}>
          {shown.map((c) => {
            const att = attention(c);
            return (
              <div key={c.id} style={{ display: "flex", gap: 6, alignItems: "stretch", background: "var(--row)", borderRadius: 8, minWidth: 0,
                                       border: sel.has(c.id) ? "1px solid var(--accent)" : att ? `2px solid ${att.color}` : "1px solid transparent",
                                       boxShadow: att ? `0 0 0 3px ${att.color}22` : undefined }}>
                <input type="checkbox" checked={sel.has(c.id)} onChange={() => toggle(c.id)} style={{ marginLeft: 6 }} />
                <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
                  <button onClick={() => onOpen(c.id)} style={{ textAlign: "left", padding: 6, background: "transparent", border: "none", display: "flex", gap: 10, minWidth: 0 }}>
                    <Posters urls={c.posters} />
                    <span style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
                      <b style={{ fontSize: 12, overflowWrap: "anywhere" }}>{c.title}</b>
                      <span style={muted}>{FMT[c.fmt] || c.fmt} · {c.entry_count} titles · ~{c.est_minutes} min</span>
                      <StageLine c={c} />
                      {c.queue_item_id && <span style={{ ...muted, color: "var(--accent)" }}>in Countdowns queue #{c.queue_item_id}{c.queue_status ? ` · ${c.queue_status}` : ""}</span>}
                      {c.render?.archived_at && <span style={{ ...muted, color: "#93c5fd" }}>📦 archived — video in Drive</span>}
                      {!!c.cost_usd && <span style={muted}>spent {money(c.cost_usd)}</span>}
                    </span>
                  </button>
                  {att && <div style={{ margin: "0 6px 4px", fontSize: 10, fontWeight: 700, color: att.color }}>{att.label}</div>}
                  <div style={{ display: "flex", gap: 6, alignItems: "center", padding: "0 6px 6px", flexWrap: "wrap" }}>
                    {c.auto && <span style={{ ...muted, fontSize: 10 }}>🤖 auto</span>}
                    {c.review?.delete_after && <span style={{ ...muted, fontSize: 10, color: "#f59e0b" }}>🗄 deletes {new Date(c.review.delete_after).toLocaleDateString()}</span>}
                    <span style={{ marginLeft: "auto", display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {["stopped", "error", "paused"].includes(c.stage_status) && c.stage !== "new" && (
                        <button className="primary" style={{ fontSize: 10, padding: "1px 6px" }} disabled={working} title="Continue from the step it was on"
                                onClick={async () => { try { await api.countdownResume(c.id); notify(`✓ "${c.title}" resumed.`); onReload(); } catch (e: any) { notify(errText(e)); } }}>
                          ▶ Resume
                        </button>
                      )}
                      {c.stage === "segments" && c.stage_status === "running" && (c.stage_message || "").includes("line") && (
                        <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={working} title="Front of Studio's line"
                                onClick={async () => { try { await api.countdownMoveUp(c.id); notify(`✓ "${c.title}" is next in line.`); onReload(); } catch (e: any) { notify(errText(e)); } }}>
                          ⏫ Move up
                        </button>
                      )}
                      <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={working || c.stage_status === "running"}
                              title={c.review?.delete_after ? "Keep it" : "Delete automatically in 5 days"}
                              onClick={async () => { try { await api.countdownArchive(c.id, !c.review?.delete_after); onReload(); } catch (e: any) { notify(errText(e)); } }}>
                        {c.review?.delete_after ? "↩ Keep" : "🗄 Archive"}
                      </button>
                      <button className="danger" style={{ fontSize: 10, padding: "1px 6px" }} title="Delete now"
                              onClick={async () => { if (await deleteCountdown(c)) { setSel((s) => { const n = new Set(s); n.delete(c.id); return n; }); onReload(); } }}>🗑</button>
                    </span>
                    {c.has.titles && (
                      <label style={{ fontSize: 10, display: "flex", gap: 3, alignItems: "center" }}>
                        <input type="checkbox" checked={!!c.review?.reviewed_at}
                               onChange={async (e) => { try { await api.countdownReview(c.id, e.target.checked); onReload(); } catch (er: any) { notify(errText(er)); } }} />
                        ✓ Reviewed
                      </label>
                    )}
                  </div>
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
    `Breakdowns from LongForm Studio that it reused are kept. If it's waiting to post, it leaves the queue too. Can't be undone — 🗄 Archive gives 5 days.`))) return false;
  try { await api.countdownDelete(c.id); return true; } catch (e: any) { notify(`Could not delete: ${errText(e)}`); return false; }
}

// --- start a new countdown (same shape as "Start a new breakdown") --------------------------------
function NewCountdown({ onCreated, onLined }: { onCreated: (c: CountdownProject) => void; onLined: () => void }) {
  const [tab, setTab] = useState<"top" | "month" | "trend" | "evergreen" | "breakdowns">("top");
  const [topic, setTopic] = useState("");
  const [fmt, setFmt] = useState("top10");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [d, setD] = useState<CountdownTopics | null>(null);
  const [mine, setMine] = useState<StudioProject[] | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [showOut, setShowOut] = useState(false);
  const [picked1, setPicked1] = useState<CountdownTopic | null>(null);      // the topic the box was filled from
  const boxRef = useRef<HTMLDivElement | null>(null);
  const fromTopic = picked1 && picked1.topic === topic.trim() ? picked1 : null;

  const load = () => api.countdownTopicList().then((x) => { setD(x); setErr(""); }).catch((e) => setErr(errText(e)));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!d?.building) return;
    const t = setInterval(() => { if (!document.hidden) load(); }, 5000);
    return () => clearInterval(t);
  }, [d?.building]);
  useEffect(() => {
    if (tab === "breakdowns" && !mine) api.studioProjects().then((ps) => setMine(ps.filter((p) => p.has?.render))).catch((e) => setErr(errText(e)));
  }, [tab]);

  async function research() {
    if (!topic.trim()) return setErr("Type a topic, e.g. “Top 10 horror movies on Netflix right now”.");
    setBusy(true); setErr("");
    try {
      onCreated(fromTopic ? await api.countdownTopicStart(fromTopic.sig, fmt) : await api.countdownCreate({ topic: topic.trim(), fmt, notes }));
    } catch (e: any) { setErr(errText(e)); } finally { setBusy(false); }
  }
  async function addToLine(t?: CountdownTopic, f?: string) {
    const name = t ? t.topic : topic.trim();
    if (!name) return setErr("Type a topic (or pick one below) first.");
    setBusy(true); setErr("");
    try {
      const sig = t ? t.sig : fromTopic?.sig;
      await api.countdownLineAdd({ topic: name, fmt: f || fmt, sig: sig || undefined, notes });
      notify(`✓ Added to your line: “${name}” (${FMT[f || fmt]}). It's made before the automation's own pick.`);
      if (!t || t.sig === fromTopic?.sig) { setTopic(""); setPicked1(null); }
      onLined();
      load();
    } catch (e: any) { setErr(errText(e)); } finally { setBusy(false); }
  }
  function pickTopic(t: CountdownTopic, f: string) {
    setTopic(t.topic); setFmt(f); setPicked1(t); setErr("");
    boxRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  async function mark(t: CountdownTopic, kind: "pinned" | "skipped", on: boolean) {
    try { setD(await api.countdownTopicMark(t.sig, kind, on)); } catch (e: any) { notify(errText(e)); }
  }
  async function fromBreakdowns() {
    if (picked.length < 2) return setErr("Pick at least 2 breakdowns.");
    if (!topic.trim()) return setErr("Type a title for the countdown first.");
    setBusy(true); setErr("");
    try { onCreated(await api.countdownFromBreakdowns({ topic: topic.trim(), project_ids: picked })); } catch (e: any) { setErr(errText(e)); } finally { setBusy(false); }
  }
  const topics = (d?.topics || []).filter((t) => tab === "top" || t.group === tab);
  return (
    <div style={card} ref={boxRef}>
      <h2 style={h2}>Start a new countdown</h2>
      <div style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
        <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="Type a topic, e.g. Top 10 horror movies on Netflix right now" style={{ flex: "1 1 220px", minWidth: 0 }} />
      </div>
      {tab !== "breakdowns" && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 6 }}>
          {(["top5", "top10", "top15"] as const).map((f) => (
            <button key={f} onClick={() => setFmt(f)} disabled={!!fromTopic && !fromTopic.sizes.includes(Number(f.slice(3)))}
                    style={{ ...small, background: fmt === f ? "var(--chip)" : "transparent" }}>{FMT[f]}</button>
          ))}
          <button className="primary" style={{ fontSize: 12 }} disabled={busy} onClick={research} title="Start it now: research (one paid AI call), then you check the titles">
            {busy ? "Starting…" : "Research & rank"}
          </button>
          <button style={{ fontSize: 12 }} disabled={busy} onClick={() => addToLine()} title="Have the automation make it next (within the daily limit)">+ Add to line</button>
          <input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Optional: extra instructions (e.g. only 2010s films)" style={{ flex: "1 1 200px", minWidth: 0, fontSize: 11 }} />
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap" }}>
        {([["top", "🔥 Top topics (checked & ranked)"], ["month", `📅 This month${d?.month ? ` (${d.month.split(" ")[0]})` : ""}`],
           ["trend", "📈 Breaking out on YouTube"], ["evergreen", "Evergreen"], ["breakdowns", "From your breakdowns"]] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{ fontSize: 11, padding: "3px 10px", background: tab === k ? "var(--chip)" : "transparent" }}>{label}</button>
        ))}
      </div>
      {fromTopic && tab !== "breakdowns" && (
        <div style={{ fontSize: 11, background: "var(--row)", borderRadius: 6, padding: "6px 8px", marginBottom: 8, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ overflowWrap: "anywhere" }}>
            From the topic list: its {fromTopic.titles.length} checked titles are used
            {fromTopic.demand ? ` · vidIQ ${short(fromTopic.demand.monthly_searches)} searches/mo` : ""}. Nothing is spent until you press Research &amp; rank or + Add to line.
          </span>
          <button style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => { setPicked1(null); setTopic(""); }}>✕ Clear</button>
        </div>
      )}
      {err && <div style={{ fontSize: 12, color: "var(--red)", marginBottom: 8 }}>{err}</div>}

      {tab !== "breakdowns" && (
        <>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
            <span style={muted}>
              Fresh topics from this month's releases and what's breaking out on YouTube, each checked: real titles on TMDB (which of Top 5/10/15 it can fill),
              searches per month from vidIQ (✓ at 20K+), timing and debate. Topics already made never come back. Rebuilt every day —
              updated {d?.built_at ? new Date(d.built_at).toLocaleString() : "never"}{d?.building ? " — rebuilding…" : ""}.
            </span>
            <button style={{ fontSize: 11, padding: "2px 8px" }} disabled={d?.building}
                    onClick={async () => { try { await api.countdownTopicsRefresh(); load(); } catch (e: any) { notify(errText(e)); } }}>↻ Refresh topics</button>
          </div>
          {tab === "trend" && (d?.videos || []).length > 0 && (
            <div style={{ ...muted, marginBottom: 8 }}>
              <b>Breaking out now:</b> {(d!.videos || []).slice(0, 6).map((v) => `“${v.title}” (${short(v.views)} views)`).join(" · ")}
            </div>
          )}
          {tab === "trend" && (d?.rising || []).length > 0 && (
            <div style={{ ...muted, marginBottom: 8, overflowWrap: "anywhere" }}>
              <b>Rising searches this week:</b> {(d!.rising || []).slice(0, 10).map((r) => `“${r.keyword}” ${short(r.monthly_searches)}/mo (+${r.change_pct}%)`).join(" · ")}
            </div>
          )}
          {tab === "trend" && d?.asks && (
            <details style={{ ...muted, marginBottom: 8 }}>
              <summary style={{ cursor: "pointer" }}>💬 What viewers of countdown channels are asking for (vidIQ, weekly)</summary>
              <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", marginTop: 4 }}>{d.asks.slice(0, 1200)}</div>
            </details>
          )}
          {!d ? <div style={muted}>Loading topics…</div> : topics.length === 0 ? (
            <div style={muted}>{d.building ? "Building the list…" : "Nothing here yet — ↻ Refresh topics."}</div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 300px), 1fr))", gap: 8 }}>
              {topics.map((t) => <TopicCard key={t.sig} t={t} busy={busy} selected={fromTopic?.sig === t.sig} onPick={pickTopic}
                                             onLine={(tt, f) => addToLine(tt, f)} onMark={mark} />)}
            </div>
          )}
          {(d?.skipped || []).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <button style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setShowOut(!showOut)}>{showOut ? "▾" : "▸"} Didn't make the list ({d!.skipped.length})</button>
              {showOut && (
                <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 6 }}>
                  {d!.skipped.map((t) => (
                    <div key={t.sig} style={{ fontSize: 11, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                      <span style={{ overflowWrap: "anywhere" }}>✕ {t.topic}</span>
                      <button style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => mark(t, "skipped", false)}>Un-skip</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {tab === "breakdowns" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={muted}>Tick rendered breakdowns in ranking order (the first you tick is #1), type the title above, then make it. No research step; they're used as they are.</div>
          {!mine ? <div style={muted}>Loading…</div> : mine.length === 0 ? <div style={muted}>No rendered breakdowns yet.</div> : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 220px), 1fr))", gap: 6 }}>
              {mine.map((p) => {
                const i = picked.indexOf(p.id);
                return (
                  <label key={p.id} style={{ display: "flex", gap: 8, alignItems: "center", background: "var(--row)", borderRadius: 6, padding: 6, minWidth: 0,
                                             border: i >= 0 ? "1px solid var(--accent)" : "1px solid transparent" }}>
                    <input type="checkbox" checked={i >= 0} onChange={() => setPicked((x) => (x.includes(p.id) ? x.filter((y) => y !== p.id) : [...x, p.id]))} />
                    <span style={{ fontSize: 12, overflowWrap: "anywhere", minWidth: 0 }}>{i >= 0 ? <b>#{i + 1} </b> : null}{p.title}</span>
                  </label>
                );
              })}
            </div>
          )}
          <div><button className="primary" style={{ fontSize: 12 }} disabled={busy || picked.length < 2} onClick={fromBreakdowns}>Make a Top {picked.length || "N"} from these</button></div>
        </div>
      )}
    </div>
  );
}

function TopicCard({ t, busy, selected, onPick, onLine, onMark }: {
  t: CountdownTopic; busy: boolean; selected: boolean; onPick: (t: CountdownTopic, fmt: string) => void;
  onLine: (t: CountdownTopic, fmt: string) => void; onMark: (t: CountdownTopic, k: "pinned" | "skipped", on: boolean) => void;
}) {
  const [preview, setPreview] = useState(false);
  const posters = t.titles.map((x) => x.poster).filter(Boolean) as string[];
  return (
    <div style={{ background: "var(--row)", borderRadius: 8, padding: 8, display: "flex", flexDirection: "column", gap: 4, minWidth: 0,
                  border: selected ? "2px solid var(--accent)" : t.pinned ? "1px solid var(--accent)" : "1px solid transparent" }}>
      <div style={{ display: "flex", gap: 8, minWidth: 0 }}>
        <Posters urls={posters.slice(0, 3)} size={26} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
            <b style={{ fontSize: 12, overflowWrap: "anywhere" }}>{t.pinned ? "⭐ " : ""}{t.topic}</b>
            <span style={{ fontSize: 12, fontWeight: 800, color: "var(--accent)" }} title="Score: vidIQ demand 40%, growth 10%, timing 25%, debate 15%, titles 10%">{t.score}</span>
          </div>
          <span style={{ ...muted, overflowWrap: "anywhere" }}>{t.why_now}</span>
        </div>
      </div>
      {t.demand && (
        <span style={{ ...muted, fontSize: 10 }}>
          vidIQ: {short(t.demand.monthly_searches)} searches/mo · competition {Math.round(t.demand.competition)}/100 · {t.demand.growth_pct >= 0 ? "▲" : "▼"}{Math.abs(Math.round(t.demand.growth_pct))}% · “{t.keyword}”
        </span>
      )}
      <Chips checks={t.checks} />
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {[5, 10, 15].map((n) => (
          <button key={n} className={t.sizes.includes(n) && `top${n}` === t.best_format ? "primary" : undefined}
                  style={tiny} disabled={busy || !t.sizes.includes(n)}
                  title={t.sizes.includes(n) ? `Put it in “Start a new countdown” as a Top ${n} (nothing is spent yet)` : `Only ${t.titles.length} real titles found`}
                  onClick={() => onPick(t, `top${n}`)}>Top {n}</button>
        ))}
        <button style={tiny} disabled={busy} title="Have the automation make it next, at its best size"
                onClick={() => onLine(t, t.sizes.includes(Number(t.best_format.slice(3))) ? t.best_format : `top${Math.max(...t.sizes)}`)}>+ Add to line</button>
        <button style={tiny} onClick={() => setPreview(!preview)}>{preview ? "Hide titles" : "👁 Preview titles"}</button>
        <button style={tiny} onClick={() => onMark(t, "pinned", !t.pinned)}>{t.pinned ? "Unpin" : "⭐ Pin"}</button>
        <button style={tiny} onClick={() => onMark(t, "skipped", true)}>✕ Skip</button>
      </div>
      {preview && (
        <ol style={{ margin: "4px 0 0", paddingLeft: 18, fontSize: 11 }}>
          {t.titles.map((x) => <li key={`${x.media_type}-${x.tmdb_id}`} style={{ overflowWrap: "anywhere" }}>{x.title}{x.year ? ` (${x.year})` : ""}</li>)}
        </ol>
      )}
    </div>
  );
}

// --- one countdown (same shape as a breakdown's page) -----------------------------------------------
function CountdownView({ id, status, onBack }: { id: number; status: StudioStatus | null; onBack: () => void }) {
  const [c, setC] = useState<CountdownProject | null>(null);
  const [err, setErr] = useState("");
  const [working, setWorking] = useState(false);
  const [segment, setSegment] = useState<number | null>(null);
  const [undoV, setUndoV] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  async function load() { try { setC(await api.countdown(id)); } catch (e: any) { setErr(errText(e)); } }
  useEffect(() => { load(); }, [id]);
  useEffect(() => {
    if (c && (busyStatus(c) || c.render?.drive?.status === "uploading")) {
      timer.current = setInterval(() => { if (!document.hidden) load(); }, 4000);
      return () => { if (timer.current) clearInterval(timer.current); };
    }
  }, [c?.stage_status, c?.stage, c?.render?.drive?.status]);

  async function act(fn: () => Promise<any>, ok?: string) {
    setErr(""); setWorking(true);
    try { const r = await fn(); if (r && typeof r === "object" && "id" in r && "stage" in r) setC(r); else await load(); if (ok) notify(ok); }
    catch (e: any) { setErr(errText(e)); }
    finally { setWorking(false); }
  }

  if (segment !== null) {
    return <ProjectView id={segment} status={status} backLabel="← Back to the countdown" onBack={() => { setSegment(null); load(); }} />;
  }
  if (!c) return <div style={card}>{err || "Loading…"}</div>;
  const running = c.stage_status === "running";
  const confirmed = c.has.titles;
  const done: Record<string, boolean> = { research: c.has.ranking, titles: confirmed, segments: c.has.segments, script: c.has.segments && !!c.script, render: c.has.render };
  const nextStep = !c.has.ranking ? "research" : !confirmed ? "titles" : !c.has.segments ? "segments" : !c.has.render ? "render" : null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, minWidth: 0 }}>
      <div style={{ ...card, display: "flex", gap: 14, alignItems: "flex-start", flexWrap: "wrap" }}>
        <button onClick={onBack} style={{ fontSize: 11 }}>← All countdowns</button>
        <Posters urls={c.posters} size={40} />
        <div style={{ flex: "1 1 220px", minWidth: 0 }}>
          <h1 style={{ margin: 0, fontSize: 18, overflowWrap: "anywhere" }}>{c.title}</h1>
          <div style={muted}>
            {FMT[c.fmt] || c.fmt} · Countdown #{c.id} · {c.entry_count} titles · ~{c.est_minutes} min{c.cost_usd ? ` · spent ${money(c.cost_usd)}` : ""}{c.auto ? " · 🤖 auto" : ""}
            {" · "}segment{" "}
            <select value={c.segment_minutes} disabled={running}
                    onChange={(e) => act(() => api.countdownSettings(c.id, { segment_minutes: Number(e.target.value) }))}
                    style={{ width: "auto", padding: "1px 4px", fontSize: 11 }}>
              {Array.from(new Set([0.6, 0.75, 0.9, 1.2, 1.6, 2, c.segment_minutes])).sort((x, y) => x - y).map((m) => (
                <option key={m} value={m}>{m < 1 ? `${Math.round(m * 60)} s` : `${m} min`}</option>
              ))}
            </select>
          </div>
          <div style={{ ...muted, overflowWrap: "anywhere" }}>
            Topic: {c.topic}{c.review?.demand?.monthly_searches ? ` · vidIQ ${short(c.review.demand.monthly_searches)} searches/mo` : ""}
          </div>
          <div style={{ marginTop: 6, fontSize: 12, color: c.stage_status === "error" ? "var(--red)" : busyStatus(c) ? "var(--yellow)" : c.stage_status === "paused" || c.stage_status === "stopped" ? "#f59e0b" : "var(--text)" }}>
            {busyStatus(c) ? "⏳ " : c.stage_status === "error" ? "✕ " : c.stage_status === "stopped" ? "■ " : c.stage_status === "done" ? "✓ " : ""}
            {c.stage_message || "Starting…"}
          </div>
          {err && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 4 }}>{err}</div>}
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          {busyStatus(c) && <button className="danger" style={{ fontSize: 12 }} disabled={working} onClick={() => act(() => api.countdownStop(c.id), "Stopped. Finished parts are kept.")}>■ Stop</button>}
          {["stopped", "error", "paused"].includes(c.stage_status) && c.stage !== "new" && (
            <button className="primary" style={{ fontSize: 12 }} disabled={working} onClick={() => act(() => api.countdownResume(c.id), "Resumed.")}>▶ Resume</button>
          )}
          <UndoButton scope={`countdown:${c.id}`} version={`${undoV}-${c.updated_at}`} onUndone={() => { setUndoV((v) => v + 1); load(); }} />
          <button className="danger" style={{ fontSize: 11 }} disabled={working} onClick={async () => { if (await deleteCountdown(c)) onBack(); }}>Delete</button>
        </div>
      </div>

      {/* Step stepper, like LongForm */}
      <div style={{ ...card, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {STEPS.map((s) => (
          <div key={s.key} title={s.hint}
               style={{ flex: "1 1 140px", minWidth: 0, padding: 8, borderRadius: 8, border: `1px solid ${nextStep === s.key ? "var(--accent)" : "var(--border)"}`,
                        background: done[s.key] ? "rgba(16,185,129,0.08)" : "var(--row)" }}>
            <div style={{ fontSize: 12, fontWeight: 700 }}>{done[s.key] ? "✓ " : ""}{s.label}</div>
            <div style={{ ...muted, minHeight: 28 }}>{s.hint}</div>
            <div style={{ display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap" }}>
              {s.key === "research" && (
                <button style={tiny} disabled={working || running || confirmed}
                        title={confirmed ? "Titles are confirmed — start a new countdown for a new list" : "Research the topic (one paid AI call)"}
                        onClick={async () => { if (!c.has.ranking || (await askConfirm("Research again? The current ranking is replaced."))) act(() => api.countdownResearch(c.id)); }}>
                  {c.has.ranking ? "Re-run" : "Run"}
                </button>
              )}
              {s.key === "titles" && (
                <button style={tiny} disabled={working || running || !c.has.ranking || confirmed || c.unmatched > 0}
                        title={c.unmatched ? "Swap or remove the unmatched titles first" : "Make a segment for each title"}
                        onClick={() => act(() => api.countdownConfirm(c.id), "Titles confirmed — segments are made one at a time.")}>
                  {confirmed ? "✓ Confirmed" : "Confirm titles"}
                </button>
              )}
              {s.key === "segments" && (
                <button style={tiny} disabled={working || !confirmed || c.has.segments || busyStatus(c)} title="Make the segments (also retries a failed one)"
                        onClick={() => act(() => api.countdownMake(c.id))}>▶ Make segments</button>
              )}
              {s.key === "render" && (
                <button style={tiny} disabled={working || running || !c.has.segments}
                        onClick={async () => { if (await askConfirm(c.has.render ? "Render the video again with the current script?" : "Render the countdown video now?")) act(() => api.countdownRender(c.id), "Rendering — it shows in the jobs bar."); }}>
                  {c.has.render ? "Re-render" : "Render video"}
                </button>
              )}
            </div>
          </div>
        ))}
        <div style={{ flex: "1 1 100%", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {nextStep && nextStep !== "render" && (
            <button className="primary" style={{ fontSize: 11 }} disabled={working || busyStatus(c) || (nextStep === "titles" && c.unmatched > 0)}
                    onClick={() => act(() => nextStep === "research" ? api.countdownResearch(c.id) : nextStep === "titles" ? api.countdownConfirm(c.id) : api.countdownMake(c.id))}>
              ▶ Continue to review (from {STEPS.find((x) => x.key === nextStep)?.label})
            </button>
          )}
          <span style={muted}>Research → segments run on their own; review the titles and script, then render. The finished video waits for 📤 Send to queue.</span>
        </div>
      </div>

      {c.has.render && <VideoPart c={c} onChange={setC} reload={load} />}
      <TitlesPart c={c} onChange={setC} locked={confirmed} />
      {confirmed && <SegmentsPart c={c} onOpen={setSegment} />}
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

function VideoPart({ c, onChange, reload }: { c: CountdownProject; onChange: (c: CountdownProject) => void; reload: () => void }) {
  const r = c.render!;
  const bust = `${r.rendered_at}-${r.thumb_v || 0}`;
  const [busy, setBusy] = useState(false);
  const drive = r.drive || {};
  return (
    <Part title={`🎬 Video (${r.minutes} min)`}>
      {r.archived_at ? (
        <div style={muted}>📦 Archived {new Date(r.archived_at).toLocaleDateString()} — the video is in Drive{drive.link ? <>: <a href={drive.link} target="_blank" rel="noreferrer">open ↗</a></> : ""}.</div>
      ) : (
        <video controls preload="metadata" src={api.countdownFileUrl(c.id, "video", r.rendered_at)} poster={api.countdownFileUrl(c.id, "thumbnail", bust)}
               style={{ width: "100%", maxHeight: 420, borderRadius: 8, background: "#000" }} />
      )}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", margin: "10px 0 4px" }}>
        <span style={muted}>Thumbnail (click to use it):</span>
        <button style={tiny} disabled={busy}
                onClick={async () => { setBusy(true); try { onChange(await api.countdownThumbRefresh(c.id)); } catch (e: any) { notify(errText(e)); } finally { setBusy(false); } }}>
          🔄 Refresh Options
        </button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 160px), 1fr))", gap: 8 }}>
        {Object.keys(r.thumbnails || {}).map((k) => (
          <button key={k} onClick={async () => { try { onChange(await api.countdownThumbnail(c.id, k)); } catch (e: any) { notify(errText(e)); } }}
                  style={{ padding: 0, borderRadius: 6, overflow: "hidden", border: r.selected_thumbnail === k ? "2px solid var(--accent)" : "2px solid transparent" }}>
            <img src={api.countdownFileUrl(c.id, `thumb_${k}`, bust)} alt="" style={{ width: "100%", display: "block" }} />
          </button>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 10, fontSize: 12 }}>
        <button className="primary" style={{ fontSize: 12 }} disabled={busy || !!c.queue_item_id}
                onClick={async () => {
                  if (!(await askConfirm("Send to the Countdowns queue as Ready to Post on Screen Central? It takes the next Countdowns post time (12:00 by default) and a copy is saved to Drive."))) return;
                  setBusy(true);
                  try { const q = await api.countdownPublish(c.id); notify(`✓ In the Countdowns queue (#${q.queue_item_id}, Ready to Post).`); reload(); }
                  catch (e: any) { notify(errText(e)); } finally { setBusy(false); }
                }}>
          {c.queue_item_id ? `✓ In Countdowns queue #${c.queue_item_id}` : "📤 Send to Countdowns queue"}
        </button>
        <span style={{ fontSize: 11, color: drive.status === "error" ? "var(--red)" : "var(--muted)" }}>
          ☁️ Drive: {drive.status === "saved" ? <>✓ saved{drive.link ? <> · <a href={drive.link} target="_blank" rel="noreferrer">open ↗</a></> : null}</>
            : drive.status === "uploading" ? "uploading…" : drive.status === "error" ? `error — ${drive.error}` : "saved when you send it to the queue"}
        </span>
        {(drive.status === "error" || drive.status === "stopped") && (
          <button style={tiny} onClick={async () => { try { await api.countdownDrive(c.id); reload(); } catch (e: any) { notify(errText(e)); } }}>Retry</button>
        )}
      </div>
      <div style={{ ...muted, margin: "10px 0 4px" }}>Chapters (go into the YouTube description):</div>
      <div style={{ fontFamily: "monospace", fontSize: 11, display: "flex", flexDirection: "column", gap: 2 }}>
        {r.chapters.map((ch, i) => <span key={i} style={{ overflowWrap: "anywhere" }}>{ch.time_formatted} {ch.title}</span>)}
      </div>
    </Part>
  );
}

function TitlesPart({ c, onChange, locked }: { c: CountdownProject; onChange: (c: CountdownProject) => void; locked: boolean }) {
  const entries = [...(c.ranking?.entries || [])].sort((a, b) => a.rank - b.rank);
  const [swapAt, setSwapAt] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<StudioTitle[] | null>(null);
  if (!c.has.ranking) return <Part title="2. Titles"><div style={muted}>{c.stage_status === "running" ? "Researching…" : "Run research first."}</div></Part>;
  async function save(next: CountdownEntryRow[]) {
    if (locked && !(await askConfirm("The titles are confirmed. Changing them resets the segments step — continue?"))) return;
    try { onChange(await api.countdownEntries(c.id, next)); } catch (e: any) { notify(errText(e)); }
  }
  const move = (i: number, d: number) => { const n = [...entries]; const j = i + d; if (j < 0 || j >= n.length) return; [n[i], n[j]] = [n[j], n[i]]; save(n); };
  async function search(e: React.FormEvent) { e.preventDefault(); if (!q.trim()) return; try { setResults(await api.studioSearch(q)); } catch (er: any) { notify(errText(er)); } }
  function pick(t: StudioTitle) {
    const row: CountdownEntryRow = { rank: 0, title: t.title, year: (t.date || "").slice(0, 4), tmdb_id: t.tmdb_id, media_type: t.media_type, poster: t.poster };
    const n = [...entries];
    if (swapAt === -1) n.push(row); else if (swapAt !== null) n[swapAt] = { ...n[swapAt], ...row };
    setSwapAt(null); setResults(null); setQ(""); save(n);
  }
  return (
    <Part title={`2. Titles (${entries.length})${c.unmatched ? ` — ${c.unmatched} to fix` : ""}`} open={!c.has.render}>
      {c.ranking?.why_now && <div style={{ ...muted, marginBottom: 8 }}>Why now: {c.ranking.why_now}</div>}
      {c.ranking?.market && (c.ranking.market.related?.length || c.ranking.market.competitors?.length) ? (
        <details style={{ ...muted, marginBottom: 8 }}>
          <summary style={{ cursor: "pointer" }}>
            🔎 What people search (vidIQ){c.ranking.market.demand ? ` — “${c.ranking.market.keyword}” ${short(c.ranking.market.demand.monthly_searches)}/mo` : ""}
          </summary>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4, overflowWrap: "anywhere" }}>
            {!!c.ranking.market.related?.length && <div><b>Related searches:</b> {c.ranking.market.related.map((r) => `${r.keyword} (${short(r.monthly_searches)})`).join(" · ")}</div>}
            {!!c.ranking.market.questions?.length && <div><b>People ask:</b> {c.ranking.market.questions.map((q) => q.question).join(" · ")}</div>}
            {!!c.ranking.market.competitors?.length && <div><b>Top existing videos:</b> {c.ranking.market.competitors.map((v) => `“${v.title}” (${short(v.views)} views)`).join(" · ")}</div>}
          </div>
        </details>
      ) : null}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {entries.map((e, i) => (
          <div key={`${e.rank}-${e.title}`} style={{ display: "flex", gap: 8, alignItems: "flex-start", background: "var(--row)", borderRadius: 8, padding: 8, minWidth: 0,
                                                    border: e.tmdb_id ? "1px solid transparent" : "2px solid #f59e0b" }}>
            <b style={{ fontSize: 16, width: 34, flexShrink: 0, color: "var(--accent)" }}>#{i + 1}</b>
            {e.poster ? <img src={e.poster.replace("/original/", "/w92/").replace("/w342/", "/w92/")} alt="" style={{ width: 38, borderRadius: 4, flexShrink: 0 }} />
                      : <div style={{ width: 38, height: 57, background: "var(--chip)", borderRadius: 4, flexShrink: 0 }} />}
            <div style={{ flex: 1, minWidth: 0 }}>
              <b style={{ fontSize: 12, overflowWrap: "anywhere" }}>{e.title}{e.year ? ` (${e.year})` : ""}</b>
              {!e.tmdb_id && <div style={{ fontSize: 11, color: "#f59e0b", fontWeight: 700 }}>⚠ Not a movie or show on TMDB — no trailer to use. Swap or remove it.</div>}
              {e.reused && <div style={{ ...muted, color: "#93c5fd" }}>Uses your LongForm breakdown as it is</div>}
              {e.why_it_ranks && <div style={{ ...muted, overflowWrap: "anywhere" }}>{e.why_it_ranks}</div>}
              {(e.key_stat || e.where_to_watch) && <div style={{ ...muted, overflowWrap: "anywhere" }}>{[e.key_stat, e.where_to_watch && `Watch: ${e.where_to_watch}`].filter(Boolean).join(" · ")}</div>}
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 6 }}>
                <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={i === 0} onClick={() => move(i, -1)} title="Higher rank">↑</button>
                <button style={{ fontSize: 10, padding: "1px 6px" }} disabled={i === entries.length - 1} onClick={() => move(i, 1)} title="Lower rank">↓</button>
                <button style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => { setSwapAt(i); setQ(e.title); setResults(null); }}>Swap</button>
                <button className="danger" style={{ fontSize: 10, padding: "1px 6px" }} disabled={entries.length <= 2} onClick={() => save(entries.filter((_, k) => k !== i))}>✕</button>
              </div>
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 8 }}><button style={small} onClick={() => { setSwapAt(-1); setQ(""); setResults(null); }}>+ Add a title</button></div>
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
                <button key={`${t.media_type}-${t.tmdb_id}`} onClick={() => pick(t)} style={{ display: "flex", gap: 8, alignItems: "center", textAlign: "left", fontSize: 12 }}>
                  {t.poster ? <img src={t.poster.replace("/original/", "/w92/").replace("/w342/", "/w92/")} alt="" style={{ width: 28, borderRadius: 3 }} /> : null}
                  <span style={{ overflowWrap: "anywhere" }}>{t.title} · {t.media_type === "tv" ? "TV" : "Movie"} {(t.date || "").slice(0, 4)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {c.ranking?.honorable_mentions?.length ? <div style={{ ...muted, marginTop: 8 }}>Honorable mentions: {c.ranking.honorable_mentions.join(" · ")}</div> : null}
    </Part>
  );
}

function SegmentsPart({ c, onOpen }: { c: CountdownProject; onOpen: (id: number) => void }) {
  const segs = [...(c.segments || [])].reverse();          // #1 first, like the titles list
  return (
    <Part title={`3. Segments (${c.segments_made} of ${c.segments_total} made)`} open={!c.has.segments}>
      <div style={{ ...muted, marginBottom: 8 }}>
        Each title gets a short breakdown written as a countdown entry (“Number 4: …”). They're made one at a time: from 12–3 PM, and any time LongForm is
        done for the day. They use the daily AI budget, not the daily breakdown limit. Open one to edit its script or shots and re-render it, exactly like a LongForm breakdown.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {segs.map((x) => (
          <div key={x.id} style={{ display: "flex", gap: 8, alignItems: "center", background: "var(--row)", borderRadius: 6, padding: 6, minWidth: 0, flexWrap: "wrap" }}>
            <b style={{ width: 34, flexShrink: 0, color: "var(--accent)" }}>#{x.rank}</b>
            <span style={{ flex: "1 1 120px", minWidth: 0, fontSize: 12, overflowWrap: "anywhere" }}>{x.title}{x.reused ? " (your breakdown)" : ""}</span>
            <span style={{ fontSize: 11, color: x.rendered ? "var(--accent)" : x.stage_status === "error" || x.stage_status === "stopped" ? "var(--red)" : ["running", "queued"].includes(x.stage_status) ? "var(--yellow)" : x.stage_status === "paused" ? "#f59e0b" : "var(--muted)" }}
                  title={x.stage_message || ""}>
              {x.rendered ? `✓ made${x.seconds ? ` (${Math.round(x.seconds)}s)` : ""}` : x.stage === "new" ? "not started" : `${x.stage} — ${x.stage_status === "queued" ? "waiting in line" : x.stage_status}`}
            </span>
            {!x.missing && <button style={{ fontSize: 10, padding: "1px 6px" }} onClick={() => onOpen(x.id)}>Open</button>}
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
  const [vq, setVq] = useState(false);
  useEffect(() => { setTitle(sc.title || ""); setIntro(sc.intro || ""); setOutro(sc.outro || ""); }, [c.id, c.updated_at]);
  const dirty = title !== (sc.title || "") || intro !== (sc.intro || "") || outro !== (sc.outro || "");
  const scoreOf = (t: string) => {
    const s = (sc.title_scores || {})[t];
    const n = s && (s.score ?? s.overallScore ?? s.ctrScore);
    return typeof n === "number" ? Math.round(n) : null;
  };
  async function vidiq(fn: () => Promise<CountdownProject>) {
    setVq(true);
    try { onChange(await fn()); } catch (e: any) { notify(errText(e)); } finally { setVq(false); }
  }
  return (
    <Part title="4. Script" open={c.has.segments && !c.has.render}>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <label style={{ fontSize: 12 }}>YouTube title
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={100} style={{ width: "100%" }} />
        </label>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap", alignItems: "center" }}>
          {(sc.title_options || []).map((t) => (
            <button key={t} style={{ fontSize: 10, padding: "1px 6px", border: t === title ? "1px solid var(--accent)" : undefined }} onClick={() => setTitle(t)}>
              {t}{scoreOf(t) !== null ? ` · ${scoreOf(t)}` : ""}
            </button>
          ))}
          <button style={tiny} disabled={vq} title="vidIQ click-through score for each title (5 credits each)" onClick={() => vidiq(() => api.countdownScoreTitles(c.id))}>📈 Score with vidIQ</button>
          <button style={tiny} disabled={vq} title="vidIQ title ideas (one call)" onClick={() => vidiq(() => api.countdownSuggestTitles(c.id))}>✨ vidIQ title ideas</button>
        </div>
        <label style={{ fontSize: 12 }}>Intro hook (voiced over a preview of the titles; tease #1 without naming it)
          <textarea value={intro} onChange={(e) => setIntro(e.target.value)} rows={3} style={{ width: "100%" }} />
        </label>
        <label style={{ fontSize: 12 }}>Closing question (voiced in the outro, to start the comments)
          <textarea value={outro} onChange={(e) => setOutro(e.target.value)} rows={2} style={{ width: "100%" }} />
        </label>
        {(c.review?.keywords || []).length > 0 && (
          <div style={{ ...muted, overflowWrap: "anywhere" }}>vidIQ tags used when sent: {(c.review!.keywords || []).slice(0, 12).join(", ")}</div>
        )}
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
