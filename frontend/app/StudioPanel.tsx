"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  parseApiDate,
  StudioPlanItem,
  StudioProject,
  StudioShot,
  StudioStatus,
  StudioTitle,
} from "../lib/api";

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
      {err && <div style={{ ...card, color: "var(--red)", fontSize: 12 }}>{err}</div>}

      <NewVideo
        status={status}
        onCreated={(p) => {
          setOpenId(p.id);
        }}
      />

      <div style={card}>
        <h2 style={h2}>Your videos ({projects.length})</h2>
        {projects.length === 0 ? (
          <div style={muted}>Nothing yet. Pick a title above to start one.</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
            {projects.map((p) => (
              <button
                key={p.id}
                onClick={() => setOpenId(p.id)}
                style={{ display: "flex", gap: 10, textAlign: "left", padding: 8, background: "var(--row)", alignItems: "center" }}
              >
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
                  {p.queue_item_id && <span style={{ ...muted, color: "var(--accent)" }}>in Posting Queue #{p.queue_item_id}</span>}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>
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
    p.stage_status === "error" ? "var(--red)" : p.stage_status === "running" ? "var(--yellow)" : "var(--muted)";
  const label = STAGES.find((s) => s.key === p.stage)?.label || p.stage;
  return (
    <span style={{ fontSize: 10, color }}>
      {p.stage_status === "running" ? "⏳ " : p.stage_status === "error" ? "✕ " : ""}
      {p.stage === "new" ? "not started" : `${label} — ${p.stage_status}`}
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
      const p = await api.studioCreate({ tmdb_id: t.tmdb_id, media_type: t.media_type, title: t.title });
      await api.studioRun(p.id, "gather", true); // research through script, then the owner reviews
      onCreated(p);
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  const list = results ?? cal?.[tab] ?? [];
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
            ["trending", "Trending today"],
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
      {err && <div style={{ fontSize: 12, color: "var(--red)", marginBottom: 8 }}>{err}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 10, maxHeight: 420, overflowY: "auto" }}>
        {list.map((t) => (
          <div key={`${t.media_type}-${t.tmdb_id}`} style={{ background: "var(--row)", borderRadius: 8, padding: 8, display: "flex", flexDirection: "column", gap: 4 }}>
            {t.poster ? (
              <img src={t.poster} alt="" style={{ width: "100%", borderRadius: 6, aspectRatio: "2/3", objectFit: "cover" }} />
            ) : (
              <div style={{ width: "100%", aspectRatio: "2/3", background: "var(--chip)", borderRadius: 6 }} />
            )}
            <b style={{ fontSize: 12 }}>{t.title}</b>
            <span style={muted}>{t.media_type === "tv" ? "TV" : "Movie"} · {fmtDate(t.date)}</span>
            <button className="primary" style={{ fontSize: 11 }} disabled={busy} onClick={() => create(t)}>
              Make breakdown
            </button>
          </div>
        ))}
      </div>
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
    if (p?.stage_status === "running") {
      timer.current = setInterval(load, 3000);
      return () => {
        if (timer.current) clearInterval(timer.current);
      };
    }
  }, [p?.stage_status]);

  async function run(stage: string, auto = false) {
    setErr("");
    try {
      await api.studioRun(id, stage, auto);
      await load();
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  if (!p) return <div style={card}>{err || "Loading…"}</div>;
  const running = p.stage_status === "running";
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
          <div style={{ marginTop: 6, fontSize: 12, color: p.stage_status === "error" ? "var(--red)" : running ? "var(--yellow)" : "var(--text)" }}>
            {running ? "⏳ " : p.stage_status === "error" ? "✕ " : p.stage_status === "done" ? "✓ " : ""}
            {p.stage_message || "Not started"}
          </div>
          {err && <div style={{ fontSize: 12, color: "var(--red)", marginTop: 4 }}>{err}</div>}
        </div>
        <button
          className="danger"
          style={{ fontSize: 11 }}
          disabled={running}
          onClick={async () => {
            if (!confirm(`Delete "${p.title}" and all its files? This cannot be undone.`)) return;
            await api.studioDelete(id);
            onBack();
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
              <button style={{ fontSize: 10, padding: "2px 8px", marginTop: 4 }} disabled={running} onClick={() => run(s.key)}>
                {done ? "Re-run" : "Run"}
              </button>
            </div>
          );
        })}
        <div style={{ flex: "1 1 100%", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button className="primary" style={{ fontSize: 11 }} disabled={running} onClick={() => run("gather", true)}>
            Run research → script
          </button>
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
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 6, marginTop: 10, maxHeight: 420, overflowY: "auto" }}>
          {shots.map((s) => <ShotThumb key={s.id} p={p} s={s} />)}
        </div>
      )}
    </Section>
  );
}

function ShotThumb({ p, s, selected, onClick }: { p: StudioProject; s: StudioShot; selected?: boolean; onClick?: () => void }) {
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
        {!s.usable && <span style={{ color: "var(--red)" }}> {s.card ? "card" : s.text ? "text" : s.quality || "untagged"}</span>}
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
  return (
    <Section title={`Voice + shot plan — ${plan.length} visuals, ${fmtTime(total)} of narration`}>
      <div style={{ ...muted, marginBottom: 6 }}>Click a picture to swap it. Re-render after changes.</div>
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
              <span style={{ fontSize: 11, flex: 1 }}>{it.text}</span>
            </div>
          );
        })}
      </div>
      {pick !== null && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.75)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }} onClick={() => setPick(null)}>
          <div style={{ ...card, maxWidth: 900, width: "100%", maxHeight: "85vh", overflowY: "auto" }} onClick={(e) => e.stopPropagation()}>
            <h2 style={h2}>Pick a shot for: “{plan[pick].text}”</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))", gap: 6 }}>
              {(p.shots || []).filter((s) => s.usable).map((s) => (
                <ShotThumb key={s.id} p={p} s={s} selected={s.id === plan[pick].shot}
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

function VideoSection({ p, onChange }: { p: StudioProject; onChange: () => void }) {
  const r = p.render;
  const [msg, setMsg] = useState("");
  if (!r) return <Section title="Video">Run step 6 to render the video.</Section>;
  const bust = String(parseApiDate(r.rendered_at) || "");
  return (
    <Section title={`Video — ${fmtTime(r.seconds)}, ${Math.round(r.size / 1e6)} MB`}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 14 }}>
        <video controls src={api.studioFileUrl(p.id, r.file, bust)} style={{ width: "100%", borderRadius: 8, background: "black" }} />
        <div>
          <div style={{ fontSize: 11, marginBottom: 4 }}>Thumbnail</div>
          <img src={api.studioFileUrl(p.id, r.thumbnail, bust)} alt="" style={{ width: "100%", borderRadius: 6 }} />
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
                  setMsg(`✓ In the Posting Queue as #${res.queue_item_id} (Needs Review). Pick where it posts and approve it there.`);
                  onChange();
                } catch (e: any) {
                  setMsg(`✕ ${e.message || e}`);
                }
              }}
            >
              {p.queue_item_id ? "Update in Posting Queue" : "Send to Posting Queue"}
            </button>
          </div>
          {msg && <div style={{ fontSize: 11, marginTop: 6, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>{msg}</div>}
        </div>
      </div>
    </Section>
  );
}
