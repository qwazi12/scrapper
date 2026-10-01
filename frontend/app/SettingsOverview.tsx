"use client";

import React, { useEffect, useState } from "react";
import { api, ScheduleInfo, SocialAccount } from "../lib/api";

function fmtHour(h: number): string {
  const ampm = h < 12 ? "am" : "pm";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}${ampm}`;
}

function fmtSlot(iso: string, tz: string): string {
  return new Date(iso).toLocaleString("en-US", {
    timeZone: tz, weekday: "short", hour: "numeric", minute: "2-digit",
  });
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
  const [err, setErr] = useState<string>("");

  useEffect(() => {
    api.schedule().then(setSched).catch((e) => setErr(String(e.message || e)));
    api.socialAccounts()
      .then((r) => {
        setAccounts(r.accounts || []);
        if (!r.configured) setAccountsMsg(r.message || "Outstand is not configured");
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
  const pipelines = Object.entries(sched.pipelines);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Posting schedule */}
      <div style={card}>
        <h2 style={h2}>⏰ Posting Schedule</h2>
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
        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 12 }}>
          Change via Railway env vars: POST_START_HOUR, POST_END_HOUR, POST_INTERVAL_HOURS, POST_TIMEZONE.
        </div>
      </div>

      {/* Outstand accounts */}
      <div style={card}>
        <h2 style={h2}>🔗 Outstand Connected Accounts</h2>
        {accountsMsg ? (
          <div style={{ fontSize: 12, color: "var(--red)" }}>{accountsMsg}</div>
        ) : accounts.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--yellow)" }}>
            Outstand is connected but has no social accounts. Connect them in the Outstand dashboard.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {accounts.map((a) => (
              <div key={a.id} style={{ fontSize: 12, display: "flex", gap: 10, alignItems: "center" }}>
                <span style={{ color: a.isActive ? "var(--accent)" : "var(--red)" }}>●</span>
                <span style={{ fontWeight: 600 }}>{a.nickname || a.username}</span>
                <span style={{ color: "var(--muted)" }}>{a.username} · {a.network}</span>
              </div>
            ))}
          </div>
        )}
        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 10 }}>
          Videos with no accounts assigned post to every active account above.
        </div>
        {accounts.some((a) => a.network === "youtube") && (
          <div style={{ fontSize: 11, color: "var(--yellow)", marginTop: 8 }}>
            ⚠️ YouTube: with Outstand&apos;s Managed Keys, uploads share one Google quota (~6 uploads/day across all
            Outstand users) and fail with “quota exceeded”. Connect YouTube with your own Google Cloud project (BYOK)
            in Outstand for your own quota — still ~6/day per project unless Google raises it.
          </div>
        )}
      </div>

      {/* AI + retention */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 16 }}>
        <div style={card}>
          <h2 style={h2}>✨ AI Captions</h2>
          <div style={{ fontSize: 12 }}>
            {sched.ai.configured ? (
              <>Gemini key set · model <span className="mono">{sched.ai.model}</span></>
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
