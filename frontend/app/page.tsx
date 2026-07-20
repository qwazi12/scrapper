"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  apiBase,
  Clip,
  Compilation,
  fmtBytes,
  fmtDuration,
  LogLine,
  Stats,
  token,
} from "@/lib/api";

export default function Page() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [comps, setComps] = useState<Compilation[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState<boolean | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [c, cm, st] = await Promise.all([api.clips(), api.compilations(), api.stats()]);
      setClips(c);
      setComps(cm);
      setStats(st);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  // Poll for state; also seed the logs list.
  useEffect(() => {
    refresh();
    api.logs(100).then(setLogs).catch(() => {});
    const id = setInterval(refresh, 2500);
    return () => clearInterval(id);
  }, [refresh]);

  // Live log stream via SSE.
  useEffect(() => {
    const t = token();
    const url = `${apiBase()}/api/events${t ? `?` : ``}`;
    const es = new EventSource(url + (t ? "" : ""));
    es.onmessage = (e) => {
      try {
        const line = JSON.parse(e.data) as LogLine;
        if (line && line.message) setLogs((prev) => [...prev.slice(-300), line]);
      } catch {}
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, []);

  return (
    <main style={{ maxWidth: 1500, margin: "0 auto", padding: "0 16px 60px" }}>
      <StatusBar stats={stats} connected={connected} clips={clips} comps={comps} />
      <SettingsBar onSaved={refresh} />
      <IngestPanel onIngested={refresh} />
      <Storyboard clips={clips} onChange={refresh} />
      <ExportPanel comps={comps} onChange={refresh} />
      <LogsPanel logs={logs} />
    </main>
  );
}

/* ------------------------------------------------------------------ */
function StatusBar({
  stats,
  connected,
  clips,
  comps,
}: {
  stats: Stats | null;
  connected: boolean | null;
  clips: Clip[];
  comps: Compilation[];
}) {
  const selected = clips.filter((c) => c.selected && c.status === "done").length;
  const running = comps.find((c) => c.status === "running");
  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 10,
        background: "#0b0d11",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        gap: 22,
        padding: "12px 4px",
        marginBottom: 16,
        flexWrap: "wrap",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 15 }}>
        🎬 Scrapper <span style={{ color: "var(--muted)", fontWeight: 400 }}>storyboard</span>
      </div>
      <Metric label="clips" value={clips.length} />
      <Metric label="selected" value={selected} accent />
      <Metric label="done" value={stats?.clips_done ?? 0} />
      <Metric label="failed" value={stats?.clips_failed ?? 0} danger={!!stats?.clips_failed} />
      <Metric label="compilations" value={comps.length} />
      <Metric label="storage" value={fmtBytes(stats?.storage_bytes ?? 0)} />
      {running && (
        <span style={{ color: "var(--yellow)" }}>
          ⚙ compiling… {Math.round(running.progress * 100)}%
        </span>
      )}
      <div style={{ marginLeft: "auto", display: "flex", gap: 14, alignItems: "center" }}>
        {stats?.sources?.map((s) => (
          <span
            key={s.source}
            title={`${s.recent} recent scrapes`}
            style={{ color: s.alerting ? "var(--red)" : "var(--muted)", fontSize: 11 }}
          >
            {s.alerting ? "⚠ " : ""}
            {s.source}: {s.success_rate == null ? "—" : `${Math.round(s.success_rate * 100)}%`}
          </span>
        ))}
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          yt-dlp {stats?.engine?.yt_dlp ?? "?"}
        </span>
        <span
          style={{
            fontSize: 11,
            color: connected ? "var(--accent)" : "var(--red)",
          }}
        >
          {connected == null ? "…" : connected ? "● connected" : "● offline"}
        </span>
      </div>
    </header>
  );
}

function Metric({
  label,
  value,
  accent,
  danger,
}: {
  label: string;
  value: number | string;
  accent?: boolean;
  danger?: boolean;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
      <span
        style={{
          fontSize: 16,
          fontWeight: 700,
          color: danger ? "var(--red)" : accent ? "var(--accent)" : "var(--text)",
        }}
      >
        {value}
      </span>
      <span style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
        {label}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
function Panel({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section
      style={{
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        marginBottom: 16,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          padding: "10px 14px",
          background: "var(--panel2)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <h2 style={{ margin: 0, fontSize: 12, textTransform: "uppercase", letterSpacing: 0.8, color: "var(--muted)" }}>
          {title}
        </h2>
        <div style={{ marginLeft: "auto" }}>{right}</div>
      </div>
      <div style={{ padding: 14 }}>{children}</div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
function SettingsBar({ onSaved }: { onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [base, setBase] = useState("");
  const [tok, setTok] = useState("");
  const [cookieState, setCookieState] = useState<string>("");

  useEffect(() => {
    setBase(window.localStorage.getItem("scrapper_api_base") || "");
    setTok(window.localStorage.getItem("scrapper_token") || "");
    api.cookiesStatus().then((s) => setCookieState(s.present ? `${s.bytes} bytes` : "none")).catch(() => {});
  }, [open]);

  function save() {
    if (base) window.localStorage.setItem("scrapper_api_base", base);
    else window.localStorage.removeItem("scrapper_api_base");
    if (tok) window.localStorage.setItem("scrapper_token", tok);
    else window.localStorage.removeItem("scrapper_token");
    setOpen(false);
    onSaved();
  }

  async function onCookieFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const r = await api.uploadCookies(f);
      setCookieState(`${r.bytes} bytes ✓`);
    } catch (err) {
      setCookieState(`upload failed`);
    }
  }

  return (
    <div style={{ marginBottom: 12, textAlign: "right" }}>
      <button onClick={() => setOpen((o) => !o)}>⚙ Connection & cookies</button>
      {open && (
        <div
          style={{
            marginTop: 8,
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            padding: 14,
            textAlign: "left",
            display: "grid",
            gap: 10,
            maxWidth: 520,
            marginLeft: "auto",
          }}
        >
          <label style={{ color: "var(--muted)", fontSize: 11 }}>API base URL</label>
          <input value={base} onChange={(e) => setBase(e.target.value)} placeholder="http://127.0.0.1:8000" />
          <label style={{ color: "var(--muted)", fontSize: 11 }}>Access token</label>
          <input value={tok} onChange={(e) => setTok(e.target.value)} placeholder="(blank for local dev)" />
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
            <label style={{ color: "var(--muted)", fontSize: 11 }}>
              cookies.txt (Layer 2 — helps X succeed). Current: {cookieState}
            </label>
            <input type="file" accept=".txt" onChange={onCookieFile} style={{ marginTop: 6 }} />
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button onClick={() => setOpen(false)}>Cancel</button>
            <button className="primary" onClick={save}>
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
function IngestPanel({ onIngested }: { onIngested: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function scrape() {
    const urls = text
      .split("\n")
      .map((u) => u.trim())
      .filter(Boolean);
    if (!urls.length) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await api.ingest(urls);
      setMsg(`Queued ${r.count} link(s) → scraping…`);
      setText("");
      onIngested();
    } catch (e: any) {
      setMsg(`Error: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Ingest — paste links (one per line)">
      <textarea
        rows={4}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={"https://x.com/user/status/123...\nhttps://youtube.com/watch?v=...\nhttps://tiktok.com/@user/video/..."}
        style={{ resize: "vertical", fontFamily: "ui-monospace, monospace", fontSize: 12 }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
        <button className="primary" onClick={scrape} disabled={busy || !text.trim()}>
          {busy ? "Queuing…" : "⬇ Scrape"}
        </button>
        <span style={{ color: "var(--muted)" }}>{msg}</span>
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
function statusColor(s: string): string {
  return (
    {
      done: "var(--accent)",
      running: "var(--yellow)",
      queued: "var(--muted)",
      failed: "var(--red)",
      skipped: "var(--blue)",
    }[s] || "var(--muted)"
  );
}

function Storyboard({ clips, onChange }: { clips: Clip[]; onChange: () => void }) {
  const [orientation, setOrientation] = useState("portrait");
  const [busy, setBusy] = useState(false);
  const selectedCount = clips.filter((c) => c.selected && c.status === "done").length;

  async function toggle(c: Clip) {
    await api.select(c.id, !c.selected).catch(() => {});
    onChange();
  }
  async function del(c: Clip) {
    if (!confirm(`Delete "${c.title || c.source_url}"? Removes the downloaded file too.`)) return;
    await api.deleteClip(c.id).catch(() => {});
    onChange();
  }
  async function extract() {
    setBusy(true);
    try {
      await api.compile(orientation);
      onChange();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title={`Storyboard — ${clips.length} clip(s)`}
      right={
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
            {["portrait", "landscape"].map((o) => (
              <button
                key={o}
                onClick={() => setOrientation(o)}
                style={{
                  border: "none",
                  borderRadius: 0,
                  background: orientation === o ? "var(--accent-dim)" : "transparent",
                  color: orientation === o ? "#fff" : "var(--muted)",
                }}
              >
                {o}
              </button>
            ))}
          </div>
          <button className="primary" onClick={extract} disabled={busy || selectedCount < 1}>
            {busy ? "Queuing…" : `🎬 Extract ${selectedCount} → 1 video`}
          </button>
        </div>
      }
    >
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: "var(--muted)", textAlign: "left" }}>
              <Th w={36}>✓</Th>
              <Th w={40}>#</Th>
              <Th w={90}>thumb</Th>
              <Th w={70}>platform</Th>
              <Th>uploader / title</Th>
              <Th w={70}>duration</Th>
              <Th w={90}>resolution</Th>
              <Th w={70}>size</Th>
              <Th w={80}>status</Th>
              <Th w={50}></Th>
            </tr>
          </thead>
          <tbody>
            {clips.length === 0 && (
              <tr>
                <td colSpan={10} style={{ padding: 24, textAlign: "center", color: "var(--muted)" }}>
                  No clips yet — paste links above and hit Scrape.
                </td>
              </tr>
            )}
            {clips.map((c, i) => (
              <tr
                key={c.id}
                style={{
                  background: i % 2 ? "var(--row-alt)" : "var(--row)",
                  borderTop: "1px solid var(--border)",
                  opacity: c.status === "failed" ? 0.6 : 1,
                }}
              >
                <Td>
                  <input
                    type="checkbox"
                    checked={c.selected}
                    disabled={c.status !== "done"}
                    onChange={() => toggle(c)}
                    style={{ width: 16, height: 16, cursor: "pointer" }}
                  />
                </Td>
                <Td>{i + 1}</Td>
                <Td>
                  {c.has_thumb ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={api.thumbUrl(c.id)}
                      alt=""
                      style={{ width: 72, height: 48, objectFit: "cover", borderRadius: 4, background: "#000" }}
                    />
                  ) : (
                    <div style={{ width: 72, height: 48, borderRadius: 4, background: "#000" }} />
                  )}
                </Td>
                <Td>
                  <span style={{ padding: "2px 7px", background: "var(--chip)", borderRadius: 4, fontSize: 11 }}>
                    {c.platform || "?"}
                  </span>
                </Td>
                <Td>
                  <div style={{ fontWeight: 600 }}>{c.uploader || "—"}</div>
                  <div style={{ color: "var(--muted)", maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {c.title || c.error || <a href={c.source_url} target="_blank" rel="noreferrer">{c.source_url}</a>}
                  </div>
                </Td>
                <Td>{fmtDuration(c.duration)}</Td>
                <Td>{c.width && c.height ? `${c.width}×${c.height}` : "—"}</Td>
                <Td>{fmtBytes(c.size_bytes)}</Td>
                <Td>
                  <span style={{ color: statusColor(c.status) }}>● {c.status}</span>
                </Td>
                <Td>
                  <button className="danger" onClick={() => del(c)} style={{ padding: "3px 7px" }}>
                    ✕
                  </button>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function Th({ children, w }: { children?: React.ReactNode; w?: number }) {
  return (
    <th style={{ padding: "8px 10px", fontWeight: 600, fontSize: 11, width: w, textTransform: "uppercase", letterSpacing: 0.4 }}>
      {children}
    </th>
  );
}
function Td({ children }: { children?: React.ReactNode }) {
  return <td style={{ padding: "8px 10px", verticalAlign: "middle" }}>{children}</td>;
}

/* ------------------------------------------------------------------ */
function ExportPanel({ comps, onChange }: { comps: Compilation[]; onChange: () => void }) {
  async function del(c: Compilation) {
    if (!confirm("Delete this compilation file?")) return;
    await api.deleteCompilation(c.id).catch(() => {});
    onChange();
  }
  return (
    <Panel title={`Export — ${comps.length} compilation(s)`}>
      {comps.length === 0 && (
        <div style={{ color: "var(--muted)", padding: "8px 0" }}>
          No compilations yet — select clips above and hit Extract.
        </div>
      )}
      <div style={{ display: "grid", gap: 8 }}>
        {comps.map((c) => (
          <div
            key={c.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 14,
              padding: "10px 12px",
              background: "var(--row)",
              border: "1px solid var(--border)",
              borderRadius: 8,
            }}
          >
            <span style={{ fontWeight: 700 }}>#{c.id}</span>
            <span style={{ padding: "2px 7px", background: "var(--chip)", borderRadius: 4, fontSize: 11 }}>
              {c.orientation}
            </span>
            <span style={{ color: "var(--muted)" }}>{c.clip_ids.length} clips</span>
            <span style={{ color: "var(--muted)" }}>{fmtDuration(c.duration)}</span>
            <span style={{ color: "var(--muted)" }}>{fmtBytes(c.size_bytes)}</span>
            <span style={{ color: statusColor(c.status) }}>
              ● {c.status}
              {c.status === "running" ? ` ${Math.round(c.progress * 100)}%` : ""}
            </span>
            {c.error && <span style={{ color: "var(--red)" }}>{c.error}</span>}
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              {c.status === "done" && (
                <a href={api.downloadUrl(c.id)}>
                  <button className="primary">⬇ Download</button>
                </a>
              )}
              <button className="danger" onClick={() => del(c)}>
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
function LogsPanel({ logs }: { logs: LogLine[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [logs]);
  const color = (lvl: string) =>
    ({ error: "var(--red)", warning: "var(--yellow)", info: "var(--text)", debug: "var(--muted)" }[lvl] || "var(--text)");
  return (
    <Panel title="Logs — live">
      <div
        ref={ref}
        style={{
          height: 240,
          overflowY: "auto",
          background: "#0a0c10",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: 10,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: 11.5,
          lineHeight: 1.6,
        }}
      >
        {logs.length === 0 && <div style={{ color: "var(--muted)" }}>waiting for activity…</div>}
        {logs.map((l, i) => (
          <div key={i}>
            <span style={{ color: "var(--muted)" }}>{new Date(l.created_at).toLocaleTimeString()} </span>
            <span style={{ color: color(l.level) }}>[{l.event}] </span>
            <span style={{ color: color(l.level) }}>{l.message}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
