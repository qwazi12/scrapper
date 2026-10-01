"use client";

import React, { useEffect, useState } from "react";
import { api, parseApiDate, ScheduleConfig, ScheduleInfo, SocialAccount } from "../lib/api";

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

  // Refresh the schedule/heartbeat every 30s so the status stays live.
  useEffect(() => {
    const t = setInterval(() => {
      setNow(Date.now());
      api.schedule().then(setSched).catch(() => {});
    }, 30000);
    return () => clearInterval(t);
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

  useEffect(() => {
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
      {/* Posting schedule */}
      <div style={card}>
        <h2 style={h2}>⏰ Posting Schedule</h2>
        <div style={{ fontSize: 12, marginBottom: 10, color: alive ? "var(--accent)" : "var(--red)" }}>
          {alive ? "●" : "○"} Auto-poster{" "}
          {!sc.enabled
            ? "is OFF on this server (WORKER_MODE=web_only)"
            : alive
            ? `running — last check ${tickAgo}s ago (every ${sc.tick_seconds}s)`
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
          How it works: set a video to <b>Ready to Post</b> (and pick where it posts) — within 30s it gets the next
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
        <ScheduleEditor sched={sched} onSaved={setSched} />
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
    </div>
  );
}
