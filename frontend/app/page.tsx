"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  Clip,
  Compilation,
  expiresIn,
  fmtBytes,
  fmtDuration,
  LogLine,
  Stats,
} from "@/lib/api";
import { PublishModal } from "./PublishModal";
import { QueuePanel } from "./QueuePanel";
import { DrivePanel } from "./DrivePanel";

type NavTab = "scraper" | "socialpilot" | "comps" | "logs" | "settings";

export default function Page() {
  const [clips, setClips] = useState<Clip[]>([]);
  const [comps, setComps] = useState<Compilation[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [queueCount, setQueueCount] = useState<number>(0);
  const [activeTab, setActiveTab] = useState<NavTab>("scraper");
  const [socialPilotTab, setSocialPilotTab] = useState<"queue" | "drive">("queue");
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(false);

  const refresh = useCallback(async () => {
    try {
      const [c, cm, st, q] = await Promise.all([
        api.clips(),
        api.compilations(),
        api.stats(),
        api.queue().catch(() => []),
      ]);
      setClips(c);
      setComps(cm);
      setStats(st);
      setQueueCount(q.length);
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

  // Sync hash with active tab if set in URL
  useEffect(() => {
    if (typeof window !== "undefined") {
      const hash = window.location.hash.replace("#", "");
      if (["scraper", "socialpilot", "comps", "logs", "settings"].includes(hash)) {
        setActiveTab(hash as NavTab);
      } else if (hash === "queue") {
        setActiveTab("socialpilot");
        setSocialPilotTab("queue");
      } else if (hash === "drive") {
        setActiveTab("socialpilot");
        setSocialPilotTab("drive");
      }
    }
  }, []);

  function handleTabSelect(tab: NavTab) {
    setActiveTab(tab);
    if (typeof window !== "undefined") {
      window.location.hash = tab;
    }
  }

  // Live log stream via SSE
  useEffect(() => {
    const es = new EventSource(api.eventsUrl());
    es.onmessage = (e) => {
      try {
        const line = JSON.parse(e.data) as LogLine;
        if (line && line.message) setLogs((prev) => [...prev.slice(-300), line]);
      } catch {}
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, []);

  const readyQueueCount = queueCount;
  const doneClipsCount = clips.filter((c) => c.status === "done").length;

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bg)" }}>
      {/* ─── REACTIVE SIDEBAR ────────────────────────────────────── */}
      <aside
        style={{
          width: sidebarCollapsed ? 76 : 280,
          transition: "width 0.2s cubic-bezier(0.4, 0, 0.2, 1)",
          background: "#0c0e14",
          borderRight: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          position: "sticky",
          top: 0,
          height: "100vh",
          zIndex: 100,
          flexShrink: 0,
        }}
      >
        {/* Brand Header */}
        <div
          style={{
            padding: sidebarCollapsed ? "18px 12px" : "18px 20px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: sidebarCollapsed ? "center" : "space-between",
          }}
        >
          {!sidebarCollapsed ? (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 18 }}>🎬</span>
                <span style={{ fontWeight: 800, fontSize: 16, letterSpacing: "-0.02em", color: "#f8fafc" }}>
                  Scrapper
                </span>
                <span
                  style={{
                    fontSize: 10,
                    fontWeight: 700,
                    color: "var(--accent)",
                    background: "#064e3b",
                    padding: "2px 6px",
                    borderRadius: 8,
                    letterSpacing: "0.05em",
                  }}
                >
                  STUDIO
                </span>
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
                Social Media Pipeline & Queue
              </div>
            </div>
          ) : (
            <span style={{ fontSize: 22 }}>🎬</span>
          )}

          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            style={{
              padding: 6,
              background: "transparent",
              border: "1px solid var(--border)",
              color: "var(--muted)",
              borderRadius: 6,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            {sidebarCollapsed ? "→" : "←"}
          </button>
        </div>

        {/* Navigation Items */}
        <nav style={{ padding: "16px 10px", display: "flex", flexDirection: "column", gap: 6, flex: 1, overflowY: "auto" }}>
          {/* 1. Scraper (Original Landing Page) */}
          <NavButton
            active={activeTab === "scraper"}
            collapsed={sidebarCollapsed}
            onClick={() => handleTabSelect("scraper")}
            icon="📥"
            title="Scraper (Home)"
            subtitle="Paste link, scrape & download"
            badge={`${doneClipsCount} ready`}
            badgeColor={doneClipsCount > 0 ? "var(--accent)" : "var(--muted)"}
            badgeBg={doneClipsCount > 0 ? "#064e3b" : "#1e293b"}
          />

          {/* 2. SocialPilot AI (Scheduler & Channel Distribution) */}
          <NavButton
            active={activeTab === "socialpilot"}
            collapsed={sidebarCollapsed}
            onClick={() => handleTabSelect("socialpilot")}
            icon="🚀"
            title="SocialPilot AI"
            subtitle="Queue, Drive & Scheduler"
            badge={`${readyQueueCount} items`}
            badgeColor={readyQueueCount > 0 ? "var(--accent)" : "var(--muted)"}
            badgeBg={readyQueueCount > 0 ? "#064e3b" : "#1e293b"}
          />

          {/* 3. Compilations & Stitching */}
          <NavButton
            active={activeTab === "comps"}
            collapsed={sidebarCollapsed}
            onClick={() => handleTabSelect("comps")}
            icon="🎞️"
            title="Compilations & Exports"
            subtitle="Multi-clip renders"
            badge={`${comps.length} comps`}
            badgeColor="#fbbf24"
            badgeBg="#78350f"
          />

          {/* 4. Live System Logs */}
          <NavButton
            active={activeTab === "logs"}
            collapsed={sidebarCollapsed}
            onClick={() => handleTabSelect("logs")}
            icon="📜"
            title="Live Activity Logs"
            subtitle="Real-time SSE worker feed"
          />

          {/* 5. Settings & API Credentials */}
          <NavButton
            active={activeTab === "settings"}
            collapsed={sidebarCollapsed}
            onClick={() => handleTabSelect("settings")}
            icon="⚙️"
            title="Settings & Channels"
            subtitle="Outstand & API credentials"
          />
        </nav>

        {/* Sidebar Footer: Health & Storage Meter */}
        <div
          style={{
            padding: sidebarCollapsed ? 12 : 16,
            borderTop: "1px solid var(--border)",
            background: "#080a0e",
            fontSize: 11,
          }}
        >
          {!sidebarCollapsed ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ color: "var(--muted)" }}>Volume Storage</span>
                <span className="mono" style={{ color: "#e6e9ef", fontWeight: 600 }}>
                  {fmtBytes(stats?.storage_bytes ?? 0)} / 5 GB
                </span>
              </div>
              <div
                style={{
                  height: 4,
                  width: "100%",
                  background: "#1e222b",
                  borderRadius: 2,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "100%",
                    width: `${Math.min(100, Math.round(((stats?.storage_bytes ?? 0) / (5 * 1024 * 1024 * 1024)) * 100))}%`,
                    background: "var(--accent)",
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 2 }}>
                <span style={{ color: connected ? "var(--accent)" : "var(--red)", display: "flex", alignItems: "center", gap: 5 }}>
                  <span>{connected ? "●" : "○"}</span> {connected ? "Railway Live" : "Disconnected"}
                </span>
                <span style={{ color: "var(--muted)" }}>v2.4 Outstand</span>
              </div>
            </div>
          ) : (
            <div style={{ textAlign: "center", color: connected ? "var(--accent)" : "var(--red)" }}>
              ●
            </div>
          )}
        </div>
      </aside>

      {/* ─── MAIN CONTENT VIEWPORT ───────────────────────────────── */}
      <main style={{ flex: 1, minWidth: 0, padding: "0 24px 60px", overflowY: "auto" }}>
        <StatusBar stats={stats} connected={connected} clips={clips} comps={comps} />

        {/* Dedicated Tab 1: POSTING QUEUE (Google Sheets Experience) */}
        {/* Landing Page: Original Scrapper (Paste Link -> Scrape -> Download to Device) */}
        {activeTab === "scraper" && (
          <div>
            <SettingsBar onSaved={refresh} />
            <IngestPanel onIngested={refresh} />
            <Storyboard clips={clips} onChange={refresh} retentionDays={stats?.retention_days ?? 0} />
            <LogsPanel logs={logs} />
          </div>
        )}

        {/* Dedicated System: SocialPilot AI (Scheduler & Channel Distribution) */}
        {activeTab === "socialpilot" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {/* SocialPilot Header & Sub-Navigation */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                background: "var(--panel)",
                border: "1px solid var(--border)",
                borderRadius: 10,
                padding: "16px 20px",
                flexWrap: "wrap",
                gap: 12,
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 20 }}>🚀</span>
                  <h1 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: "#f8fafc" }}>
                    SocialPilot AI Studio
                  </h1>
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      color: "#34d399",
                      background: "#064e3b",
                      padding: "2px 8px",
                      borderRadius: 10,
                      letterSpacing: "0.04em",
                    }}
                  >
                    EXEMPT FROM 5-DAY RETENTION
                  </span>
                </div>
                <p style={{ margin: "4px 0 0", color: "var(--muted)", fontSize: 12 }}>
                  Multi-channel distribution & scheduler • Permanent Google Drive storage • Outstand posting
                </p>
              </div>

              {/* Sub-view switcher */}
              <div style={{ display: "flex", gap: 8, background: "var(--bg)", padding: 4, borderRadius: 8, border: "1px solid var(--border)" }}>
                <button
                  onClick={() => setSocialPilotTab("queue")}
                  style={{
                    background: socialPilotTab === "queue" ? "#064e3b" : "transparent",
                    color: socialPilotTab === "queue" ? "#34d399" : "var(--text)",
                    border: socialPilotTab === "queue" ? "1px solid var(--accent)" : "1px solid transparent",
                    fontWeight: 600,
                    padding: "6px 14px",
                    borderRadius: 6,
                  }}
                >
                  📋 Posting Queue ({readyQueueCount})
                </button>
                <button
                  onClick={() => setSocialPilotTab("drive")}
                  style={{
                    background: socialPilotTab === "drive" ? "#4c1d95" : "transparent",
                    color: socialPilotTab === "drive" ? "#a78bfa" : "var(--text)",
                    border: socialPilotTab === "drive" ? "1px solid #7c3aed" : "1px solid transparent",
                    fontWeight: 600,
                    padding: "6px 14px",
                    borderRadius: 6,
                  }}
                >
                  ☁️ Google Drive Ingestion
                </button>
              </div>
            </div>

            {socialPilotTab === "queue" ? (
              <QueuePanel onChange={refresh} />
            ) : (
              <DrivePanel onIngested={refresh} />
            )}
          </div>
        )}

        {/* Separate View 4: Live Activity Logs */}
        {activeTab === "logs" && (
          <div>
            <LogsPanel logs={logs} />
          </div>
        )}

        {/* Separate View 5: Settings & Credentials */}
        {activeTab === "settings" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            <SettingsBar onSaved={refresh} />
          </div>
        )}
      </main>
    </div>
  );
}

/* ------------------------------------------------------------------ */
function NavButton({
  active,
  collapsed,
  onClick,
  icon,
  title,
  subtitle,
  badge,
  badgeColor,
  badgeBg,
}: {
  active: boolean;
  collapsed: boolean;
  onClick: () => void;
  icon: string;
  title: string;
  subtitle?: string;
  badge?: string;
  badgeColor?: string;
  badgeBg?: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: collapsed ? "12px 0" : "10px 14px",
        justifyContent: collapsed ? "center" : "flex-start",
        width: "100%",
        textAlign: "left",
        borderRadius: 8,
        border: active ? "1px solid #334155" : "1px solid transparent",
        background: active ? "#1e293b" : "transparent",
        color: active ? "#f8fafc" : "#94a3b8",
        cursor: "pointer",
        transition: "all 0.15s ease",
        position: "relative",
      }}
      title={collapsed ? title : undefined}
    >
      <span style={{ fontSize: 18, lineHeight: 1, flexShrink: 0 }}>{icon}</span>

      {!collapsed && (
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
            <span style={{ fontWeight: active ? 700 : 500, fontSize: 13, color: active ? "#fff" : "inherit" }}>
              {title}
            </span>
            {badge && (
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: badgeColor || "#fff",
                  background: badgeBg || "#334155",
                  padding: "1px 6px",
                  borderRadius: 10,
                  whiteSpace: "nowrap",
                }}
              >
                {badge}
              </span>
            )}
          </div>
          {subtitle && (
            <span style={{ fontSize: 11, color: "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {subtitle}
            </span>
          )}
        </div>
      )}

      {/* Active accent pill on left */}
      {active && (
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 6,
            bottom: 6,
            width: 3,
            background: "var(--accent)",
            borderRadius: "0 3px 3px 0",
          }}
        />
      )}
    </button>
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
  const pending = clips.filter((c) => c.status === "failed" && c.error?.includes("Mac worker")).length;
  const actualFailed = Math.max(0, (stats?.clips_failed ?? 0) - pending);
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
      {pending > 0 && <Metric label="pending" value={pending} />}
      <Metric label="failed" value={actualFailed} danger={actualFailed > 0} />
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

  async function rescan() {
    try {
      const r = await api.rescan();
      alert(`Rescan complete — recovered ${r.clips_added} clip(s) and ${r.compilations_added} compilation(s) from disk.`);
      onSaved();
    } catch (e: any) {
      alert(`Rescan failed: ${e.message}`);
    }
  }

  return (
    <div style={{ marginBottom: 12, textAlign: "right", display: "flex", gap: 8, justifyContent: "flex-end" }}>
      <button onClick={rescan} title="Rebuild the library from video files on the server (recovers anything missing)">
        ↻ Rescan library
      </button>
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

function Storyboard({ clips, onChange, retentionDays }: { clips: Clip[]; onChange: () => void; retentionDays: number }) {
  const [orientation, setOrientation] = useState("portrait");
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const selectedForCompile = clips.filter((c) => c.selected && c.status === "done").length;
  const selectedIds = clips.filter((c) => c.selected).map((c) => c.id);
  const allChecked = clips.length > 0 && selectedIds.length === clips.length;

  async function toggle(c: Clip) {
    await api.select(c.id, !c.selected).catch(() => {});
    onChange();
  }
  async function toggleAll(check: boolean) {
    await Promise.all(
      clips.filter((c) => c.selected !== check).map((c) => api.select(c.id, check).catch(() => {}))
    );
    onChange();
  }
  async function del(c: Clip) {
    if (!confirm(`Delete "${c.title || c.source_url}"? Removes the downloaded file too.`)) return;
    await api.deleteClip(c.id).catch(() => {});
    onChange();
  }
  async function deleteSelected() {
    if (selectedIds.length === 0) return;
    if (!confirm(`Delete ${selectedIds.length} selected clip(s)? This removes the downloaded files too.`)) return;
    setDeleting(true);
    try {
      await Promise.all(selectedIds.map((id) => api.deleteClip(id).catch(() => {})));
      onChange();
    } finally {
      setDeleting(false);
    }
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
          <button className="primary" onClick={extract} disabled={busy || selectedForCompile < 1}>
            {busy ? "Queuing…" : `🎬 Extract ${selectedForCompile} → 1 video`}
          </button>
          <button className="danger" onClick={deleteSelected} disabled={deleting || selectedIds.length < 1}>
            {deleting ? "Deleting…" : `🗑 Delete ${selectedIds.length}`}
          </button>
        </div>
      }
    >
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: "var(--muted)", textAlign: "left" }}>
              <Th w={36}>
                <input
                  type="checkbox"
                  checked={allChecked}
                  onChange={(e) => toggleAll(e.target.checked)}
                  title="select all"
                  style={{ width: 16, height: 16, cursor: "pointer" }}
                />
              </Th>
              <Th w={40}>#</Th>
              <Th w={90}>thumb</Th>
              <Th w={70}>platform</Th>
              <Th>uploader / title</Th>
              <Th w={70}>duration</Th>
              <Th w={90}>resolution</Th>
              <Th w={70}>size</Th>
              <Th w={80}>status</Th>
              <Th w={70}>expires</Th>
              <Th w={180}>Actions</Th>
            </tr>
          </thead>
          <tbody>
            {clips.length === 0 && (
              <tr>
                <td colSpan={11} style={{ padding: 24, textAlign: "center", color: "var(--muted)" }}>
                  No clips yet — paste links above and hit Scrape.
                </td>
              </tr>
            )}
            {clips.map((c, i) => {
              const isWaitingForMac = c.status === "failed" && Boolean(c.error?.includes("Mac worker"));
              return (
              <tr
                key={c.id}
                style={{
                  background: i % 2 ? "var(--row-alt)" : "var(--row)",
                  borderTop: "1px solid var(--border)",
                  opacity: (c.status === "failed" && !isWaitingForMac) ? 0.6 : 1,
                }}
              >
                <Td>
                  <input
                    type="checkbox"
                    checked={c.selected}
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
                  <div style={{ color: isWaitingForMac ? "var(--yellow)" : "var(--muted)", maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {c.title || c.error || <a href={c.source_url} target="_blank" rel="noreferrer">{c.source_url}</a>}
                  </div>
                </Td>
                <Td>{fmtDuration(c.duration)}</Td>
                <Td>{c.width && c.height ? `${c.width}×${c.height}` : "—"}</Td>
                <Td>{fmtBytes(c.size_bytes)}</Td>
                <Td>
                  {isWaitingForMac ? (
                    <span style={{ color: "var(--yellow)" }}>● pending (mac)</span>
                  ) : (
                    <span style={{ color: statusColor(c.status) }}>● {c.status}</span>
                  )}
                </Td>
                <Td>
                  <ExpiryTag createdAt={c.created_at} retentionDays={retentionDays} />
                </Td>
                <Td>
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    {c.status === "done" && (
                      <a href={api.clipDownloadUrl(c.id)} download>
                        <button
                          className="primary"
                          style={{
                            padding: "4px 9px",
                            fontSize: 11,
                            fontWeight: 600,
                            display: "flex",
                            alignItems: "center",
                            gap: 4,
                          }}
                          title="Download video directly to your device"
                        >
                          ⬇ Download
                        </button>
                      </a>
                    )}
                    {c.status === "done" && (
                      <button
                        style={{ padding: "4px 7px", fontSize: 11, background: "var(--chip)", color: "var(--text)" }}
                        onClick={async () => {
                          await api.createQueueItem({ clip_id: c.id, pipeline: "Movie Clips", status: "review" });
                          onChange();
                          alert(`Added Clip #${c.id} to Posting Queue!`);
                        }}
                        title="Add this clip to the Posting Queue"
                      >
                        + Queue
                      </button>
                    )}
                    <button className="danger" onClick={() => del(c)} style={{ padding: "4px 7px", fontSize: 11 }} title="Delete clip">
                      ✕
                    </button>
                  </div>
                </Td>
              </tr>
              );
            })}
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

/** Today's lines show just the time; older ones carry a date so daily
 *  entries (like the nightly engine check) stop looking like duplicates. */
function fmtLogTime(iso: string): string {
  const d = new Date(iso);
  const time = d.toLocaleTimeString();
  const isToday = d.toDateString() === new Date().toDateString();
  return isToday ? time : `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${time}`;
}

/** Countdown to the retention sweep. Goes amber in the last day. */
function ExpiryTag({ createdAt, retentionDays }: { createdAt: string; retentionDays: number }) {
  const left = expiresIn(createdAt, retentionDays);
  if (!left) return <span style={{ color: "var(--muted)" }}>—</span>;
  const urgent = left === "due" || left.endsWith("h");
  return (
    <span
      title={`Auto-deleted ${retentionDays} days after it was added`}
      style={{ color: urgent ? "var(--yellow)" : "var(--muted)" }}
    >
      {left}
    </span>
  );
}

/* ------------------------------------------------------------------ */
function ExportPanel({ comps, onChange, retentionDays }: { comps: Compilation[]; onChange: () => void; retentionDays: number }) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [deleting, setDeleting] = useState(false);
  const [publishComp, setPublishComp] = useState<Compilation | null>(null);

  const validIds = comps.map((c) => c.id);
  const selectedIds = [...sel].filter((id) => validIds.includes(id));
  const allChecked = comps.length > 0 && selectedIds.length === comps.length;

  function toggle(id: number) {
    setSel((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }
  function toggleAll(check: boolean) {
    setSel(check ? new Set(validIds) : new Set());
  }
  async function del(c: Compilation) {
    if (!confirm("Delete this compilation file?")) return;
    await api.deleteCompilation(c.id).catch(() => {});
    onChange();
  }
  async function deleteSelected() {
    if (selectedIds.length === 0) return;
    if (!confirm(`Delete ${selectedIds.length} selected compilation(s)?`)) return;
    setDeleting(true);
    try {
      await Promise.all(selectedIds.map((id) => api.deleteCompilation(id).catch(() => {})));
      setSel(new Set());
      onChange();
    } finally {
      setDeleting(false);
    }
  }
  return (
    <Panel
      title={`Export — ${comps.length} compilation(s)`}
      right={
        comps.length > 0 ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <label style={{ color: "var(--muted)", display: "flex", gap: 6, alignItems: "center", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={allChecked}
                onChange={(e) => toggleAll(e.target.checked)}
                style={{ width: 15, height: 15, cursor: "pointer" }}
              />
              select all
            </label>
            <button className="danger" onClick={deleteSelected} disabled={deleting || selectedIds.length < 1}>
              {deleting ? "Deleting…" : `🗑 Delete ${selectedIds.length}`}
            </button>
          </div>
        ) : null
      }
    >
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
            <input
              type="checkbox"
              checked={sel.has(c.id)}
              onChange={() => toggle(c.id)}
              style={{ width: 16, height: 16, cursor: "pointer" }}
            />
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
            <span style={{ color: "var(--muted)" }}>
              expires <ExpiryTag createdAt={c.created_at} retentionDays={retentionDays} />
            </span>
            {c.error && <span style={{ color: "var(--red)" }}>{c.error}</span>}
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              {c.status === "done" && (
                <button
                  className="primary"
                  style={{ background: "#7c3aed", borderColor: "#6d28d9", color: "#ffffff" }}
                  onClick={() => setPublishComp(c)}
                >
                  🚀 Post / Schedule
                </button>
              )}
              {c.status === "done" && (
                <button
                  style={{ background: "#1e293b", borderColor: "#334155", color: "#38bdf8" }}
                  onClick={async () => {
                    await api.createQueueItem({ compilation_id: c.id, pipeline: "Movie Clips", status: "review" });
                    onChange();
                    alert(`Added Compilation #${c.id} to Posting Queue!`);
                  }}
                  title="Add to Posting Queue"
                >
                  + Queue
                </button>
              )}
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
      {publishComp && (
        <PublishModal
          compilation={publishComp}
          onClose={() => setPublishComp(null)}
          onSuccess={onChange}
        />
      )}
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
            <span style={{ color: "var(--muted)" }}>{fmtLogTime(l.created_at)} </span>
            <span style={{ color: color(l.level) }}>[{l.event}] </span>
            <span style={{ color: color(l.level) }}>{l.message}</span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
