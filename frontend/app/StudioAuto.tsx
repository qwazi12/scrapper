"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  api,
  RenderBatch,
  StudioAuto,
  StudioCandidate,
  StudioCandidates,
  StudioProject,
} from "../lib/api";
import { askConfirm, notify } from "../lib/dialogs";

// Breakdown automation UI: the on/off switch + daily limits, the checked and
// ranked trending list (10 movies + 10 TV), and "Your videos" with review
// marks, render-in-a-batch and send-to-queue-in-a-batch.

const card: React.CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: "14px 16px",
};
const h2: React.CSSProperties = { margin: "0 0 10px", fontSize: 14, fontWeight: 700 };
const muted: React.CSSProperties = { color: "var(--muted)", fontSize: 11 };
const ago = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "never");
const short = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(n));

// --- automation switch ------------------------------------------------------------
export function AutomationCard({ onChange }: { onChange?: () => void }) {
  const [a, setA] = useState<StudioAuto | null>(null);
  const [movies, setMovies] = useState("3");
  const [tv, setTv] = useState("2");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");

  const load = () =>
    api.studioAuto().then((d) => {
      setA(d);
      setErr("");
    }).catch((e) => setErr(String(e.message || e)));
  useEffect(() => {
    load();
    const t = setInterval(() => !document.hidden && load(), 20000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (a) {
      setMovies(String(a.movies_per_day));
      setTv(String(a.tv_per_day));
    }
  }, [a?.movies_per_day, a?.tv_per_day]);

  async function save(patch: Parameters<typeof api.studioAutoSet>[0]) {
    setSaving(true);
    try {
      setA(await api.studioAutoSet(patch));
      onChange?.();
    } catch (e: any) {
      notify(`Could not save: ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  if (err) return <div style={{ ...card, color: "var(--red)", fontSize: 12 }}>Automation: {err}</div>;
  if (!a) return <div style={card}><span style={muted}>Loading automation…</span></div>;
  const total = a.movies_per_day + a.tv_per_day;
  const made = a.today.movie + a.today.tv;
  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ ...h2, margin: 0 }}>🤖 Breakdown automation</h2>
        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 700 }}>
          <input type="checkbox" checked={a.enabled} disabled={saving}
                 onChange={async (e) => {
                   const on = e.target.checked;
                   if (on && !(await askConfirm(
                     `Turn on automation?\n\nIt makes up to ${a.movies_per_day} movie + ${a.tv_per_day} TV breakdowns a day ` +
                     `from the top of the checked trending list (steps 1–5, ~$${a.estimate_usd.toFixed(2)} each), ` +
                     `one at a time, and stops before rendering so you can review.`))) return;
                   save({ enabled: on });
                 }} />
          {a.enabled ? <span style={{ color: "var(--accent)" }}>ON</span> : <span style={{ color: "var(--muted)" }}>OFF</span>}
        </label>
        <span style={muted}>Today: {made} of {total} started ({a.today.movie}/{a.movies_per_day} movies, {a.today.tv}/{a.tv_per_day} TV)</span>
      </div>

      <div style={{ fontSize: 12, marginTop: 8, lineHeight: 1.7 }}>
        <div>
          <b>Next:</b>{" "}
          {!a.enabled ? <span style={muted}>nothing — automation is off</span>
            : a.waiting ? <span style={{ color: "var(--yellow)" }}>waiting — {a.waiting}</span>
            : a.next ? <>{a.next.title} <span style={muted}>({a.next.media_type === "tv" ? "TV" : "movie"}, score {a.next.score}) at the next check (every 5 min)</span></>
            : <span style={{ color: "var(--yellow)" }}>no title passes every check — refresh the list or ⭐ pin one</span>}
        </div>
        <div>
          <b>Last started:</b>{" "}
          {a.last_started ? <>#{a.last_started.project_id} {a.last_started.title} <span style={muted}>· {ago(a.last_started.at)}</span></> : <span style={muted}>none yet</span>}
        </div>
        <div style={muted}>Last check: {ago(a.last_check_at)}{a.last_result ? ` — ${a.last_result}` : ""}</div>
        <div style={muted}>Undo: {a.undo}</div>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginTop: 8, fontSize: 11 }}>
        <span style={muted}>Per day (counts manual breakdowns too):</span>
        <label>Movies <input style={{ width: 48, fontSize: 11 }} inputMode="numeric" value={movies} onChange={(e) => setMovies(e.target.value)} /></label>
        <label>TV <input style={{ width: 48, fontSize: 11 }} inputMode="numeric" value={tv} onChange={(e) => setTv(e.target.value)} /></label>
        <button style={{ fontSize: 11, padding: "3px 10px" }}
                disabled={saving || (Number(movies) === a.movies_per_day && Number(tv) === a.tv_per_day)}
                onClick={() => {
                  const m = Number(movies), t = Number(tv);
                  if (![m, t].every((n) => Number.isInteger(n) && n >= 0 && n <= 20)) return notify("Each limit must be a whole number 0–20.");
                  save({ movies_per_day: m, tv_per_day: t });
                }}>Save limits</button>
      </div>
    </div>
  );
}

// --- ranked trending list -----------------------------------------------------------
const CHECK_LABEL: Record<string, string> = {
  not_made: "New", trailer: "Trailer", facts: "Facts", release: "Release", interest: "Interest",
};

function CheckChips({ c }: { c: StudioCandidate }) {
  return (
    <div style={{ display: "flex", gap: 3, flexWrap: "wrap" }}>
      {Object.entries(c.checks).map(([k, v]) => (
        <span key={k} title={v.why}
              style={{ fontSize: 9, padding: "1px 5px", borderRadius: 8,
                       background: v.status === "pass" ? "#064e3b" : v.status === "warn" ? "#422006" : "#450a0a",
                       color: v.status === "pass" ? "#34d399" : v.status === "warn" ? "#fcd34d" : "#fca5a5" }}>
          {v.status === "pass" ? "✓" : v.status === "warn" ? "⚠" : "✕"} {CHECK_LABEL[k] || k}
        </span>
      ))}
    </div>
  );
}

function Sentiment({ c }: { c: StudioCandidate }) {
  const i = c.imdb, t = c.trailer_stats;
  const bits: string[] = [];
  if (i?.rating && i.votes >= 50) bits.push(`IMDb ${i.rating}/10 (${short(i.votes)})`);
  if (i?.metascore) bits.push(`Metacritic ${i.metascore}`);
  if (i?.review_avg) bits.push(`reviews ★${i.review_avg}`);
  if (i?.meter_rank) bits.push(`IMDb pop #${i.meter_rank}${i.meter_change === "UP" ? " ▲" : i.meter_change === "DOWN" ? " ▼" : ""}`);
  if (t?.views) bits.push(`trailer ${short(t.views)} views (${short(c.score.views_per_day)}/day)`);
  if (t?.views && t.likes != null) bits.push(`${((t.likes / t.views) * 100).toFixed(1)}% likes`);
  return <div style={{ ...muted, fontSize: 10, lineHeight: 1.4 }}>{bits.join(" · ") || "no audience data yet"}</div>;
}

function CandidateCard({ c, busy, onCreate, onMark }: {
  c: StudioCandidate; busy: boolean; onCreate: (c: StudioCandidate) => void; onMark: (c: StudioCandidate, m: "pin" | "skip" | null) => void;
}) {
  const parts = Object.entries(c.score.parts).filter(([, v]) => v > 0).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(", ");
  return (
    <div style={{ background: "var(--row)", borderRadius: 8, padding: 8, display: "flex", gap: 8,
                  border: c.mark === "pin" ? "1px solid var(--accent)" : "1px solid transparent" }}>
      {c.poster ? <img src={c.poster} alt="" style={{ width: 54, borderRadius: 4, alignSelf: "flex-start" }} />
        : <div style={{ width: 54, height: 81, background: "var(--chip)", borderRadius: 4 }} />}
      <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
          <b style={{ fontSize: 12 }}>{c.mark === "pin" ? "⭐ " : ""}{c.title}</b>
          <span title={parts} style={{ fontSize: 12, fontWeight: 800, color: "var(--accent)" }}>{c.score.total}</span>
        </div>
        <span style={muted}>
          Trending #{c.trending_rank} · {c.checks.release.why}
          {c.imdb?.page && <> · <a href={c.imdb.page} target="_blank" rel="noreferrer">IMDb ↗</a></>}
        </span>
        <Sentiment c={c} />
        <CheckChips c={c} />
        {!c.auto_ok && c.checks.trailer.status !== "fail" && (
          <span style={{ ...muted, color: "#fcd34d" }}>Manual only: {c.checks.trailer.status === "warn" ? c.checks.trailer.why : "skipped"}</span>
        )}
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          <button className="primary" style={{ fontSize: 10, padding: "2px 8px" }} disabled={busy || c.checks.not_made.status === "fail"}
                  onClick={() => onCreate(c)}>Make breakdown</button>
          <button style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => onMark(c, c.mark === "pin" ? null : "pin")}>
            {c.mark === "pin" ? "Unpin" : "⭐ Pin"}</button>
          <button style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => onMark(c, c.mark === "skip" ? null : "skip")}>
            {c.mark === "skip" ? "Un-skip" : "✕ Skip"}</button>
        </div>
      </div>
    </div>
  );
}

export function RankedTrending({ busy, onCreate }: { busy: boolean; onCreate: (c: StudioCandidate) => void }) {
  const [d, setD] = useState<StudioCandidates | null>(null);
  const [err, setErr] = useState("");
  const [showOut, setShowOut] = useState(false);

  const load = () => api.studioCandidates().then((x) => { setD(x); setErr(""); }).catch((e) => setErr(String(e.message || e)));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!d?.refreshing) return;
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, [d?.refreshing]);

  async function mark(c: StudioCandidate, m: "pin" | "skip" | null) {
    try {
      setD(await api.studioCandidateMark(c.key, m));
    } catch (e: any) {
      notify(`Could not save: ${e.message || e}`);
    }
  }

  if (err) return <div style={{ fontSize: 12, color: "var(--red)" }}>{err}</div>;
  if (!d) return <div style={muted}>Loading the checked trending list…</div>;
  return (
    <div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", marginBottom: 8 }}>
        <span style={muted}>
          Top trending this week, checked &amp; ranked (trailer heat, IMDb/Metacritic/trailer reactions, popularity, release timing).
          Updated {d.built_at ? new Date(d.built_at).toLocaleString() : "never"}{d.refreshing ? " — refreshing…" : ""}.
        </span>
        <button style={{ fontSize: 11, padding: "2px 8px" }} disabled={d.refreshing}
                onClick={async () => {
                  try { await api.studioCandidatesRefresh(); load(); } catch (e: any) { notify(String(e.message || e)); }
                }}>↻ Refresh</button>
      </div>
      {d.youtube_note && <div style={{ ...muted, color: "var(--yellow)", marginBottom: 6 }}>{d.youtube_note}</div>}
      {d.last_error && <div style={{ fontSize: 11, color: "var(--red)", marginBottom: 6 }}>Last refresh failed: {d.last_error}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
        {(["movie", "tv"] as const).map((mt) => (
          <div key={mt}>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>
              {mt === "movie" ? "🎬 Top movies" : "📺 Top TV shows"} ({d[mt].length})
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 640, overflowY: "auto" }}>
              {d[mt].length === 0 && <div style={muted}>{d.built_at ? "None pass every check right now." : "Building the list…"}</div>}
              {d[mt].map((c) => <CandidateCard key={c.key} c={c} busy={busy} onCreate={onCreate} onMark={mark} />)}
            </div>
          </div>
        ))}
      </div>
      {d.not_passing.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setShowOut(!showOut)}>
            {showOut ? "▾" : "▸"} Didn't make the list ({d.not_passing.length}) — with reasons
          </button>
          {showOut && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 6, marginTop: 6 }}>
              {d.not_passing.map((c) => (
                <div key={c.key} style={{ background: "var(--row)", borderRadius: 6, padding: 6, fontSize: 11 }}>
                  <b>{c.title}</b> <span style={muted}>({c.media_type === "tv" ? "TV" : "movie"}, score {c.score.total})</span>
                  <div style={muted}>
                    {c.mark === "skip" ? "✕ skipped by you" : c.beyond_top ? "passes, but below the top 10" :
                      Object.values(c.checks).filter((x) => x.status === "fail").map((x) => x.why).join(" · ")}
                  </div>
                  {(c.mark === "skip") && <button style={{ fontSize: 10, padding: "1px 6px", marginTop: 3 }} onClick={() => mark(c, null)}>Un-skip</button>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- your videos: review, batch render, batch queue -----------------------------------------
type Filter = "all" | "review" | "reviewed" | "rendered" | "queued";

function stateOf(p: StudioProject): Filter {
  if (p.queue_item_id) return "queued";
  if (p.has?.render) return "rendered";
  if (p.review?.reviewed_at) return "reviewed";
  if (p.has?.script) return "review";
  return "all";
}

export function VideosList({ projects, onOpen, onReload, renderItem }: {
  projects: StudioProject[];
  onOpen: (id: number) => void;
  onReload: () => void;
  renderItem: (p: StudioProject) => React.ReactNode;
}) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<Filter>("all");
  const [batch, setBatch] = useState<RenderBatch | null>(null);
  const [working, setWorking] = useState(false);

  const loadBatch = () => api.studioRenderBatchStatus().then(setBatch).catch(() => {});
  useEffect(() => { loadBatch(); }, []);
  useEffect(() => {
    if (!batch?.running) return;
    const t = setInterval(() => { loadBatch(); onReload(); }, 5000);
    return () => clearInterval(t);
  }, [batch?.running]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: projects.length, review: 0, reviewed: 0, rendered: 0, queued: 0 };
    projects.forEach((p) => { const s = stateOf(p); if (s !== "all") c[s] += 1; });
    return c;
  }, [projects]);
  const shown = projects.filter((p) => filter === "all" || stateOf(p) === filter);
  const chosen = projects.filter((p) => sel.has(p.id));
  const toggle = (id: number) => setSel((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });

  async function markReviewed(p: StudioProject, on: boolean) {
    try { await api.studioReview(p.id, on); onReload(); } catch (e: any) { notify(String(e.message || e)); }
  }

  async function renderSelected() {
    const ids = chosen.filter((p) => p.has?.script && !p.archive?.archived_at).map((p) => p.id);
    if (!ids.length) return notify("Pick breakdowns that have a script (and aren't archived).");
    if (!(await askConfirm(`Render ${ids.length} breakdown(s) one by one?\n\nEach runs step 5 first if it hasn't, then step 6. You can Stop the batch any time.`))) return;
    setWorking(true);
    try { setBatch(await api.studioRenderBatch(ids)); setSel(new Set()); onReload(); }
    catch (e: any) { notify(`Could not start: ${e.message || e}`); }
    finally { setWorking(false); }
  }

  async function queueSelected() {
    const ids = chosen.filter((p) => p.has?.render).map((p) => p.id);
    if (!ids.length) return notify("Pick rendered breakdowns to send to the queue.");
    if (!(await askConfirm(`Send ${ids.length} rendered breakdown(s) to the Posting Queue (status Review)?`))) return;
    setWorking(true);
    try {
      const r = await api.studioPublishBatch(ids);
      const bad = r.results.filter((x) => !x.ok);
      notify(`✓ Sent ${r.sent} of ${r.results.length} to the Posting Queue.` + (bad.length ? `\nNot sent: ${bad.map((b) => `${b.title} (${b.why})`).join("; ")}` : ""));
      setSel(new Set());
      onReload();
    } catch (e: any) { notify(`Could not send: ${e.message || e}`); }
    finally { setWorking(false); }
  }

  const pills: [Filter, string][] = [["all", "All"], ["review", "Needs review"], ["reviewed", "Reviewed"], ["rendered", "Rendered"], ["queued", "In queue"]];
  return (
    <div style={card}>
      <h2 style={h2}>Your videos ({projects.length})</h2>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
        {pills.map(([k, label]) => (
          <button key={k} onClick={() => setFilter(k)}
                  style={{ fontSize: 11, padding: "2px 9px", background: filter === k ? "var(--chip)" : "transparent" }}>
            {label} ({counts[k]})
          </button>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 10, fontSize: 11 }}>
        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
          <input type="checkbox" checked={shown.length > 0 && shown.every((p) => sel.has(p.id))}
                 onChange={(e) => setSel(e.target.checked ? new Set([...sel, ...shown.map((p) => p.id)]) : new Set([...sel].filter((id) => !shown.some((p) => p.id === id))))} />
          Select all shown
        </label>
        <button style={{ fontSize: 11, padding: "3px 10px" }} disabled={working || !!batch?.running || !chosen.length} onClick={renderSelected}>
          🎞 Render selected ({chosen.filter((p) => p.has?.script).length})
        </button>
        <button style={{ fontSize: 11, padding: "3px 10px" }} disabled={working || !chosen.length} onClick={queueSelected}>
          📤 Send to queue ({chosen.filter((p) => p.has?.render).length})
        </button>
        {sel.size > 0 && <button style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => setSel(new Set())}>Clear</button>}
      </div>
      {batch && (batch.running || batch.results.length > 0) && (
        <div style={{ fontSize: 11, padding: 8, background: "var(--row)", borderRadius: 6, marginBottom: 10 }}>
          <b>{batch.running ? `⏳ Rendering ${Math.min(batch.done + 1, batch.total)} of ${batch.total}` : `Render batch finished — ${batch.results.filter((r) => r.ok).length} of ${batch.results.length} rendered`}</b>
          {batch.running && <span style={muted}> · stop it from the running-jobs list (■ Stop)</span>}
          {batch.results.map((r) => (
            <div key={r.id} style={{ color: r.ok ? "var(--accent)" : "var(--red)" }}>{r.ok ? "✓" : "✕"} {r.title}{r.why ? ` — ${r.why}` : ""}</div>
          ))}
        </div>
      )}
      {shown.length === 0 ? (
        <div style={muted}>{projects.length ? "Nothing in this filter." : "Nothing yet. Pick a title below to start one."}</div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
          {shown.map((p) => (
            <div key={p.id} style={{ display: "flex", gap: 6, alignItems: "stretch", background: "var(--row)", borderRadius: 8,
                                     border: sel.has(p.id) ? "1px solid var(--accent)" : "1px solid transparent" }}>
              <input type="checkbox" checked={sel.has(p.id)} onChange={() => toggle(p.id)} style={{ marginLeft: 6 }} />
              <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
                <button onClick={() => onOpen(p.id)} style={{ textAlign: "left", padding: 6, background: "transparent", border: "none" }}>
                  {renderItem(p)}
                </button>
                <div style={{ display: "flex", gap: 6, alignItems: "center", padding: "0 6px 6px", flexWrap: "wrap" }}>
                  {p.review?.auto && <span style={{ ...muted, fontSize: 10 }}>🤖 auto</span>}
                  {p.has?.script && (
                    <label style={{ fontSize: 10, display: "flex", gap: 3, alignItems: "center" }}>
                      <input type="checkbox" checked={!!p.review?.reviewed_at} onChange={(e) => markReviewed(p, e.target.checked)} />
                      ✓ Reviewed
                    </label>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
