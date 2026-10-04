"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { StopButton } from "./StopButton";
import {
  api,
  parseApiDate,
  StudioArchive,
  StudioMotion,
  StudioPlanItem,
  StudioProject,
  StudioShot,
  StudioStatus,
  StudioTitle,
} from "../lib/api";
import { UndoButton } from "./UndoButton";
import { askConfirm, notify } from "../lib/dialogs";
import { AutomationCard, deleteProject, RankedTrending, VideosList } from "./StudioAuto";

// LongForm Studio: trailer breakdowns from research to rendered video.
// Every stage runs on the server and saves its own output, so any step can be
// re-run after an edit without redoing the others.

const STAGES: { key: string; label: string; hint: string }[] = [
  { key: "gather", label: "1. Research", hint: "TMDB facts + web sources" },
  { key: "trailer", label: "2. Footage", hint: "Official trailer + clips" },
  { key: "shots", label: "3. Shots", hint: "Cut + tag every shot" },
  { key: "script", label: "4. Script", hint: "Written from the storyboard, fact-checked" },
  { key: "plan", label: "5. Voice + shots", hint: "Narration and a shot for every ~4 s" },
  { key: "render", label: "6. Render", hint: "Final 1080p video" },
];

const card: React.CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: "14px 16px",
};
const h2: React.CSSProperties = { margin: "0 0 10px", fontSize: 14, fontWeight: 700 };
const muted: React.CSSProperties = { color: "var(--muted)", fontSize: 11 };

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

function fmtDate(iso: string): string {
  if (!iso) return "date TBA";
  const d = new Date(iso + "T12:00:00Z");
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function StudioPanel() {
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [projects, setProjects] = useState<StudioProject[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [err, setErr] = useState("");

  async function loadList() {
    try {
      setProjects(await api.studioProjects());
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  useEffect(() => {
    api.studioStatus().then(setStatus).catch((e) => setErr(e.message || String(e)));
    loadList();
  }, []);

  if (openId !== null) {
    return (
      <ProjectView
        id={openId}
        status={status}
        onBack={() => {
          setOpenId(null);
          loadList();
        }}
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ ...card, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 18 }}>🎬 LongForm Studio</h1>
          <div style={muted}>Trailer breakdowns (2–4 min): research → footage → script → voice → video → Posting Queue</div>
        </div>
        {status && <KeyChips status={status} />}
      </div>
      <ArchiveRule />
      {err && <div style={{ ...card, color: "var(--red)", fontSize: 12 }}>{err}</div>}

      <AutomationCard onChange={loadList} />

      <VideosList
        projects={projects}
        onOpen={setOpenId}
        onReload={loadList}
        renderItem={(p) => (
          <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {p.poster ? (
              <img src={p.poster.replace("/original/", "/w92/")} alt="" style={{ width: 46, borderRadius: 4 }} />
            ) : (
              <div style={{ width: 46, height: 69, background: "var(--chip)", borderRadius: 4 }} />
            )}
            <span style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
              <b style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {p.title || `TMDB ${p.tmdb_id}`}
              </b>
              <StageBadge p={p} />
              {p.archive?.archived_at && <span style={{ ...muted, color: "#93c5fd" }}>📦 archived — video in Drive</span>}
              {p.queue_item_id && <span style={{ ...muted, color: "var(--accent)" }}>in Posting Queue #{p.queue_item_id}</span>}
              {!!p.cost_usd && <span style={muted}>spent ${p.cost_usd < 1 ? p.cost_usd.toFixed(3) : p.cost_usd.toFixed(2)}</span>}
            </span>
          </span>
        )}
      />

      <NewVideo
        status={status}
        onCreated={(p) => {
          setOpenId(p.id);
        }}
      />
    </div>
  );
}

function KeyChips({ status }: { status: StudioStatus }) {
  const chip = (ok: boolean, label: string, missing: string) => (
    <span
      title={ok ? `${label} connected` : `${missing} is not set on the server (Railway)`}
      style={{
        fontSize: 10,
        padding: "2px 8px",
        borderRadius: 10,
        background: ok ? "#064e3b" : "#450a0a",
        color: ok ? "#34d399" : "#fca5a5",
      }}
    >
      {ok ? "●" : "○"} {label}
    </span>
  );
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {chip(status.tmdb, "TMDB", "TMDB_API_KEY")}
      {chip(status.gemini, "Gemini", "GEMINI_API_KEY")}
      {chip(status.tts, "Voice", "TTS_API_KEY")}
    </div>
  );
}

function StageBadge({ p }: { p: StudioProject }) {
  const color =
    p.stage_status === "error" ? "var(--red)" : p.stage_status === "running" || p.stage_status === "queued" ? "var(--yellow)" : "var(--muted)";
  const label = STAGES.find((s) => s.key === p.stage)?.label || p.stage;
  return (
    <span style={{ fontSize: 10, color }}>
      {p.stage_status === "running" ? "⏳ " : p.stage_status === "queued" ? "⏳ " : p.stage_status === "error" ? "✕ " : ""}
      {p.stage === "new" ? "not started" : `${label} — ${p.stage_status === "queued" ? "waiting in line" : p.stage_status}`}
    </span>
  );
}

// --- choose a title -----------------------------------------------------------
function NewVideo({ status, onCreated }: { status: StudioStatus | null; onCreated: (p: StudioProject) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<StudioTitle[] | null>(null);
  const [cal, setCal] = useState<{ upcoming_movies: StudioTitle[]; on_the_air_tv: StudioTitle[]; trending: StudioTitle[] } | null>(null);
  const [tab, setTab] = useState<"upcoming_movies" | "trending" | "on_the_air_tv">("upcoming_movies");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [month, setMonth] = useState("all");
  const [kind, setKind] = useState<"all" | "cinema" | "digital">("all");

  useEffect(() => {
    if (status?.tmdb) api.studioCalendar().then(setCal).catch((e) => setErr(e.message || String(e)));
  }, [status?.tmdb]);

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setErr("");
    try {
      setResults(await api.studioSearch(q));
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  async function create(t: StudioTitle) {
    setBusy(true);
    try {
      let p: StudioProject;
      try {
        p = await api.studioCreate({ tmdb_id: t.tmdb_id, media_type: t.media_type, title: t.title });
      } catch (e: any) {
        const msg = String(e.message || e);
        const m = msg.match(/(daily_limit|already_made): ([^"]*)/);
        if (!m) throw e;
        if (!(await askConfirm(m[2]))) return;
        p = await api.studioCreate({ tmdb_id: t.tmdb_id, media_type: t.media_type, title: t.title, force: true });
      }
      await api.studioRun(p.id, "gather", true); // research through script, then the owner reviews
      onCreated(p);
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const byMonth = !results && tab === "upcoming_movies";
  const monthOf = (d: string) => (d || "").slice(0, 7);
  const monthLabel = (m: string) =>
    new Date(`${m}-15T12:00:00Z`).toLocaleString("en-US", { month: "long", year: "numeric" });
  const months = byMonth ? [...new Set((cal?.upcoming_movies ?? []).map((t) => monthOf(t.date)).filter(Boolean))] : [];
  const list = (results ?? cal?.[tab] ?? []).filter(
    (t) =>
      !byMonth ||
      ((month === "all" || monthOf(t.date) === month) &&
        (kind === "all" || (kind === "digital" ? t.release === "digital" : t.release !== "digital")))
  );
  return (
    <div style={card}>
      <h2 style={h2}>Start a new breakdown</h2>
      {status && !status.tmdb && (
        <div style={{ fontSize: 12, color: "var(--yellow)", marginBottom: 10 }}>
          Add <b>TMDB_API_KEY</b> in Railway to search titles and load the release calendar.
        </div>
      )}
      <form onSubmit={search} style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a movie or TV show…" style={{ flex: 1 }} />
        <button type="submit" disabled={!status?.tmdb}>Search</button>
        {results && (
          <button type="button" onClick={() => { setResults(null); setQ(""); }}>Calendar</button>
        )}
      </form>
      {!results && (
        <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
          {([
            ["upcoming_movies", "Upcoming movies"],
            ["trending", "🔥 Top trending (checked & ranked)"],
            ["on_the_air_tv", "TV airing now"],
          ] as const).map(([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              style={{ fontSize: 11, padding: "3px 10px", background: tab === k ? "var(--chip)" : "transparent" }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {byMonth && months.length > 0 && (
        <div style={{ display: "flex", gap: 6, marginBottom: 10, flexWrap: "wrap", alignItems: "center" }}>
          <span style={muted}>Opening in the US, next 3 months:</span>
          {["all", ...months].map((m) => (
            <button key={m} onClick={() => setMonth(m)}
              style={{ fontSize: 11, padding: "2px 9px", background: month === m ? "var(--chip)" : "transparent" }}>
              {m === "all" ? "All" : monthLabel(m).split(" ")[0]}
            </button>
          ))}
          <span style={{ ...muted, marginLeft: 8 }}>Release:</span>
          {([["all", "All"], ["cinema", "In theaters"], ["digital", "Digital / streaming"]] as const).map(([k, label]) => (
            <button key={k} onClick={() => setKind(k)}
              style={{ fontSize: 11, padding: "2px 9px", background: kind === k ? "var(--chip)" : "transparent" }}>
              {label}
            </button>
          ))}
        </div>
      )}
      {err && <div style={{ fontSize: 12, color: "var(--red)", marginBottom: 8 }}>{err}</div>}
      {!results && tab === "trending" ? (
        <RankedTrending busy={busy} onCreate={(c) => create({ tmdb_id: c.tmdb_id, media_type: c.media_type, title: c.title } as StudioTitle)} />
      ) : (
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10, maxHeight: 420, overflowY: "auto" }}>
        {list.map((t, i) => (
          <React.Fragment key={`${t.media_type}-${t.tmdb_id}`}>
          {byMonth && monthOf(t.date) !== monthOf(list[i - 1]?.date) && (
            <div style={{ gridColumn: "1 / -1", fontSize: 12, fontWeight: 700, marginTop: i ? 8 : 0, borderBottom: "1px solid var(--border)", paddingBottom: 4 }}>
              {monthLabel(monthOf(t.date))}
            </div>
          )}
          <div style={{ background: "var(--row)", borderRadius: 8, padding: 8, display: "flex", flexDirection: "column", gap: 4 }}>
            {t.poster ? (
              <img src={t.poster} alt="" style={{ width: "100%", borderRadius: 6, aspectRatio: "2/3", objectFit: "cover" }} />
            ) : (
              <div style={{ width: "100%", aspectRatio: "2/3", background: "var(--chip)", borderRadius: 6 }} />
            )}
            <b style={{ fontSize: 12 }}>{t.title}</b>
            <span style={muted}>{t.media_type === "tv" ? "TV" : "Movie"} · {fmtDate(t.date)}</span>
            {t.release && (
              <span style={{ ...muted, color: t.release === "digital" ? "#93c5fd" : t.release === "limited" ? "#fcd34d" : "#86efac" }}
                title={t.in_theaters_since ? `Already in US theaters since ${fmtDate(t.in_theaters_since)}` : undefined}>
                {t.release === "theaters" ? "🎬 In theaters" : t.release === "limited" ? "🎬 Limited release" : "📺 Digital / streaming"}
                {t.in_theaters_since ? ` · in theaters since ${fmtDate(t.in_theaters_since)}` : ""}
              </span>
            )}
            <button className="primary" style={{ fontSize: 11 }} disabled={busy} onClick={() => create(t)}>
              Make breakdown
            </button>
          </div>
          </React.Fragment>
        ))}
      </div>
      )}
      {status && <div style={{ ...muted, marginTop: 10 }}>{status.attribution}</div>}
    </div>
  );
}

// --- one project ------------------------------------------------------------------
function ProjectView({ id, status, onBack }: { id: number; status: StudioStatus | null; onBack: () => void }) {
  const [p, setP] = useState<StudioProject | null>(null);
  const [err, setErr] = useState("");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  async function load() {
    try {
      setP(await api.studioProject(id));
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  // Poll while a stage runs so progress shows up without refreshing.
  useEffect(() => {
    if (p?.stage_status === "running" || p?.stage_status === "queued") {
      timer.current = setInterval(load, 3000);
      return () => {
        if (timer.current) clearInterval(timer.current);
      };
    }
  }, [p?.stage_status]);

  const [undoV, setUndoV] = useState(0);

  async function stopStage() {
    setErr("");
    try {
      await api.studioStop(id);
      try {
        const jobs = await api.jobs();
        const j = jobs.find((x) => x.scope === "studio" && Number(x.ref) === Number(id) && ["running", "stopping"].includes(x.status));
        if (j) await api.stopJob(j.id);
      } catch {}
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      await load();
    }
  }

  async function run(stage: string, auto = false, until: "script" | "plan" | "render" = "script") {
    setErr("");
    try {
      await api.studioRun(id, stage, auto, until);
      await load();
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  if (!p) return <div style={card}>{err || "Loading…"}</div>;
  const running = p.stage_status === "running" || p.stage_status === "queued";
  const f = p.facts || {};

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ ...card, display: "flex", gap: 14, alignItems: "flex-start", flexWrap: "wrap" }}>
        <button onClick={onBack} style={{ fontSize: 11 }}>← All videos</button>
        {p.poster && <img src={p.poster.replace("/original/", "/w154/")} alt="" style={{ width: 70, borderRadius: 6 }} />}
        <div style={{ flex: 1, minWidth: 240 }}>
          <h1 style={{ margin: 0, fontSize: 18 }}>{p.title || `TMDB ${p.tmdb_id}`}</h1>
          <div style={muted}>
            {p.media_type === "tv" ? "TV" : "Movie"} · Studio #{p.id} · target{" "}
            <select
              value={p.target_minutes}
              disabled={running}
              onChange={async (e) => setP(await api.studioPatch(id, { target_minutes: Number(e.target.value) }))}
              style={{ width: "auto", padding: "1px 4px", fontSize: 11 }}
            >
              {[2, 2.5, 3, 3.5, 4].map((m) => <option key={m} value={m}>{m} min</option>)}
            </select>
          </div>
          <div style={{ marginTop: 6, fontSize: 12, color: p.stage_status === "error" ? "var(--red)" : running ? "var(--yellow)" : p.stage_status === "stopped" || p.stage_status === "paused" ? "#f59e0b" : "var(--text)" }}>
            {running ? "⏳ " : p.stage_status === "error" ? "✕ " : p.stage_status === "stopped" ? "■ " : p.stage_status === "paused" ? "⏸ " : p.stage_status === "done" ? "✓ " : ""}
            {p.stage_message || "Not started"}
          </div>
          {err && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 4 }}>{err}</div>}
        </div>
        {running && (
          <StopButton immediate style={{ fontSize: 12 }} what="Stop all activity immediately; finished steps are kept" onStop={stopStage} />
        )}
        <UndoButton
          scope={`studio:${id}`}
          version={`${undoV}-${p.updated_at}`}
          onUndone={() => {
            setUndoV((v) => v + 1);
            load();
          }}
        />
        <button
          className="danger"
          style={{ fontSize: 11 }}
          disabled={running}
          onClick={async () => {
            if (await deleteProject(p)) onBack();
          }}
        >
          Delete
        </button>
      </div>

      {/* Stage stepper */}
      <div style={{ ...card, display: "flex", gap: 8, flexWrap: "wrap" }}>
        {STAGES.map((s) => {
          const done = p.has[s.key === "gather" ? "facts" : s.key === "trailer" ? "trailer" : s.key] ?? false;
          const active = p.stage === s.key;
          return (
            <div
              key={s.key}
              title={s.hint}
              style={{
                flex: "1 1 130px",
                padding: 8,
                borderRadius: 8,
                border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                background: done ? "rgba(16,185,129,0.08)" : "var(--row)",
              }}
            >
              <div style={{ fontSize: 12, fontWeight: 700 }}>{done ? "✓ " : ""}{s.label}</div>
              <div style={{ ...muted, minHeight: 28 }}>{s.hint}</div>
              <div style={{ display: "flex", gap: 4, marginTop: 4, flexWrap: "wrap" }}>
                <button style={{ fontSize: 10, padding: "2px 8px" }} disabled={running} onClick={() => run(s.key)}
                        title={done ? "Run just this step again" : "Run just this step"}>
                  {done ? "Re-run" : "Run"}
                </button>
                {done && s.key !== "render" && s.key !== "plan" && (
                  <button style={{ fontSize: 10, padding: "2px 8px" }} disabled={running}
                          title="Redo this step and every step after it, up to step 5 (Voice + shots)"
                          onClick={async () => {
                            if (await askConfirm(`Redo "${s.label}" and the steps after it up to step 5? Later steps are rebuilt from the new result (an undo point is kept for the script and plan).`))
                              run(s.key, true, "plan");
                          }}>
                    ↻ from here
                  </button>
                )}
              </div>
            </div>
          );
        })}
        <div style={{ flex: "1 1 100%", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button className="primary" style={{ fontSize: 11 }} disabled={running} onClick={() => run("gather", true)}>
            Run research → script
          </button>
          {(() => {
            const order = ["gather", "trailer", "shots", "script", "plan"];
            const hasKey = (k: string) => p.has[k === "gather" ? "facts" : k];
            const next = order.find((k) => !hasKey(k));
            return (
              <button style={{ fontSize: 11 }} disabled={running || !next}
                      title={next ? `Runs ${order.slice(order.indexOf(next)).join(" → ")}, then stops before render` : "Steps 1–5 are done"}
                      onClick={() => next && run(next, true, "plan")}>
                {next ? `▶ Continue to step 5 (from ${STAGES.find((x) => x.key === next)?.label})` : "✓ Steps 1–5 done"}
              </button>
            );
          })()}
          <button style={{ fontSize: 11 }} disabled={running || !p.has.script} onClick={() => run("plan")}>
            Voice + shots
          </button>
          <button style={{ fontSize: 11 }} disabled={running || !p.has.plan} onClick={() => run("render")}>
            Render video
          </button>
          <span style={muted}>Research → script runs on its own; review the script before voicing and rendering.</span>
        </div>
      </div>

      <FactsSection p={p} />
      <FootageSection p={p} onChange={setP} running={running} />
      <ScriptSection p={p} onChange={setP} running={running} />
      <PlanSection p={p} onChange={setP} running={running} />
      <VideoSection p={p} onChange={load} />
    </div>
  );
}

function Section({ title, children, open = true }: { title: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details open={open} style={card}>
      <summary style={{ cursor: "pointer", fontSize: 14, fontWeight: 700 }}>{title}</summary>
      <div style={{ marginTop: 10 }}>{children}</div>
    </details>
  );
}

function FactsSection({ p }: { p: StudioProject }) {
  const f = p.facts;
  if (!f) return <Section title="Research">Run step 1 to gather facts and sources.</Section>;
  const r = p.research;
  return (
    <Section title="Research — facts & sources" open={!p.script}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 14, fontSize: 12 }}>
        <div>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>From TMDB</div>
          {f.tagline && <div style={{ fontStyle: "italic" }}>{f.tagline}</div>}
          <div style={{ margin: "4px 0" }}>{f.overview}</div>
          <div><b>Release:</b> {(f.releases || []).map((x: any) => `${fmtDate(x.date)} (${x.type}, ${x.country})`).join(" · ") || fmtDate(f.primary_date)}</div>
          <div><b>Directed by:</b> {(f.directors || []).join(", ") || "—"}</div>
          <div><b>Studio:</b> {[...(f.studios || []), ...(f.networks || [])].join(", ") || "—"}</div>
          <div style={{ marginTop: 6 }}>
            <b>Cast:</b>
            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
              {(f.cast || []).slice(0, 8).map((c: any) => <li key={c.actor}>{c.actor} as {c.character || "?"}</li>)}
            </ul>
          </div>
          <a href={f.source} target="_blank" rel="noreferrer" style={{ fontSize: 11 }}>TMDB page ↗</a>
        </div>
        <div>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>From the web (each line has its sources)</div>
          {r?.claims?.length ? (
            <ul style={{ margin: 0, paddingLeft: 16 }}>
              {r.claims.map((c, i) => (
                <li key={i} style={{ marginBottom: 4 }}>
                  {c.text}{" "}
                  {c.sources.map((k) => r.sources[k] && (
                    <a key={k} href={r.sources[k].url} target="_blank" rel="noreferrer" style={{ fontSize: 10, marginLeft: 4 }}>
                      [{r.sources[k].title}]
                    </a>
                  ))}
                </li>
              ))}
            </ul>
          ) : (
            <div style={muted}>{r?.text || "No web findings."}</div>
          )}
        </div>
      </div>
    </Section>
  );
}

function FootageSection({ p, onChange, running }: { p: StudioProject; onChange: (p: StudioProject) => void; running: boolean }) {
  const [upErr, setUpErr] = useState("");
  const [view, setView] = useState<string | null>(null);
  const t = p.trailer;
  const shots = p.shots || [];
  const usable = shots.filter((s) => s.usable).length;
  return (
    <Section title={`Footage & shots${shots.length ? ` — ${shots.length} shots, ${usable} usable` : ""}`} open={!p.script}>
      <div style={{ fontSize: 12, marginBottom: 8 }}>
        {t?.sources?.length ? (
          <ul style={{ margin: 0, paddingLeft: 16 }}>
            {t.sources.map((v: any) => (
              <li key={v.id}>
                {v.type}: {v.name} {v.seconds ? `(${v.seconds}s)` : ""} · {v.origin}{" "}
                {v.page && <a href={v.page} target="_blank" rel="noreferrer">source ↗</a>}
              </li>
            ))}
          </ul>
        ) : t?.pending_clip_id ? (
          <div style={{ color: "var(--yellow)" }}>
            Waiting for the home Mac worker to download the YouTube trailer (clip #{t.pending_clip_id}). Run step 2 again once it arrives.
          </div>
        ) : (
          <div style={muted}>No footage yet.</div>
        )}
      </div>
      <label style={{ fontSize: 11 }}>
        Or upload a trailer file yourself:{" "}
        <input
          type="file"
          accept="video/*"
          disabled={running}
          style={{ width: "auto" }}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setUpErr("");
            try {
              onChange(await api.studioUploadTrailer(p.id, file));
            } catch (er: any) {
              setUpErr(er.message || String(er));
            }
          }}
        />
      </label>
      {upErr && <div style={{ fontSize: 11, color: "var(--red)" }}>{upErr}</div>}
      {shots.length > 0 && (
        <>
          <div style={{ ...muted, marginTop: 10 }}>Tap a shot to see it large and choose whether the video may use it, including ones marked unusable.</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 6, marginTop: 6, maxHeight: 420, overflowY: "auto" }}>
            {shots.map((s) => <ShotThumb key={s.id} p={p} s={s} onClick={() => setView(s.id)} />)}
          </div>
        </>
      )}
      {view && (
        <ShotLightbox p={p} shotId={view} running={running} onClose={() => setView(null)} onChange={onChange}
          onStep={(d) => {
            const i = shots.findIndex((x) => x.id === view);
            const n = shots[(i + d + shots.length) % shots.length];
            if (n) setView(n.id);
          }} />
      )}
    </Section>
  );
}

/** Why the tagger left a shot out, in words. */
function whyUnusable(s: StudioShot): string {
  if (s.card) return "title or credits card";
  if (s.tag_error) return "couldn't be tagged";
  if ((s as any).owner_set) return "left out by you";
  return "untagged";
}

/** Big view of one shot: the still, the moving clip, its tags, and the use / leave-out switch. */
function ShotLightbox({ p, shotId, running, onClose, onChange, onStep }: {
  p: StudioProject; shotId: string; running: boolean; onClose: () => void;
  onChange: (p: StudioProject) => void; onStep: (d: number) => void;
}) {
  const s = (p.shots || []).find((x) => x.id === shotId);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [mode, setMode] = useState<"still" | "clip">("still");
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") onStep(1);
      if (e.key === "ArrowLeft") onStep(-1);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose, onStep]);
  if (!s) return null;
  const clipPath = s.file ? `footage/${s.file.split("/").pop()}` : null;

  async function toggle() {
    setBusy(true);
    setErr("");
    try {
      onChange(await api.studioSetShot(p.id, s!.id, !s!.usable));
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div onClick={onClose}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.85)", zIndex: 120, display: "flex", alignItems: "center", justifyContent: "center", padding: 12 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...card, maxWidth: 1000, width: "100%", maxHeight: "92vh", overflowY: "auto", padding: 12 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
          <b>{s.id}</b>
          <span style={muted}>{fmtTime(s.start)}–{fmtTime(s.end)} of {s.source_type || "trailer"}</span>
          <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => setMode(mode === "still" ? "clip" : "still")} disabled={!clipPath}>
              {mode === "still" ? "▶ Play clip" : "🖼 Still"}
            </button>
            <button style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => onStep(-1)}>‹ Prev</button>
            <button style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => onStep(1)}>Next ›</button>
            <button style={{ fontSize: 11, padding: "3px 10px" }} onClick={onClose}>✕</button>
          </span>
        </div>
        {mode === "clip" && clipPath ? (
          <video key={s.id} controls autoPlay playsInline
            src={`${api.studioFileUrl(p.id, clipPath)}#t=${s.start.toFixed(2)},${s.end.toFixed(2)}`}
            style={{ width: "100%", borderRadius: 6, background: "black" }} />
        ) : (
          <img src={api.studioFileUrl(p.id, s.still)} alt="" style={{ width: "100%", borderRadius: 6, display: "block" }} />
        )}
        <div style={{ fontSize: 12, marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
          {s.description && <div>{s.description}</div>}
          <div style={muted}>
            {s.people?.length ? `On screen: ${s.people.join(", ")}` : "No one recognised"}
            {s.setting ? ` · ${s.setting}` : ""}{s.mood ? ` · ${s.mood}` : ""}{s.size ? ` · ${s.size} shot` : ""}
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 4 }}>
            <span style={{ color: s.usable ? "var(--accent)" : "var(--yellow)" }}>
              {s.usable ? "✓ The video may use this shot" : `✕ Left out (${whyUnusable(s)})`}
              {s.owner_set ? " · your choice" : ""}
            </span>
            <button className={s.usable ? "" : "primary"} disabled={busy || running} onClick={toggle} style={{ fontSize: 12 }}>
              {busy ? "Saving…" : s.usable ? "Leave this shot out" : "Use this shot"}
            </button>
            {running && <span style={muted}>wait for the running step to finish</span>}
          </div>
          {!s.usable && <div style={muted}>Using it makes it available to step 5 (Voice + shots) and to the swap picker; re-run step 5 or swap it in by hand.</div>}
          {err && <div style={{ color: "var(--red)" }}>{err}</div>}
        </div>
      </div>
    </div>
  );
}

function ShotThumb({ p, s, selected, onClick, note }: { p: StudioProject; s: StudioShot; selected?: boolean; onClick?: () => void; note?: string }) {
  return (
    <div
      onClick={onClick}
      title={`${s.id} · ${s.description || s.tag_error || ""}${s.people?.length ? ` · ${s.people.join(", ")}` : ""}`}
      style={{
        cursor: onClick ? "pointer" : "default",
        opacity: s.usable ? 1 : 0.35,
        border: selected ? "2px solid var(--accent)" : "1px solid var(--border)",
        borderRadius: 6,
        overflow: "hidden",
        background: "var(--row)",
      }}
    >
      <img src={api.studioFileUrl(p.id, s.thumb)} alt="" style={{ width: "100%", display: "block" }} loading="lazy" />
      <div style={{ fontSize: 9, padding: "2px 4px", lineHeight: 1.3 }}>
        <b>{s.id}</b> {s.people?.join(", ")}
        {!s.usable && <span style={{ color: "var(--red)" }}> {s.card ? "card" : (s as any).owner_set ? "left out" : s.tag_error ? "untagged" : "not usable"}</span>}
        {note && <div style={{ color: "var(--yellow)" }}>{note}</div>}
      </div>
    </div>
  );
}

// Script text <-> sentences: one sentence per line, a blank line between paragraphs.
function toText(sentences: { paragraph: number; text: string }[]): string {
  let out = "";
  let prev: number | null = null;
  for (const s of sentences) {
    if (prev !== null && s.paragraph !== prev) out += "\n";
    out += s.text + "\n";
    prev = s.paragraph;
  }
  return out.trim();
}

function fromText(text: string) {
  const out: { paragraph: number; text: string }[] = [];
  text.split(/\n\s*\n/).forEach((para, i) => {
    para.split("\n").map((l) => l.trim()).filter(Boolean).forEach((l) => out.push({ paragraph: i + 1, text: l }));
  });
  return out;
}

function ScriptSection({ p, onChange, running }: { p: StudioProject; onChange: (p: StudioProject) => void; running: boolean }) {
  const sc = p.script;
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [tags, setTags] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!sc) return;
    setText(toText(sc.sentences));
    setTitle(sc.youtube_title || "");
    setDesc(sc.description || "");
    setTags((sc.tags || []).join(", "));
  }, [p.id, sc?.sentences?.length, sc?.youtube_title]);

  if (!sc) return <Section title="Script">Run steps 1–4 to write the script.</Section>;
  const words = text.split(/\s+/).filter(Boolean).length;

  async function save() {
    setMsg("");
    try {
      const sentences = fromText(text);
      const next = { ...sc, sentences, youtube_title: title, description: desc,
                     tags: tags.split(",").map((t) => t.trim()).filter(Boolean), word_count: words };
      onChange(await api.studioPatch(p.id, { script: next }));
      setMsg("✓ Saved. If the words changed, run Voice + shots again.");
    } catch (e: any) {
      setMsg(`✕ ${e.message || e}`);
    }
  }

  return (
    <Section title={`Script — ${sc.sentences.length} sentences, ${words} words (~${fmtTime(words / 3)})`}>
      {!!sc.changes?.length && (
        <div style={{ fontSize: 11, marginBottom: 10, padding: 8, background: "var(--row)", borderRadius: 6 }}>
          <b>Fact-check changed {sc.changes.length} sentence(s):</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
            {sc.changes.map((c, i) => (
              <li key={i}>
                <s style={{ color: "var(--muted)" }}>{c.original}</s> → {c.fix || <i>removed</i>}{" "}
                <span style={muted}>({c.reason})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div style={muted}>One sentence per line; a blank line starts a new paragraph. Paragraph 1 names the title, paragraph 2 gives the release date.</div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={16} style={{ width: "100%", marginTop: 6, fontSize: 13, lineHeight: 1.5 }} disabled={running} />
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8, marginTop: 10 }}>
        <label style={{ fontSize: 11 }}>YouTube title
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={100} style={{ width: "100%" }} disabled={running} />
        </label>
        <label style={{ fontSize: 11 }}>Description (keep the sources and the TMDB line)
          <textarea value={desc} onChange={(e) => setDesc(e.target.value)} rows={6} style={{ width: "100%" }} disabled={running} />
        </label>
        <label style={{ fontSize: 11 }}>Tags (comma separated)
          <input value={tags} onChange={(e) => setTags(e.target.value)} style={{ width: "100%" }} disabled={running} />
        </label>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10 }}>
        <button className="primary" onClick={save} disabled={running}>Save script</button>
        {msg && <span style={{ fontSize: 11, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>{msg}</span>}
      </div>
    </Section>
  );
}

function PlanSection({ p, onChange, running }: { p: StudioProject; onChange: (p: StudioProject) => void; running: boolean }) {
  const [pick, setPick] = useState<number | null>(null);
  const plan = p.plan;
  const shots = useMemo(() => Object.fromEntries((p.shots || []).map((s) => [s.id, s])), [p.shots]);
  if (!plan) return <Section title="Voice + shot plan">Run step 5 after you are happy with the script.</Section>;

  async function setSlot(idx: number, change: Partial<StudioPlanItem>) {
    const next = plan!.map((it, i) => (i === idx ? { ...it, ...change } : it));
    onChange(await api.studioPatch(p.id, { plan: next }));
  }

  const total = plan.length ? plan[plan.length - 1].end : 0;
  // QA: the same picture (or a look-alike) within 6 visuals, or used again at all.
  const issues = useMemo(() => {
    const out: Record<number, string> = {};
    const seen: Record<string, number> = {};
    plan.forEach((it, idx) => {
      if (!it.shot || it.kind === "poster") return;
      const look = shots[it.shot]?.look || it.shot;
      if (look in seen) {
        const gap = idx - seen[look];
        const at = fmtTime(plan[seen[look]].start);
        out[idx] = gap === 1 ? "⚠ Same picture as the one before" : gap <= 6 ? `⚠ Same picture shown at ${at}` : `Repeat of ${at}`;
      }
      seen[look] = idx;
    });
    return out;
  }, [plan, shots]);
  const nIssues = Object.keys(issues).length;
  const usedAt = useMemo(() => {
    const m: Record<string, string> = {};
    plan.forEach((it) => { if (it.shot) m[shots[it.shot]?.look || it.shot] = fmtTime(it.start); });
    return m;
  }, [plan, shots]);
  return (
    <Section title={`Voice + shot plan — ${plan.length} visuals, ${fmtTime(total)} of narration`}>
      <div style={{ ...muted, marginBottom: 6 }}>
        Click a picture to swap it. Re-render after changes.{" "}
        <span style={{ color: nIssues ? "var(--yellow)" : "var(--accent)" }}>
          {nIssues ? `QA: ${nIssues} repeated picture${nIssues === 1 ? "" : "s"} — swap the flagged ones` : "QA: no repeated pictures"}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 520, overflowY: "auto" }}>
        {plan.map((it, idx) => {
          const sh = it.shot ? shots[it.shot] : undefined;
          return (
            <div key={it.slot} style={{ display: "flex", gap: 10, alignItems: "center", padding: 4, background: idx % 2 ? "transparent" : "var(--row)", borderRadius: 6 }}>
              <span className="mono" style={{ ...muted, width: 42 }}>{fmtTime(it.start)}</span>
              <div style={{ width: 110, flexShrink: 0, cursor: running ? "default" : "pointer" }} onClick={() => !running && setPick(idx)}>
                {it.kind === "poster" ? (
                  <div style={{ fontSize: 10, padding: 10, background: "var(--chip)", borderRadius: 6, textAlign: "center" }}>Poster card</div>
                ) : sh ? (
                  <img src={api.studioFileUrl(p.id, sh.thumb)} alt="" style={{ width: "100%", borderRadius: 4 }} />
                ) : (
                  <div style={{ fontSize: 10 }}>?</div>
                )}
              </div>
              <select
                value={it.kind}
                disabled={running}
                onChange={(e) => setSlot(idx, { kind: e.target.value as StudioPlanItem["kind"] })}
                style={{ width: "auto", fontSize: 11, padding: "2px 4px" }}
              >
                <option value="still">still</option>
                <option value="clip">clip</option>
                <option value="poster">poster</option>
              </select>
              <span style={{ fontSize: 11, flex: 1 }}>
                {it.text}
                {issues[idx] && <div style={{ color: issues[idx].startsWith("⚠") ? "var(--yellow)" : "var(--muted)", marginTop: 2 }}>{issues[idx]}</div>}
              </span>
            </div>
          );
        })}
      </div>
      {pick !== null && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }} onClick={() => setPick(null)}>
          <div style={{ ...card, maxWidth: 900, width: "100%", maxHeight: "85vh", overflowY: "auto" }} onClick={(e) => e.stopPropagation()}>
            <h2 style={h2}>Pick a shot for: “{plan[pick].text}”</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 6 }}>
              {[...(p.shots || [])].sort((a, b) => Number(!!b.usable) - Number(!!a.usable)).map((s) => (
                <ShotThumb key={s.id} p={p} s={s} selected={s.id === plan[pick].shot}
                  note={s.id !== plan[pick].shot && usedAt[s.look || s.id] ? `used at ${usedAt[s.look || s.id]}` : undefined}
                  onClick={async () => {
                    await setSlot(pick, { shot: s.id, kind: plan[pick].kind === "poster" ? "still" : plan[pick].kind, clip_start: s.start });
                    setPick(null);
                  }} />
              ))}
            </div>
            <button style={{ marginTop: 10 }} onClick={() => setPick(null)}>Cancel</button>
          </div>
        </div>
      )}
    </Section>
  );
}

/** Where the Drive copy ("LongForm Studio" folder) stands, with Save/Retry. */
function DriveStatus({ p, onChange }: { p: StudioProject; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const d = p.drive;
  const stale = d?.status === "saved" && d.rendered_at !== p.render?.rendered_at;
  const uploading = busy || d?.status === "uploading";

  async function saveNow() {
    setErr("");
    setBusy(true);
    try {
      const { job_id } = await api.studioSaveToDrive(p.id);
      onChange();
      await api.waitJob(job_id).catch(() => {});
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
      onChange();
    }
  }

  return (
    <div style={{ fontSize: 11, marginTop: 10, padding: 8, background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6 }}>
      <b>☁️ Drive (LongForm Studio folder): </b>
      {uploading ? (
        <span style={{ color: "var(--yellow)" }}>⏳ uploading… (Stop is in the bar at the top)</span>
      ) : d?.status === "saved" ? (
        <>
          <span style={{ color: stale ? "var(--yellow)" : "var(--accent)" }}>{stale ? "⚠ older render saved" : "✓ saved"}</span>{" "}
          {d.link && <a href={d.link} target="_blank" rel="noreferrer">open video ↗</a>}{" "}
          {d.thumb_link && <a href={d.thumb_link} target="_blank" rel="noreferrer">thumbnail ↗</a>}
          {d.saved_at && <span style={{ color: "var(--muted)" }}> · {new Date(parseApiDate(d.saved_at)).toLocaleString()}</span>}
        </>
      ) : d?.status === "error" ? (
        <span style={{ color: "var(--red)" }}>✕ {d.error}</span>
      ) : d?.status === "stopped" ? (
        <span style={{ color: "var(--yellow)" }}>■ upload stopped</span>
      ) : (
        <span style={{ color: "var(--muted)" }}>not saved yet — saved automatically when you send it to the queue</span>
      )}
      {!uploading && (d?.status !== "saved" || stale) && (
        <button style={{ fontSize: 10, padding: "2px 8px", marginLeft: 8 }} onClick={saveNow}>
          {d?.status === "error" || d?.status === "stopped" ? "Retry" : "Save to Drive now"}
        </button>
      )}
      {err && <div style={{ color: "var(--red)", marginTop: 4 }}>{err}</div>}
    </div>
  );
}

/** Motion graphics (HyperFrames): the switch + what the last render did. */
function MotionPanel({ p }: { p: StudioProject }) {
  const [m, setM] = useState<StudioMotion | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { api.studioMotion().then(setM).catch(() => {}); }, []);
  const info = p.render?.motion;
  async function save(patch: Partial<Pick<StudioMotion, "mode" | "cast_cards">>, ask?: string) {
    if (ask && !await askConfirm(ask)) return;
    setSaving(true);
    try { setM(await api.setStudioMotion(patch)); } catch (e: any) { notify(`Could not save: ${e.message || e}`); }
    finally { setSaving(false); }
  }
  if (!m) return null;
  return (
    <div style={{ fontSize: 11, marginTop: 10, padding: 8, background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6 }}>
      <b>🎞 Motion graphics</b> (intro, release card, cast names, subscribe, end screen){" "}
      <select style={{ width: "auto", fontSize: 11, padding: "1px 6px", marginLeft: 6 }} value={m.mode} disabled={saving}
        onChange={(e) => save({ mode: e.target.value as StudioMotion["mode"] })}>
        <option value="compare">Compare — make both versions</option>
        <option value="on">On — motion version is the video</option>
        <option value="off">Off — static only</option>
      </select>
      <label style={{ marginLeft: 10 }}>
        cast name cards{" "}
        <select style={{ width: "auto", fontSize: 11, padding: "1px 6px" }} value={m.cast_cards} disabled={saving}
          onChange={(e) => save({ cast_cards: Number(e.target.value) })}>
          {[0, 1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}{n === 2 ? " (the two leads)" : ""}</option>)}
        </select>
      </label>
      <div style={{ color: "var(--muted)", marginTop: 4 }}>
        Changes apply to the next render (step 6).{" "}
        {!m.available && <span style={{ color: "var(--yellow)" }}>Not available on this server: {m.reason}. Videos use the static look.</span>}
      </div>
      {info && (
        <div style={{ marginTop: 4 }}>
          Last render: {info.pieces} motion piece{info.pieces === 1 ? "" : "s"}
          {info.cast_cards.length > 0 && <> · name cards for {info.cast_cards.join(", ")}</>}
          {info.failures.length > 0 && (
            <div style={{ color: "var(--yellow)" }}>⚠ {info.failures.length} fell back to the static look: {info.failures[0]}</div>
          )}
        </div>
      )}
      {p.render?.motion_mode === "compare" && info?.file && (
        <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
          <button className="primary" style={{ fontSize: 11 }} disabled={saving}
            onClick={() => save({ mode: "on" }, "Make the motion look the default? Re-render (step 6) to make it this video's file.")}>
            ✓ Use motion look from now on
          </button>
          <button style={{ fontSize: 11 }} disabled={saving}
            onClick={() => save({ mode: "off" }, "Keep the static look and stop making the motion version?")}>
            Keep static look
          </button>
        </div>
      )}
    </div>
  );
}

/** The 14-day archive rule: switch, day count, and a dry-run preview. */
function ArchiveRule() {
  const [a, setA] = useState<StudioArchive | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { api.studioArchive().then(setA).catch(() => {}); }, []);
  if (!a) return null;
  const due = a.projects.filter((x) => x.eligible);
  async function save(patch: { enabled?: boolean; days?: number }) {
    setBusy(true);
    try { setA(await api.setStudioArchive(patch)); } catch (e: any) { setMsg(String(e.message || e)); } finally { setBusy(false); }
  }
  return (
    <div style={{ ...card, fontSize: 12, padding: "10px 14px" }}>
      <span>📦 <b>Archive finished breakdowns</b> </span>
      <label style={{ marginLeft: 6 }}>
        <input type="checkbox" checked={a.settings.enabled} disabled={busy} onChange={(e) => save({ enabled: e.target.checked })} /> on
      </label>
      <span style={muted}> · </span>
      <select style={{ width: "auto", fontSize: 11, padding: "1px 6px" }} value={a.settings.days} disabled={busy}
        onChange={(e) => save({ days: Number(e.target.value) })}>
        {[7, 14, 30, 60, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
      </select>
      <span style={muted}> after posting, once the Drive copy is confirmed. Deletes footage, stills and render files (the video stays in Drive; script, plan and thumbnails are kept). </span>
      <button style={{ fontSize: 10, padding: "1px 8px" }} onClick={() => setOpen(!open)}>
        {open ? "Hide preview" : `Preview${due.length ? ` (${due.length} due, ${a.would_free_mb} MB)` : ""}`}
      </button>
      {open && (
        <div style={{ marginTop: 6 }}>
          {a.projects.length === 0 ? <div style={muted}>No breakdowns yet.</div> : a.projects.map((x) => (
            <div key={x.id} style={{ color: x.eligible ? "var(--yellow)" : "var(--muted)" }}>
              {x.title}: {x.archived_at ? `archived ${new Date(parseApiDate(x.archived_at)).toLocaleDateString()}` : x.eligible ? `due — frees ${x.frees_mb} MB` : `not yet (${x.reason})`}
            </div>
          ))}
          {due.length > 0 && (
            <button style={{ fontSize: 11, marginTop: 6 }} disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  const r = await api.studioArchiveNow();
                  setA(r);
                  setMsg(`Archived ${r.archived.length} breakdown(s).`);
                } catch (e: any) { setMsg(String(e.message || e)); } finally { setBusy(false); }
              }}>
              Archive the due ones now
            </button>
          )}
          {msg && <div style={{ marginTop: 4 }}>{msg}</div>}
        </div>
      )}
    </div>
  );
}

/** Shown instead of the player once a breakdown is archived. */
function ArchivedNotice({ p, onChange }: { p: StudioProject; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const a = p.archive!;
  return (
    <Section title="Video — archived">
      <div style={{ fontSize: 12, display: "flex", flexDirection: "column", gap: 6 }}>
        <div>
          📦 Archived {a.archived_at ? new Date(parseApiDate(a.archived_at)).toLocaleDateString() : ""} to save space
          {a.freed_mb ? ` (freed ${a.freed_mb} MB)` : ""}. The finished video is in Drive
          {p.drive?.link && <> — <a href={p.drive.link} target="_blank" rel="noreferrer">open it ↗</a></>}.
        </div>
        <div style={muted}>To edit and re-render, restore the footage first: the same trailers are re-downloaded and the stills rebuilt (no AI cost).</div>
        <div>
          <button className="primary" disabled={busy} onClick={async () => {
            setBusy(true); setErr("");
            try {
              const { job_id } = await api.studioRestore(p.id);
              await api.waitJob(job_id).catch((e) => setErr(String(e?.message || e)));
            } catch (e: any) { setErr(String(e.message || e)); } finally { setBusy(false); onChange(); }
          }}>
            {busy ? "Restoring footage…" : "Restore footage"}
          </button>
        </div>
        {err && <div style={{ color: "var(--red)" }}>{err}</div>}
      </div>
    </Section>
  );
}

function VideoSection({ p, onChange }: { p: StudioProject; onChange: () => void }) {
  const r = p.render;
  const [msg, setMsg] = useState("");
  const [thumbBusy, setThumbBusy] = useState(false);
  if (p.archive?.archived_at) return <ArchivedNotice p={p} onChange={onChange} />;
  if (!r) return <Section title="Video">Run step 6 to render the video.</Section>;
  const bust = String(parseApiDate(r.rendered_at) || "");
  // Selecting / refreshing thumbnails rewrites the files without a new render.
  const thumbBust = `${bust}-${parseApiDate(r.thumbnails_updated_at || "") || ""}`;
  return (
    <Section title={`Video — ${fmtTime(r.seconds)}, ${Math.round(r.size / 1e6)} MB`}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14 }}>
        <div>
          {r.motion_mode === "compare" && r.motion?.file && <div style={{ fontSize: 11, marginBottom: 4 }}>Static look (current video)</div>}
          <video controls src={api.studioFileUrl(p.id, r.file, bust)} style={{ width: "100%", borderRadius: 8, background: "black" }} />
          {r.motion_mode === "compare" && r.motion?.file && (
            <>
              <div style={{ fontSize: 11, margin: "10px 0 4px" }}>🎞 Motion graphics look</div>
              <video controls src={api.studioFileUrl(p.id, r.motion.file, bust)} style={{ width: "100%", borderRadius: 8, background: "black" }} />
            </>
          )}
        </div>
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <span style={{ fontSize: 12, fontWeight: 700 }}>Thumbnail Options (3 choices)</span>
            <button
              style={{ fontSize: 10, padding: "2px 6px" }}
              disabled={thumbBusy}
              onClick={async () => {
                setMsg("");
                setThumbBusy(true);
                try {
                  await api.studioGenerateThumbnails(p.id);
                  setMsg("✓ New options — showing the next-best shots");
                  onChange();
                } catch (e: any) {
                  setMsg(`✕ ${e.message || e}`);
                } finally {
                  setThumbBusy(false);
                }
              }}
              title="Show the next-best close-up and scene shots (the poster stays)"
            >
              {thumbBusy ? "⏳ Refreshing…" : "🔄 Refresh Options"}
            </button>
          </div>

          {/* Active Thumbnail Preview */}
          <div style={{ marginBottom: 10 }}>
            <img src={api.studioFileUrl(p.id, r.thumbnail, thumbBust)} alt="Active Thumbnail" style={{ width: "100%", borderRadius: 6, border: "2px solid var(--accent)", boxShadow: "0 4px 12px rgba(0,0,0,0.3)" }} />
            <div style={{ fontSize: 10, color: "var(--accent)", marginTop: 4, fontWeight: 600 }}>
              ✓ Current Active Thumbnail (used for download, queue &amp; Drive)
            </div>
          </div>

          {/* 3 Selectable Thumbnails Grid */}
          {r.thumbnails && r.thumbnails.length > 0 ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginTop: 8 }}>
              {r.thumbnails.map((t) => {
                const isSelected = (r.selected_thumbnail === t.id) || (r.thumbnail?.endsWith(t.file) ?? false);
                return (
                  <div
                    key={t.id}
                    onClick={async () => {
                      setMsg("");
                      try {
                        await api.studioSelectThumbnail(p.id, t.id);
                        onChange();
                      } catch (e: any) {
                        setMsg(`✕ ${e.message || e}`);
                      }
                    }}
                    style={{
                      cursor: "pointer",
                      border: isSelected ? "2px solid var(--accent)" : "1px solid var(--border)",
                      background: isSelected ? "rgba(52, 211, 153, 0.12)" : "var(--row)",
                      borderRadius: 6,
                      padding: 6,
                      display: "flex",
                      flexDirection: "column",
                      gap: 4,
                      transition: "all 0.15s ease",
                    }}
                  >
                    <img
                      src={api.studioFileUrl(p.id, t.file, thumbBust)}
                      alt={t.label}
                      style={{ width: "100%", aspectRatio: "16/9", objectFit: "cover", borderRadius: 4 }}
                    />
                    <div style={{ fontSize: 11, fontWeight: 700, color: isSelected ? "var(--accent)" : "var(--text)" }}>
                      {t.id === "poster" ? "🪧 " : t.id === "shot1" ? "👤 " : "🎬 "}
                      {t.label}
                    </div>
                    <div style={{ fontSize: 9, color: "var(--muted)", lineHeight: 1.2 }}>{t.desc}</div>
                    <div style={{
                      marginTop: "auto",
                      fontSize: 10,
                      fontWeight: 600,
                      textAlign: "center",
                      padding: "2px 4px",
                      borderRadius: 4,
                      background: isSelected ? "var(--accent)" : "rgba(255, 255, 255, 0.08)",
                      color: isSelected ? "#064e3b" : "var(--text)",
                    }}>
                      {isSelected ? "✓ Active" : "Select"}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <button
              style={{ fontSize: 11, padding: "6px 12px", width: "100%", marginTop: 4 }}
              onClick={async () => {
                setMsg("");
                try {
                  await api.studioGenerateThumbnails(p.id);
                  onChange();
                } catch (e: any) {
                  setMsg(`✕ ${e.message || e}`);
                }
              }}
            >
              ✨ Generate 3 Thumbnail Options (Poster, Close-Up, Scene Still)
            </button>
          )}
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <a href={api.studioFileUrl(p.id, r.file, bust)} download>
              <button>⬇ Download</button>
            </a>
            <button
              className="primary"
              onClick={async () => {
                setMsg("");
                try {
                  const res = await api.studioPublish(p.id);
                  setMsg(`✓ In SocialPilot → LongForm Breakdowns as #${res.queue_item_id} (Needs Review). Pick where it posts and approve it there.`
                    + (res.drive_job_id ? " Saving a copy to Drive…" : ""));
                  onChange();
                  if (res.drive_job_id) api.waitJob(res.drive_job_id).catch(() => {}).finally(onChange);
                } catch (e: any) {
                  setMsg(`✕ ${e.message || e}`);
                }
              }}
            >
              {p.queue_item_id ? "Update in Posting Queue" : "Send to Posting Queue"}
            </button>
          </div>
          {msg && <div style={{ fontSize: 11, marginTop: 6, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>{msg}</div>}
          <DriveStatus p={p} onChange={onChange} />
          <MotionPanel p={p} />
          {!!p.cost_usd && (
            <div style={{ fontSize: 11, marginTop: 8, color: "var(--muted)" }}>
              This video has cost about <b style={{ color: "var(--text)" }}>${p.cost_usd.toFixed(p.cost_usd < 1 ? 3 : 2)}</b> so far (AI + voice-over).
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}
