"use client";

import React, { useEffect, useState } from "react";
import { api, apiBase, BackupStatus, DiskSystemStatus, mediaUrl, parseApiDate, ScheduleConfig, ScheduleInfo, SocialAccount, TTSVoice } from "../lib/api";
import { UndoButton } from "./UndoButton";
import { SpendingCard } from "./SpendingCard";
import { PacingThrottle } from "./PacingThrottle";

function fmtHour(h: number): string {
  const ampm = h < 12 ? "am" : "pm";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}${ampm}`;
}

function fmtSlot(iso: string, tz: string): string {
  return new Date(parseApiDate(iso)).toLocaleString("en-US", {
    timeZone: tz, weekday: "short", hour: "numeric", minute: "2-digit",
  });
}

const TIMEZONES = [
  "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "America/Toronto", "Europe/London", "Europe/Paris", "Africa/Lagos", "Asia/Dubai", "UTC",
];
const INTERVALS = [1, 2, 3, 4, 6, 8, 12];

function slotHours(c: ScheduleConfig): number[] {
  const out: number[] = [];
  for (let h = c.start_hour; h <= c.end_hour; h += c.interval_hours) out.push(h);
  return out;
}

const fieldStyle: React.CSSProperties = { width: "auto", padding: "4px 8px", fontSize: 12 };

/** Edit form for posting times; saved server-side, Ready videos re-plan at once. */
function ScheduleEditor({ sched, onSaved }: { sched: ScheduleInfo; onSaved: (s: ScheduleInfo) => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<ScheduleConfig>({
    timezone: sched.timezone, start_hour: sched.start_hour, end_hour: sched.end_hour, interval_hours: sched.interval_hours,
  });
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const hours = slotHours(form);
  const invalid = form.start_hour > form.end_hour ? "First slot must be at or before the last slot" : "";
  const zones = TIMEZONES.includes(form.timezone) ? TIMEZONES : [form.timezone, ...TIMEZONES];

  async function save(reset = false) {
    const what = reset
      ? `Reset posting times to the defaults (${slotHours(sched.defaults).map(fmtHour).join(", ")} ${sched.defaults.timezone})?`
      : `Post at ${hours.map(fmtHour).join(", ")} (${form.timezone}) — ${hours.length} slots/day?`;
    if (!confirm(`${what}\n\nReady videos move onto the new slots right away.`)) return;
    setSaving(true);
    setMsg("");
    try {
      const res = await api.updateSchedule(reset ? { reset: true } : form);
      onSaved(res);
      setForm({ timezone: res.timezone, start_hour: res.start_hour, end_hour: res.end_hour, interval_hours: res.interval_hours });
      setMsg("✓ Saved — schedule updated");
      setOpen(false);
    } catch (e: any) {
      setMsg(`✕ ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <button style={{ fontSize: 11, padding: "4px 10px" }} onClick={() => setOpen(true)}>✏️ Edit posting times</button>
        {sched.customized ? (
          <span style={{ fontSize: 10, color: "var(--muted)" }}>Custom schedule (set on this page)</span>
        ) : (
          <span style={{ fontSize: 10, color: "var(--muted)" }}>Using the default schedule</span>
        )}
        {msg && <span style={{ fontSize: 11, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>{msg}</span>}
      </div>
    );
  }

  return (
    <div style={{ marginTop: 12, padding: 12, background: "var(--row)", border: "1px solid var(--border)", borderRadius: 8 }}>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end", fontSize: 11 }}>
        <label>
          <div style={{ marginBottom: 3, color: "var(--muted)" }}>First post</div>
          <select style={fieldStyle} value={form.start_hour}
            onChange={(e) => setForm({ ...form, start_hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
          </select>
        </label>
        <label>
          <div style={{ marginBottom: 3, color: "var(--muted)" }}>Last post (no later than)</div>
          <select style={fieldStyle} value={form.end_hour}
            onChange={(e) => setForm({ ...form, end_hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
          </select>
        </label>
        <label>
          <div style={{ marginBottom: 3, color: "var(--muted)" }}>Every</div>
          <select style={fieldStyle} value={form.interval_hours}
            onChange={(e) => setForm({ ...form, interval_hours: Number(e.target.value) })}>
            {INTERVALS.map((n) => <option key={n} value={n}>{n} hour{n === 1 ? "" : "s"}</option>)}
          </select>
        </label>
        <label>
          <div style={{ marginBottom: 3, color: "var(--muted)" }}>Timezone</div>
          <select style={fieldStyle} value={form.timezone}
            onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
            {zones.map((z) => <option key={z} value={z}>{z}</option>)}
          </select>
        </label>
      </div>
      <div style={{ fontSize: 11, marginTop: 10, color: invalid ? "var(--red)" : "var(--text)" }}>
        {invalid || <>Posts at <b>{hours.map(fmtHour).join(", ")}</b> — {hours.length} slot{hours.length === 1 ? "" : "s"}/day per pipeline</>}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <button className="primary" style={{ fontSize: 11 }} disabled={saving || !!invalid} onClick={() => save(false)}>
          {saving ? "Saving…" : "Save schedule"}
        </button>
        <button style={{ fontSize: 11 }} onClick={() => setOpen(false)} disabled={saving}>Cancel</button>
        {sched.customized && (
          <button style={{ fontSize: 11, marginLeft: "auto" }} onClick={() => save(true)} disabled={saving}>
            Reset to defaults
          </button>
        )}
      </div>
      {msg && <div style={{ fontSize: 11, marginTop: 6, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>{msg}</div>}
    </div>
  );
}

const card: React.CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: "16px 18px",
};

const h2: React.CSSProperties = { margin: "0 0 10px", fontSize: 14, fontWeight: 700 };

function BackupCard({ card, h2 }: { card: React.CSSProperties; h2: React.CSSProperties }) {
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [backingUp, setBackingUp] = useState(false);
  const [msg, setMsg] = useState("");

  function load() {
    api.backupStatus().then(setStatus).catch((e) => setMsg(`Could not load backups: ${e.message || e}`));
  }

  useEffect(() => {
    load();
  }, []);

  async function handleBackupNow() {
    setBackingUp(true);
    setMsg("");
    try {
      const res = await api.backupNow();
      setMsg(`✓ Backup created: ${res.filename} (${(res.compressed_bytes / 1024).toFixed(1)} KB)`);
      load();
    } catch (e: any) {
      setMsg(`✕ Backup failed: ${e.message || e}`);
    } finally {
      setBackingUp(false);
    }
  }

  return (
    <div style={card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h2 style={{ ...h2, margin: 0 }}>💾 Database Backups &amp; Safety</h2>
          {status && (
            <span
              style={{
                fontSize: 10,
                padding: "2px 8px",
                borderRadius: 8,
                fontWeight: 700,
                background: status.is_stale ? "rgba(239, 68, 68, 0.2)" : "rgba(16, 185, 129, 0.2)",
                color: status.is_stale ? "#f87171" : "#34d399",
              }}
            >
              {status.is_stale ? "⚠️ Backup Overdue" : "● Healthy"}
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={handleBackupNow}
            disabled={backingUp}
            style={{
              fontSize: 11,
              padding: "4px 12px",
              background: "#2563eb",
              color: "#fff",
              fontWeight: 600,
              border: "1px solid #1d4ed8",
            }}
          >
            {backingUp ? "⏳ Creating Snapshot…" : "💾 Backup Now"}
          </button>
        </div>
      </div>

      {msg && (
        <div style={{ fontSize: 11, marginBottom: 10, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>
          {msg}
        </div>
      )}

      {status?.warning && (
        <div style={{ fontSize: 11, background: "rgba(245, 158, 11, 0.15)", border: "1px solid #d97706", color: "#fbbf24", padding: "6px 10px", borderRadius: 6, marginBottom: 12 }}>
          ⚠️ {status.warning}
        </div>
      )}

      <p style={{ margin: "0 0 12px", color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>
        Lock-free SQLite snapshots taken nightly at <b>3:00 am Eastern</b> with automated <code>PRAGMA integrity_check</code>,
        gzip compression, <b>7-day local disk retention</b> on Railway, and <b>30-day off-disk retention</b> in Google Drive.
      </p>

      {status && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10, marginBottom: 14 }}>
          <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Database File</div>
            <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
              {status.database_type.toUpperCase()} <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>(/data/scrapper.db)</span>
            </div>
          </div>

          <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Local Retention</div>
            <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
              {status.local_copies_count} / {status.retention.keep_local} daily snapshots <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>(/data/backups/)</span>
            </div>
          </div>

          <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Google Drive Off-Disk</div>
            <div style={{ fontSize: 12, fontWeight: 600, marginTop: 2, color: status.drive_status === "synced" ? "#34d399" : "#fbbf24" }}>
              {status.drive_status === "synced"
                ? "✓ Synced (30-day retention)"
                : status.drive_status.startsWith("Local disk")
                ? "⚠️ Local disk only (Shared Drive pending)"
                : status.drive_status}
            </div>
          </div>

          <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
            <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Next Automated Snapshot</div>
            <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
              {new Date(status.next_scheduled_run).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" })} Eastern
            </div>
          </div>
        </div>
      )}

      {/* Recent Backups List */}
      {status && status.local_copies.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: "var(--muted)", marginBottom: 6, textTransform: "uppercase" }}>
            Available Snapshots for 1-Click Restore &amp; Download:
          </div>
          <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
            {status.local_copies.map((c, idx) => (
              <div
                key={c.filename}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "7px 12px",
                  fontSize: 11,
                  borderBottom: idx < status.local_copies.length - 1 ? "1px solid var(--border)" : "none",
                  gap: 10,
                  flexWrap: "wrap",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="mono" style={{ color: "#38bdf8", fontWeight: 600 }}>{c.filename}</span>
                  <span style={{ color: "var(--muted)", fontSize: 10 }}>
                    ({(c.size_bytes / 1024).toFixed(1)} KB)
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <span style={{ color: "var(--muted)" }}>
                    {new Date(c.created_at).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} Eastern
                  </span>
                  <a
                    href={mediaUrl(c.download_url)}
                    download={c.filename}
                    style={{ color: "var(--accent)", textDecoration: "underline", fontSize: 11 }}
                  >
                    ⬇ Download .db.gz
                  </a>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ marginTop: 12, padding: "8px 12px", background: "rgba(255,255,255,0.03)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 11, color: "var(--muted)" }}>
        🛠️ <b>Safe Recovery CLI:</b> To dry-run inspect or restore a backup on Railway, run:
        <code style={{ display: "block", marginTop: 4, color: "#38bdf8" }}>python scripts/restore_db.py --from /data/backups/&lt;filename&gt; --confirm</code>
      </div>
    </div>
  );
}

function DiskCard({ card, h2 }: { card: React.CSSProperties; h2: React.CSSProperties }) {
  const [disk, setDisk] = useState<DiskSystemStatus | null>(null);
  const [cleaning, setCleaning] = useState(false);
  const [msg, setMsg] = useState("");

  function load() {
    api.diskStatus().then(setDisk).catch((e) => setMsg(`Could not load disk status: ${e.message || e}`));
  }

  useEffect(() => {
    load();
  }, []);

  async function handleSweep() {
    setCleaning(true);
    setMsg("");
    try {
      const res = await api.runCleanup();
      const freedMB = ((res.caches.motion.bytes_freed + res.caches.tts.bytes_freed + res.temp_downloads.bytes_freed) / (1024 * 1024)).toFixed(1);
      setMsg(`✓ Cleanup sweep complete: freed ${freedMB} MB across temporary files and caches.`);
      setDisk(res.disk_usage);
    } catch (e: any) {
      setMsg(`✕ Cleanup sweep failed: ${e.message || e}`);
    } finally {
      setCleaning(false);
    }
  }

  const pct = disk?.percent_used ?? 0;
  const isCrit = disk?.status === "critical";
  const isWarn = disk?.status === "warning";
  const badgeColor = isCrit ? "#f87171" : isWarn ? "#fbbf24" : "#34d399";
  const badgeBg = isCrit ? "rgba(239, 68, 68, 0.2)" : isWarn ? "rgba(245, 158, 11, 0.2)" : "rgba(16, 185, 129, 0.2)";
  const badgeText = isCrit ? "🛑 Critical (Refusing New Jobs)" : isWarn ? "⚠️ High Usage" : "● Healthy";

  return (
    <div style={card}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <h2 style={{ ...h2, margin: 0 }}>💽 Disk Space &amp; Storage Retention</h2>
          {disk && (
            <span
              style={{
                fontSize: 10,
                padding: "2px 8px",
                borderRadius: 8,
                fontWeight: 700,
                background: badgeBg,
                color: badgeColor,
              }}
            >
              {badgeText}
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button
            onClick={handleSweep}
            disabled={cleaning}
            style={{
              fontSize: 11,
              padding: "4px 12px",
              background: "#334155",
              color: "#fff",
              fontWeight: 600,
              border: "1px solid #475569",
            }}
          >
            {cleaning ? "⏳ Sweeping…" : "🧹 Run Cleanup Sweep"}
          </button>
        </div>
      </div>

      {msg && (
        <div style={{ fontSize: 11, marginBottom: 10, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>
          {msg}
        </div>
      )}

      {isCrit && (
        <div style={{ fontSize: 11, background: "rgba(239, 68, 68, 0.15)", border: "1px solid #dc2626", color: "#f87171", padding: "6px 10px", borderRadius: 6, marginBottom: 12 }}>
          🛑 Volume disk usage has exceeded {disk?.max_threshold_percent}%. New breakdowns and bulk downloads are paused until space is freed.
        </div>
      )}

      <p style={{ margin: "0 0 12px", color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>
        Automated <b>5-day rolling retention sweep</b> cleans un-queued video files every 30m. Proactive <b>LRU cache caps</b> automatically evict the oldest generated assets, and intermediate render workfiles are deleted upon completion.
        {" "}<span style={{ color: "#38bdf8" }}>SocialPilot queued items are strictly exempt from retention deletion.</span>
      </p>

      {disk && (
        <>
          {/* Visual Disk Meter Bar */}
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4 }}>
              <span>Volume Utilization ({disk.used_gb} GB of {disk.total_gb} GB used)</span>
              <span style={{ fontWeight: 700, color: badgeColor }}>{pct}%</span>
            </div>
            <div style={{ height: 8, background: "rgba(255,255,255,0.08)", borderRadius: 4, overflow: "hidden", position: "relative" }}>
              <div
                style={{
                  height: "100%",
                  width: `${Math.min(pct, 100)}%`,
                  background: isCrit ? "#ef4444" : isWarn ? "#f59e0b" : "#10b981",
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: "var(--muted)", marginTop: 2 }}>
              <span>0 GB</span>
              <span>75% Warning</span>
              <span>90% Refusal Limit</span>
              <span>{disk.total_gb} GB</span>
            </div>
          </div>

          {/* Metric cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
            <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
              <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Free Volume Space</div>
              <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
                {disk.free_gb} GB free <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>({(100 - pct).toFixed(1)}% remaining)</span>
              </div>
            </div>

            <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
              <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Motion Cache (HyperFrames)</div>
              <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
                {disk.caches?.motion_cache_mb ?? 0} MB <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>/ {disk.caches?.max_motion_mb ?? 400} MB cap</span>
              </div>
            </div>

            <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
              <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Voiceover &amp; TTS Cache</div>
              <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
                {disk.caches?.tts_cache_mb ?? 0} MB <span style={{ fontSize: 10, color: "var(--muted)", fontWeight: 400 }}>/ {disk.caches?.max_tts_mb ?? 200} MB cap</span>
              </div>
            </div>

            <div style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 12px" }}>
              <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", fontWeight: 700 }}>Active Volume Mount</div>
              <div style={{ fontSize: 12, fontWeight: 500, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                <code>{disk.path}</code>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/** Read-only view of what the server is configured to do: schedule, accounts, AI. */
export function SettingsOverview() {
  const [sched, setSched] = useState<ScheduleInfo | null>(null);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [accountsMsg, setAccountsMsg] = useState<string>("");
  const [manageUrl, setManageUrl] = useState("https://app.upload-post.com/manage-users");
  const [err, setErr] = useState<string>("");
  const [aiResult, setAiResult] = useState<string>("");
  const [aiTesting, setAiTesting] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [seo, setSeo] = useState<{ auto: boolean; clips_without_seo: number } | null>(null);

  // Gemini 3.8 Flash TTS Studio State
  const [ttsVoices, setTtsVoices] = useState<TTSVoice[]>([]);
  const [ttsVoice, setTtsVoice] = useState("Puck");
  const [ttsStyle, setTtsStyle] = useState("high energy and enthusiastic");
  const [ttsModel, setTtsModel] = useState("gemini-3.8-flash-tts");
  const [ttsText, setTtsText] = useState("You will NOT believe what happened next! <gasp> Watch till the very end.");
  const [ttsAudioUrl, setTtsAudioUrl] = useState<string | null>(null);
  const [ttsDuration, setTtsDuration] = useState<number | null>(null);
  const [ttsGenerating, setTtsGenerating] = useState(false);
  const [ttsStatusMsg, setTtsStatusMsg] = useState("");
  const [ttsCheckTesting, setTtsCheckTesting] = useState(false);
  const [ttsCheckResult, setTtsCheckResult] = useState("");

  // Refresh the schedule/heartbeat every 30s so the status stays live.
  useEffect(() => {
    const t = setInterval(() => {
      setNow(Date.now());
      api.schedule().then(setSched).catch(() => {});
    }, 30000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    api.ttsVoices()
      .then((r) => {
        setTtsVoices(r.voices || []);
        if (r.default_voice) setTtsVoice(r.default_voice);
      })
      .catch(() => {});
  }, []);

  async function testAi() {
    setAiTesting(true);
    setAiResult("");
    try {
      const r = await api.aiCheck();
      setAiResult(`✓ ${r.model} replied: “${r.title}” ${(r.hashtags || []).join(" ")}`);
    } catch (e: any) {
      setAiResult(`✕ ${e.message || e}`);
    } finally {
      setAiTesting(false);
    }
  }

  async function handleTtsCheck() {
    setTtsCheckTesting(true);
    setTtsCheckResult("");
    try {
      const r = await api.ttsCheck();
      setTtsCheckResult(`✓ ${r.model} (${r.voice}, ${r.duration}s) live!`);
      setTtsAudioUrl(r.audio_url);
      setTtsDuration(r.duration);
    } catch (e: any) {
      setTtsCheckResult(`✕ ${e.message || e}`);
    } finally {
      setTtsCheckTesting(false);
    }
  }

  async function handleTtsGenerate() {
    if (!ttsText.trim()) return;
    setTtsGenerating(true);
    setTtsStatusMsg("");
    try {
      const res = await api.ttsGenerate({
        text: ttsText.trim(),
        voice: ttsVoice,
        style: ttsStyle.trim() || undefined,
        model: ttsModel,
      });
      setTtsAudioUrl(res.audio_url);
      setTtsDuration(res.duration_seconds);
      setTtsStatusMsg(`✓ Synthesized ${res.duration_seconds}s audio with ${res.voice}!`);
    } catch (e: any) {
      setTtsStatusMsg(`✕ ${e.message || e}`);
    } finally {
      setTtsGenerating(false);
    }
  }


  useEffect(() => {
    api.seo().then(setSeo).catch(() => {});
    api.schedule().then(setSched).catch((e) => setErr(String(e.message || e)));
    api.socialAccounts()
      .then((r) => {
        setAccounts(r.accounts || []);
        if (r.manage_url) setManageUrl(r.manage_url);
        if (!r.configured) setAccountsMsg(r.message || "Upload-Post is not configured");
      })
      .catch((e) => setAccountsMsg(`Could not load accounts: ${e.message || e}`));
  }, []);

  if (err) {
    return <div style={{ ...card, color: "var(--red)" }}>Could not load settings: {err}</div>;
  }
  if (!sched) {
    return <div style={{ ...card, color: "var(--muted)" }}>Loading settings…</div>;
  }

  const tzShort = sched.timezone === "America/New_York" ? "Eastern" : sched.timezone;
  const sc = sched.scheduler;
  const tickAgo = sc.last_tick_at ? Math.round((now - parseApiDate(sc.last_tick_at)) / 1000) : null;
  const alive = sc.enabled && tickAgo !== null && tickAgo < sc.tick_seconds * 3 + 30;
  const pipelines = Object.entries(sched.pipelines);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <SpendingCard card={card} h2={h2} />
      <BackupCard card={card} h2={h2} />
      <DiskCard card={card} h2={h2} />

      {/* Posting schedule */}
      <div style={card}>
        <h2 style={h2}>⏰ Posting Schedule</h2>
        <div style={{ fontSize: 12, marginBottom: 10, color: alive ? "var(--accent)" : "var(--red)" }}>
          {sc.paused && <div style={{ color: "#fde68a", fontWeight: 700 }}>⏸ Auto-posting is PAUSED — nothing new will be submitted.</div>}
          {alive ? "●" : "○"} Auto-poster{" "}
          {!sc.enabled
            ? "is OFF on this server (WORKER_MODE=web_only)"
            : alive
            ? `running — last check ${tickAgo < 120 ? `${tickAgo}s` : `${Math.round(tickAgo / 60)} min`} ago (checks every ${Math.round(sc.tick_seconds / 60)} min, and at once when the queue changes)`
            : sc.last_tick_at
            ? `NOT running — last check ${tickAgo}s ago`
            : "has not run since the server started"}
          {sc.last_submitted && (
            <span style={{ color: "var(--muted)" }}>
              {" "}· last submitted #{sc.last_submitted.id} at {fmtSlot(sc.last_submitted.at, sched.timezone)}
            </span>
          )}
          {sc.last_error && (
            <div style={{ color: "var(--red)", fontSize: 11 }}>
              Last error ({sc.last_error_at ? fmtSlot(sc.last_error_at, sched.timezone) : "?"}): {sc.last_error}
            </div>
          )}
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10 }}>
          How it works: set a video to <b>Ready to Post</b> (and pick where it posts) — within a few seconds it gets the next
          free slot for its pipeline and posts at that time. Set it back to Review to take it out.
        </div>
        <div style={{ fontSize: 13, marginBottom: 10 }}>
          Every <b>{sched.interval_hours}h</b> from <b>{fmtHour(sched.start_hour)}</b> to{" "}
          <b>{fmtHour(sched.end_hour)}</b> {tzShort} — <b>{sched.slots_per_day} slots/day</b>. Each slot posts the
          next <i>Ready to Post</i> video from <b>each</b> pipeline, in posting order.
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
          Next slots: {sched.next_slots.map((s) => fmtSlot(s, sched.timezone)).join(" · ")}
        </div>

        {pipelines.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--yellow)" }}>
            Nothing is scheduled: no items are <i>Ready to Post</i>. Select videos in the Posting Queue and click
            “Set Ready to Post”.
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
            {pipelines.map(([name, p]) => (
              <div key={name} style={{ background: "var(--row)", border: "1px solid var(--border)", borderRadius: 8, padding: 12 }}>
                <div style={{ fontWeight: 700, fontSize: 12, marginBottom: 6 }}>
                  📁 {name} <span style={{ color: "var(--muted)", fontWeight: 400 }}>— {p.ready} ready</span>
                </div>
                {p.next.map((n) => (
                  <div key={n.id} style={{ fontSize: 11, display: "flex", gap: 8, marginTop: 3 }}>
                    <span style={{ color: "#34d399", whiteSpace: "nowrap" }}>{fmtSlot(n.scheduled_at, sched.timezone)}</span>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      #{n.id} {n.title}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
        <div style={{ marginTop: 14 }}>
          <PacingThrottle sched={sched} onSaved={setSched} />
        </div>
      </div>

      {/* Upload-Post accounts */}
      <div style={card}>
        <h2 style={h2}>🔗 Upload-Post Connected Accounts</h2>
        {accountsMsg ? (
          <div style={{ fontSize: 12, color: "var(--red)" }}>{accountsMsg}</div>
        ) : accounts.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--yellow)" }}>
            Upload-Post is connected but no social accounts are linked.{" "}
            <a href={manageUrl} target="_blank" rel="noreferrer">Connect them in Upload-Post ↗</a>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {Object.entries(
              accounts.reduce<Record<string, SocialAccount[]>>((acc, a) => {
                (acc[a.profile || "?"] ||= []).push(a);
                return acc;
              }, {})
            ).map(([profile, list]) => (
              <div key={profile}>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                  Profile <span className="mono">{profile}</span>{" "}
                  <button
                    style={{ fontSize: 10, padding: "1px 6px", marginLeft: 6 }}
                    onClick={() =>
                      api.socialConnectUrl(profile)
                        .then((r) => window.open(r.url, "_blank", "noopener"))
                        .catch(() => window.open(manageUrl, "_blank", "noopener"))
                    }
                  >
                    + connect channels
                  </button>
                </div>
                {list.map((a) => (
                  <div key={a.id} style={{ fontSize: 12, display: "flex", gap: 10, alignItems: "center", marginLeft: 8 }}>
                    <span style={{ color: "var(--accent)" }}>●</span>
                    <span style={{ fontWeight: 600 }}>{a.nickname || a.username}</span>
                    <span style={{ color: "var(--muted)" }}>{a.network}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 10 }}>
          Nothing posts unless you pick where it goes (Posts To column in the Posting Queue): a whole profile or specific channels. Each Upload-Post
          profile gets its own upload. Posts go out as <b>{sched.publisher.privacy}</b> (PUBLISH_PRIVACY).
          Upload-Post caps YouTube at 10 uploads per channel per 24h.
        </div>
        {sched.ready_without_accounts > 0 && (
          <div style={{ fontSize: 11, color: "var(--yellow)", marginTop: 8 }}>
            ⚠ {sched.ready_without_accounts} Ready video(s) have no accounts picked and won&apos;t be scheduled.
          </div>
        )}
      </div>

      {/* AI + retention */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
        <div style={card}>
          <h2 style={h2}>✨ AI Captions</h2>
          <div style={{ fontSize: 12 }}>
            {sched.ai.configured ? (
              <>
                Gemini key set · model <span className="mono">{sched.ai.model}</span>{" "}
                <button style={{ fontSize: 10, padding: "1px 8px", marginLeft: 6 }} onClick={testAi} disabled={aiTesting}>
                  {aiTesting ? "Testing…" : "Test AI"}
                </button>
                {aiResult && (
                  <div style={{ fontSize: 11, marginTop: 6, color: aiResult.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>
                    {aiResult}
                  </div>
                )}
              </>
            ) : (
              <span style={{ color: "var(--red)" }}>GEMINI_API_KEY is not set — the ✨ AI button will report an error.</span>
            )}
          </div>
          {seo && (
            <div style={{ fontSize: 12, marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 600 }}>
                <input type="checkbox" checked={seo.auto}
                  onChange={async (e) => {
                    const on = e.target.checked;
                    if (!confirm(on ? "Turn on auto-SEO? Clips still carrying their raw file name get a TMDB-researched title, caption and hashtags just before they post." : "Turn off auto-SEO? Clips will post with whatever text they have.")) return;
                    try { setSeo(await api.setSeo(on)); } catch (err: any) { alert(`Could not save: ${err.message || err}`); }
                  }} />
                Auto-SEO every clip before it posts
              </label>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                {seo.auto ? "On" : "Off"} — a clip that never had ✨ AI and still has its raw file name gets researched with TMDB and
                rewritten (title, caption, hashtags) right before posting. Text you edited yourself is never touched; each rewrite can be undone in the
                Posting Queue. {seo.clips_without_seo > 0 && <b>{seo.clips_without_seo} queued clip(s) still have raw text.</b>}
              </div>
            </div>
          )}
        </div>
        <div style={card}>
          <h2 style={h2}>🗑 Posted Archive Cleanup</h2>
          <div style={{ fontSize: 12 }}>
            {sched.archive_delete_days > 0 ? (
              <>Posted/archived videos are removed <b>{sched.archive_delete_days} days</b> after posting, and their
                Google Drive file is moved to Drive trash (recoverable there for 30 days).</>
            ) : (
              <span style={{ color: "var(--yellow)" }}>Disabled (ARCHIVE_DELETE_DAYS=0).</span>
            )}
          </div>
        </div>
      </div>

      {/* Gemini 3.8 Flash Voice Studio & Playground */}
      <div style={{ ...card, marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h2 style={{ ...h2, margin: 0 }}>🎙️ Gemini 3.8 Flash Voice Studio</h2>
            <span style={{ fontSize: 10, background: "#064e3b", color: "#34d399", padding: "2px 7px", borderRadius: 8, fontWeight: 700 }}>
              NEW TTS
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <button
              onClick={handleTtsCheck}
              disabled={ttsCheckTesting}
              style={{ fontSize: 11, padding: "3px 10px", background: "rgba(255,255,255,0.06)" }}
            >
              {ttsCheckTesting ? "Testing…" : "⚡ Test TTS Connection"}
            </button>
            {ttsCheckResult && (
              <span style={{ fontSize: 11, color: ttsCheckResult.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>
                {ttsCheckResult}
              </span>
            )}
          </div>
        </div>

        <p style={{ margin: "0 0 14px", color: "var(--muted)", fontSize: 12, lineHeight: 1.5 }}>
          Generates expressive, human-like voiceovers with turn-level emotion metadata and point-in-time inline tags
          (<span className="mono" style={{ color: "#38bdf8" }}>&lt;gasp&gt;</span>, <span className="mono" style={{ color: "#38bdf8" }}>&lt;sigh&gt;</span>, <span className="mono" style={{ color: "#38bdf8" }}>&lt;short pause&gt;</span>).
        </p>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginBottom: 12 }}>
          {/* Voice Picker */}
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", display: "block", marginBottom: 4 }}>
              Studio Voice:
            </label>
            <select
              value={ttsVoice}
              onChange={(e) => setTtsVoice(e.target.value)}
              style={{ width: "100%", padding: "6px 10px", background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, color: "var(--text)", fontSize: 12 }}
            >
              {ttsVoices.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name} ({v.timbre}) — {v.gender}
                </option>
              ))}
            </select>
          </div>

          {/* Model Picker */}
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", display: "block", marginBottom: 4 }}>
              TTS Model:
            </label>
            <select
              value={ttsModel}
              onChange={(e) => setTtsModel(e.target.value)}
              style={{ width: "100%", padding: "6px 10px", background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, color: "var(--text)", fontSize: 12 }}
            >
              <option value="gemini-3.8-flash-tts">gemini-3.8-flash-tts (Studio Fidelity &amp; Acting)</option>
              <option value="gemini-3.8-flash-lite-tts">gemini-3.8-flash-lite-tts (Fast Bulk Generation)</option>
            </select>
          </div>

          {/* Style Input */}
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", display: "block", marginBottom: 4 }}>
              Turn-Level Style / Emotion:
            </label>
            <input
              type="text"
              value={ttsStyle}
              onChange={(e) => setTtsStyle(e.target.value)}
              placeholder="e.g. urgent and dramatic, whispered urgently"
              style={{ width: "100%", padding: "6px 10px", background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, color: "var(--text)", fontSize: 12 }}
            />
          </div>
        </div>

        {/* Quick Style Presets */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12, alignItems: "center" }}>
          <span style={{ fontSize: 10, color: "var(--muted)" }}>Quick Styles:</span>
          {[
            "urgent and dramatic build-up",
            "high energy and enthusiastic",
            "whispered urgently",
            "deep cinematic movie trailer narrator",
            "sarcastic and playful",
            "calm and authoritative",
          ].map((st) => (
            <button
              key={st}
              type="button"
              onClick={() => setTtsStyle(st)}
              style={{ fontSize: 10, padding: "2px 8px", background: ttsStyle === st ? "var(--chip)" : "rgba(255,255,255,0.04)", border: "1px solid var(--border)", borderRadius: 12, color: ttsStyle === st ? "var(--accent)" : "var(--muted)", cursor: "pointer" }}
            >
              {st}
            </button>
          ))}
        </div>

        {/* Text Input with Inline Tag Insertions */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <label style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)" }}>
              Verbatim Transcript:
            </label>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              <span style={{ fontSize: 10, color: "var(--muted)", alignSelf: "center", marginRight: 4 }}>Insert Tag:</span>
              {["<gasp>", "<sigh>", "<short pause>", "<long pause>", "<chuckle>", "<throat-clearing>"].map((tag) => (
                <button
                  key={tag}
                  type="button"
                  onClick={() => setTtsText((prev) => prev + " " + tag + " ")}
                  style={{ fontSize: 10, padding: "1px 6px", background: "#1e293b", border: "1px solid #334155", color: "#38bdf8", borderRadius: 4, cursor: "pointer" }}
                >
                  {tag}
                </button>
              ))}
            </div>
          </div>
          <textarea
            rows={2}
            value={ttsText}
            onChange={(e) => setTtsText(e.target.value)}
            style={{ width: "100%", padding: "8px 10px", background: "var(--row)", border: "1px solid var(--border)", borderRadius: 6, color: "var(--text)", fontSize: 12, resize: "vertical" }}
          />
        </div>

        {/* Action Button & Player */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button
            onClick={handleTtsGenerate}
            disabled={ttsGenerating || !ttsText.trim()}
            style={{ background: "#2563eb", borderColor: "#1d4ed8", color: "#fff", fontWeight: 600, fontSize: 12, padding: "7px 16px" }}
          >
            {ttsGenerating ? "⏳ Synthesizing Voice…" : "▶ Synthesize Audio"}
          </button>

          {ttsStatusMsg && (
            <span style={{ fontSize: 11, color: ttsStatusMsg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>
              {ttsStatusMsg}
            </span>
          )}

          {ttsAudioUrl && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginLeft: "auto" }}>
              <audio controls src={mediaUrl(ttsAudioUrl)} autoPlay style={{ height: 32 }} />
              <a
                href={mediaUrl(ttsAudioUrl)}
                download="gemini_tts.wav"
                style={{ fontSize: 11, color: "var(--accent)", textDecoration: "underline" }}
              >
                ⬇ Download WAV
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
