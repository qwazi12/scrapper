"use client";

import React, { useEffect, useState } from "react";
import { api, ScheduleInfo } from "../lib/api";
import { notify } from "../lib/dialogs";

// One queue's own posting schedule (owner, 2026-10-05): the Posting Queue
// ("Movie Clips") and LongForm each get posts/day + hours, or exact times —
// editing one never changes the other.

type Queue = "Movie Clips" | "LongForm";
const LABEL: Record<Queue, string> = { "Movie Clips": "Posting Queue", LongForm: "LongForm" };

const fmtHour = (h: number) => `${h % 12 === 0 ? 12 : h % 12}${h < 12 ? " AM" : " PM"}`;
const fmtTime = (t: string) => {
  const [h, m] = t.split(":").map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
};
const fmtSlot = (iso: string, tz: string) =>
  new Date(iso).toLocaleString("en-US", { timeZone: tz, weekday: "short", month: "short", day: "numeric",
                                          hour: "numeric", minute: "2-digit" });

export function QueueScheduleButton({ queue, schedInfo, isNarrow, onSaved }: {
  queue: Queue;
  schedInfo: ScheduleInfo | null;
  isNarrow: boolean;
  onSaved: (s: ScheduleInfo) => void;
}) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const ov = schedInfo?.pipeline_overrides?.[queue] || {};
  const globalPpd = schedInfo?.posts_per_day ?? schedInfo?.slots_per_day ?? 1;
  const [mode, setMode] = useState<"spread" | "times">("spread");
  const [ppd, setPpd] = useState(1);
  const [start, setStart] = useState(10);
  const [end, setEnd] = useState(20);
  const [times, setTimes] = useState<string[]>([]);
  const [newTime, setNewTime] = useState("10:00");

  // Load the saved values each time the editor opens.
  useEffect(() => {
    if (!open || !schedInfo) return;
    setMode(ov.times && ov.times.length ? "times" : "spread");
    setPpd(ov.posts_per_day ?? globalPpd);
    setStart(ov.start_hour ?? schedInfo.start_hour);
    setEnd(ov.end_hour ?? schedInfo.end_hour);
    setTimes(ov.times || []);
  }, [open]);   // eslint-disable-line react-hooks/exhaustive-deps

  const perDay = ov.times?.length || ov.posts_per_day || globalPpd;
  const summary = ov.times?.length ? ov.times.map(fmtTime).join(", ") : `${fmtHour(ov.start_hour ?? schedInfo?.start_hour ?? 10)}–${fmtHour(ov.end_hour ?? schedInfo?.end_hour ?? 20)}`;
  const next = schedInfo?.pipeline_next_slots?.[queue] || [];
  const tz = schedInfo?.timezone || "America/New_York";

  async function save(useDefault = false) {
    if (!schedInfo) return;
    if (!useDefault && mode === "times" && times.length === 0) return notify("Add at least one posting time.");
    if (!useDefault && mode === "spread" && start > end) return notify("The first post must be at or before the last.");
    setSaving(true);
    try {
      // PUT replaces the whole schedule: send everything as saved, change only this queue.
      const pipelineOverrides = { ...(schedInfo.pipeline_overrides || {}) };
      if (useDefault) delete pipelineOverrides[queue];
      else pipelineOverrides[queue] = mode === "times"
        ? { times }
        : { posts_per_day: ppd, start_hour: start, end_hour: end };
      const updated = await api.updateSchedule({
        timezone: schedInfo.timezone, start_hour: schedInfo.start_hour, end_hour: schedInfo.end_hour,
        interval_hours: schedInfo.interval_hours, posts_per_day: schedInfo.posts_per_day ?? null,
        pipeline_overrides: pipelineOverrides, account_overrides: { ...(schedInfo.account_overrides || {}) },
      });
      onSaved(updated);
      setOpen(false);
      notify(`✓ ${LABEL[queue]} schedule saved — Ready videos moved onto the new times.`);
    } catch (e: any) {
      notify(`Could not save the ${LABEL[queue]} schedule: ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  const sheet: React.CSSProperties = isNarrow
    ? { position: "fixed", left: 16, right: 16, bottom: "calc(16px + env(safe-area-inset-bottom, 0px))", maxHeight: "75vh", overflowY: "auto" }
    : { position: "absolute", top: "100%", right: 0, marginTop: 6, width: 340 };

  return (
    <div style={{ position: "relative" }}>
      <button onClick={() => setOpen(!open)} disabled={saving || !schedInfo}
              title={`${LABEL[queue]} posting schedule`}
              style={{ background: "#064e3b", borderColor: "#059669", color: "#d1fae5", fontWeight: 600, fontSize: 11 }}>
        ⏰ Schedule · {perDay}/day{isNarrow ? "" : ` · ${summary}`} ▾
      </button>
      {open && schedInfo && (
        <div style={{ ...sheet, zIndex: 60, background: "var(--panel)", border: "1px solid #059669", borderRadius: 10,
                      padding: 12, boxShadow: "0 10px 25px rgba(0,0,0,0.5)", display: "flex", flexDirection: "column", gap: 10, fontSize: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <b>{LABEL[queue]} schedule</b>
            <button style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setOpen(false)}>✕</button>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {(["spread", "times"] as const).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                      style={{ flex: 1, fontSize: 11, padding: "4px 6px",
                               background: mode === m ? "#064e3b" : "transparent", borderColor: mode === m ? "#059669" : "var(--border)" }}>
                {m === "spread" ? "Spread across hours" : "Exact times"}
              </button>
            ))}
          </div>

          {mode === "spread" ? (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
              <label>Posts / day
                <select value={ppd} onChange={(e) => setPpd(Number(e.target.value))} style={{ width: "100%" }}>
                  {[1, 2, 3, 4, 5, 6, 8, 10, 12].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <label>First post
                <select value={start} onChange={(e) => setStart(Number(e.target.value))} style={{ width: "100%" }}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
                </select>
              </label>
              <label>Last post
                <select value={end} onChange={(e) => setEnd(Number(e.target.value))} style={{ width: "100%" }}>
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{fmtHour(h)}</option>)}
                </select>
              </label>
            </div>
          ) : (
            <div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
                {times.length === 0 && <span style={{ color: "var(--muted)" }}>No times yet — add one below.</span>}
                {times.map((t) => (
                  <span key={t} style={{ background: "#064e3b", border: "1px solid #059669", borderRadius: 12, padding: "2px 8px" }}>
                    {fmtTime(t)} <button style={{ border: "none", background: "transparent", padding: 0, marginLeft: 4, fontSize: 11 }}
                                         onClick={() => setTimes(times.filter((x) => x !== t))} title="Remove">✕</button>
                  </span>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <input type="time" value={newTime} onChange={(e) => setNewTime(e.target.value)} style={{ flex: 1 }} />
                <button style={{ fontSize: 11 }} onClick={() => {
                  if (newTime && !times.includes(newTime)) setTimes([...times, newTime].sort());
                }}>+ Add time</button>
              </div>
              <div style={{ color: "var(--muted)", fontSize: 10, marginTop: 4 }}>One post at each time, every day ({tz}).</div>
            </div>
          )}

          <div>
            <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 2 }}>Next posts on the saved schedule:</div>
            {next.length ? next.map((t) => <div key={t}>⏰ {fmtSlot(t, tz)}</div>)
              : <div style={{ color: "var(--muted)" }}>—</div>}
          </div>

          <div style={{ display: "flex", gap: 8, justifyContent: "space-between", flexWrap: "wrap" }}>
            <button style={{ fontSize: 11 }} disabled={saving || !schedInfo.pipeline_overrides?.[queue]}
                    title="Drop this queue's own schedule and follow the default" onClick={() => save(true)}>
              Use the default
            </button>
            <button className="primary" style={{ fontSize: 12 }} disabled={saving} onClick={() => save(false)}>
              {saving ? "Saving…" : "Save schedule"}
            </button>
          </div>
          <div style={{ color: "var(--muted)", fontSize: 10 }}>
            📌 To post one video at a set time, open its Edit and pin a time. ⏫ Post next puts a video first in line.
          </div>
        </div>
      )}
    </div>
  );
}
