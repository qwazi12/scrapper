"use client";

import React, { useEffect, useState } from "react";
import { api, PacingOverride, ScheduleConfig, ScheduleInfo } from "../lib/api";
import { UndoButton } from "./UndoButton";

const PRESET_PACING = [
  { count: 1, label: "1 / day", desc: "Daily Highlight ( Drop)" },
  { count: 3, label: "3 / day", desc: "Morning, Afternoon, Evening" },
  { count: 4, label: "4 / day", desc: "Every 4 Hours" },
  { count: 8, label: "8 / day", desc: "Every 2 Hours (Standard)", isDefault: true },
  { count: 12, label: "12 / day", desc: "High Velocity (Every ~70m)" },
  { count: 20, label: "20 / day", desc: "Blitz (Every ~44m)" },
];

const KNOWN_PIPELINES = [
  { id: "Movie Clips", name: "Movie Clips", icon: "🎬", desc: "14-Channel Shorts Pipeline (Parent)" },
  { id: "LongForm", name: "LongForm Studio", icon: "🎞️", desc: "Trailer Breakdown Videos (16:9)" },
  { id: "Abyss Declassified", name: "Abyss Declassified", icon: "🌊", desc: "Documentary Pipeline" },
  { id: "The ICK Room", name: "The ICK Room", icon: "🎙️", desc: "Curated Clips Pipeline" },
  { id: "default", name: "Default Pipeline", icon: "📁", desc: "General Content Queue" },
];

const KNOWN_ACCOUNTS = [
  { id: "default", name: "Screen Central (YouTube)", icon: "▶️", profile: "default" },
  { id: "mk", name: "Flamingo Remix (YouTube)", icon: "🦩", profile: "mk" },
];

const TIMEZONES = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "UTC",
  "Europe/London",
];

function fmtHour(h: number): string {
  const ampm = h >= 12 ? "PM" : "AM";
  const num = h % 12 === 0 ? 12 : h % 12;
  return `${num}:00 ${ampm}`;
}

function fmtSlot(iso: string, tz: string): string {
  try {
    return new Date(Date.parse(iso)).toLocaleTimeString("en-US", {
      timeZone: tz,
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function calculatePacingInterval(postsPerDay: number, startHour: number, endHour: number): string {
  if (postsPerDay <= 1) return `Single post at ${fmtHour(startHour)}`;
  const windowMins = (endHour - startHour) * 60;
  if (windowMins <= 0) return `Distributed across 24h`;
  const stepMins = Math.round(windowMins / (postsPerDay - 1));
  const h = Math.floor(stepMins / 60);
  const m = stepMins % 60;
  if (h > 0 && m > 0) return `Every ${h}h ${m}m`;
  if (h > 0) return `Every ${h} hour${h === 1 ? "" : "s"}`;
  return `Every ${m} mins`;
}

export function PacingThrottle({
  sched,
  onSaved,
  compact = false,
}: {
  sched: ScheduleInfo;
  onSaved: (s: ScheduleInfo) => void;
  compact?: boolean;
}) {
  const [targetTab, setTargetTab] = useState<"global" | "pipelines" | "accounts">("global");
  const [form, setForm] = useState<ScheduleConfig>({
    timezone: sched.timezone,
    start_hour: sched.start_hour,
    end_hour: sched.end_hour,
    interval_hours: sched.interval_hours,
    posts_per_day: sched.posts_per_day ?? sched.slots_per_day,
    pipeline_overrides: { ...(sched.pipeline_overrides || sched.pipelines || {}) },
    account_overrides: { ...(sched.account_overrides || sched.accounts || {}) },
  });
  const [showAdvancedHours, setShowAdvancedHours] = useState(false);
  const [customInput, setCustomInput] = useState<string>(
    String(sched.posts_per_day ?? sched.slots_per_day ?? 8)
  );
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const currentGlobalPpd = form.posts_per_day ?? sched.slots_per_day ?? 8;
  const currentIntervalText = calculatePacingInterval(currentGlobalPpd, form.start_hour, form.end_hour);

  // Total ready items across queue
  const totalReady = Object.values(sched.pipelines || {}).reduce((acc, p) => acc + (p.ready || 0), 0);
  const daysRunway = currentGlobalPpd > 0 ? (totalReady / currentGlobalPpd).toFixed(1) : "0";

  // Check if modified compared to incoming sched
  const isDirty =
    form.posts_per_day !== (sched.posts_per_day ?? sched.slots_per_day) ||
    form.start_hour !== sched.start_hour ||
    form.end_hour !== sched.end_hour ||
    form.timezone !== sched.timezone ||
    JSON.stringify(form.pipeline_overrides) !== JSON.stringify(sched.pipeline_overrides || {}) ||
    JSON.stringify(form.account_overrides) !== JSON.stringify(sched.account_overrides || {});

  async function handleSave(reset = false) {
    if (reset) {
      if (!confirm("Reset all posting pacing & schedule back to defaults (8 posts/day, 8 AM - 10 PM ET)?")) {
        return;
      }
    } else {
      const promptText =
        `Save Pacing Schedule?\n\n` +
        `• Global Velocity: ${form.posts_per_day ?? 8} posts/day (${currentIntervalText})\n` +
        `• Active Window: ${fmtHour(form.start_hour)} - ${fmtHour(form.end_hour)} (${form.timezone})\n` +
        `• Pipeline Overrides: ${Object.keys(form.pipeline_overrides || {}).length} configured\n` +
        `• Account Overrides: ${Object.keys(form.account_overrides || {}).length} configured\n\n` +
        `Ready videos in the queue will immediately re-plan onto the new slots.`;
      if (!confirm(promptText)) return;
    }

    setSaving(true);
    setMsg("");
    try {
      const res = await api.updateSchedule(reset ? { reset: true } : form);
      onSaved(res);
      setForm({
        timezone: res.timezone,
        start_hour: res.start_hour,
        end_hour: res.end_hour,
        interval_hours: res.interval_hours,
        posts_per_day: res.posts_per_day ?? res.slots_per_day,
        pipeline_overrides: { ...(res.pipeline_overrides || res.pipelines || {}) },
        account_overrides: { ...(res.account_overrides || res.accounts || {}) },
      });
      setMsg("✓ Schedule & Pacing saved — queue re-planned");
    } catch (e: any) {
      setMsg(`✕ Failed to save: ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  function setPipelineOverride(pipelineId: string, ppd: number | null) {
    const next = { ...(form.pipeline_overrides || {}) };
    if (ppd === null) {
      delete next[pipelineId];
    } else {
      next[pipelineId] = {
        ...(next[pipelineId] || {}),
        posts_per_day: ppd,
        start_hour: form.start_hour,
        end_hour: form.end_hour,
      };
    }
    setForm({ ...form, pipeline_overrides: next });
  }

  function setAccountOverride(accountId: string, ppd: number | null) {
    const next = { ...(form.account_overrides || {}) };
    if (ppd === null) {
      delete next[accountId];
    } else {
      next[accountId] = {
        ...(next[accountId] || {}),
        posts_per_day: ppd,
        start_hour: form.start_hour,
        end_hour: form.end_hour,
      };
    }
    setForm({ ...form, account_overrides: next });
  }

  return (
    <div
      style={{
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: compact ? "14px" : "18px 20px",
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      {/* Top Velocity Speedometer & Live Runway */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          flexWrap: "wrap",
          gap: 12,
          paddingBottom: 14,
          borderBottom: "1px solid var(--border)",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 20 }}>⚡</span>
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: "#f8fafc" }}>
              Posting Pacing Throttle &amp; Scheduler
            </h2>
            <span
              style={{
                fontSize: 11,
                fontWeight: 700,
                color: "#34d399",
                background: "#064e3b",
                padding: "2px 8px",
                borderRadius: 12,
              }}
            >
              {currentGlobalPpd} POSTS / DAY
            </span>
            {sched.scheduler.paused && (
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "#fde68a",
                  background: "#78350f",
                  padding: "2px 8px",
                  borderRadius: 12,
                }}
              >
                ⏸ AUTO-POSTING PAUSED
              </span>
            )}
          </div>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--muted)" }}>
            {currentIntervalText} • Active {fmtHour(form.start_hour)} to {fmtHour(form.end_hour)} ({form.timezone})
          </p>
        </div>

        {/* Live Queue Runway Badge */}
        <div
          style={{
            background: "var(--row)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: "8px 12px",
            fontSize: 11,
            textAlign: "right",
          }}
        >
          <div style={{ color: "var(--muted)", marginBottom: 2 }}>Content Runway</div>
          <div style={{ fontWeight: 700, color: "#38bdf8" }}>
            {totalReady} ready videos • ~{daysRunway} days
          </div>
        </div>
      </div>

      {/* Sub-Tabs: Global Presets vs Per-Pipeline vs Per-Account */}
      <div style={{ display: "flex", gap: 8, borderBottom: "1px solid var(--border)", paddingBottom: 8 }}>
        <button
          onClick={() => setTargetTab("global")}
          style={{
            background: targetTab === "global" ? "#1e293b" : "transparent",
            color: targetTab === "global" ? "#38bdf8" : "var(--muted)",
            border: targetTab === "global" ? "1px solid #38bdf8" : "1px solid transparent",
            fontWeight: 600,
            fontSize: 12,
            padding: "5px 12px",
            borderRadius: 6,
          }}
        >
          🌐 Global Default ({currentGlobalPpd}/day)
        </button>
        <button
          onClick={() => setTargetTab("pipelines")}
          style={{
            background: targetTab === "pipelines" ? "#1e293b" : "transparent",
            color: targetTab === "pipelines" ? "#38bdf8" : "var(--muted)",
            border: targetTab === "pipelines" ? "1px solid #38bdf8" : "1px solid transparent",
            fontWeight: 600,
            fontSize: 12,
            padding: "5px 12px",
            borderRadius: 6,
          }}
        >
          📁 Per-Pipeline Overrides ({Object.keys(form.pipeline_overrides || {}).length})
        </button>
        <button
          onClick={() => setTargetTab("accounts")}
          style={{
            background: targetTab === "accounts" ? "#1e293b" : "transparent",
            color: targetTab === "accounts" ? "#38bdf8" : "var(--muted)",
            border: targetTab === "accounts" ? "1px solid #38bdf8" : "1px solid transparent",
            fontWeight: 600,
            fontSize: 12,
            padding: "5px 12px",
            borderRadius: 6,
          }}
        >
          🔗 Per-Account Overrides ({Object.keys(form.account_overrides || {}).length})
        </button>
      </div>

      {/* TAB 1: GLOBAL PRESETS */}
      {targetTab === "global" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            Select a 1-click velocity preset or enter a custom posts/day target. This sets the baseline rate for all
            pipelines that do not have a dedicated override.
          </div>

          {/* 1-Click Preset Grid */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
              gap: 10,
            }}
          >
            {PRESET_PACING.map((preset) => {
              const selected = form.posts_per_day === preset.count;
              return (
                <button
                  key={preset.count}
                  type="button"
                  onClick={() => {
                    setForm({ ...form, posts_per_day: preset.count });
                    setCustomInput(String(preset.count));
                  }}
                  style={{
                    background: selected ? "#064e3b" : "var(--row)",
                    border: selected ? "1px solid var(--accent)" : "1px solid var(--border)",
                    color: selected ? "#34d399" : "var(--text)",
                    padding: "10px",
                    borderRadius: 8,
                    textAlign: "center",
                    cursor: "pointer",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    gap: 4,
                  }}
                >
                  <div style={{ fontWeight: 700, fontSize: 14 }}>{preset.label}</div>
                  <div style={{ fontSize: 10, color: selected ? "#a7f3d0" : "var(--muted)" }}>
                    {preset.desc}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Custom Velocity Input */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "10px 14px",
              background: "var(--row)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              flexWrap: "wrap",
            }}
          >
            <span style={{ fontSize: 12, fontWeight: 600 }}>Custom Velocity:</span>
            <input
              type="number"
              min={1}
              max={48}
              value={customInput}
              onChange={(e) => {
                setCustomInput(e.target.value);
                const n = parseInt(e.target.value, 10);
                if (!isNaN(n) && n >= 1 && n <= 48) {
                  setForm({ ...form, posts_per_day: n });
                }
              }}
              style={{
                width: 70,
                padding: "4px 8px",
                fontSize: 12,
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: "var(--bg)",
                color: "var(--text)",
              }}
            />
            <span style={{ fontSize: 11, color: "var(--muted)" }}>
              posts per day (spread evenly across active hours)
            </span>
          </div>
        </div>
      )}

      {/* TAB 2: PER-PIPELINE OVERRIDES */}
      {targetTab === "pipelines" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            Tune posting velocity independently per pipeline. For instance, run <b>Movie Clips</b> at 20/day while
            running <b>LongForm Studio</b> at 1/day.
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {KNOWN_PIPELINES.map((p) => {
              const override = form.pipeline_overrides?.[p.id];
              const effectivePpd = override?.posts_per_day ?? currentGlobalPpd;
              const hasOverride = override?.posts_per_day !== undefined;
              const readyCount = sched.pipelines?.[p.id]?.ready || 0;

              return (
                <div
                  key={p.id}
                  style={{
                    background: "var(--row)",
                    border: hasOverride ? "1px solid #059669" : "1px solid var(--border)",
                    borderRadius: 8,
                    padding: "12px 14px",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: 12,
                  }}
                >
                  <div style={{ minWidth: 200 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span>{p.icon}</span>
                      <span style={{ fontWeight: 700, fontSize: 13 }}>{p.name}</span>
                      <span style={{ fontSize: 11, color: "var(--muted)" }}>({readyCount} ready)</span>
                    </div>
                    <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{p.desc}</div>
                    <div style={{ marginTop: 4 }}>
                      {hasOverride ? (
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 700,
                            color: "#34d399",
                            background: "#064e3b",
                            padding: "2px 6px",
                            borderRadius: 10,
                          }}
                        >
                          ⚡ OVERRIDE: {effectivePpd} / day
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, color: "var(--muted)" }}>
                          ↳ Inheriting global ({effectivePpd} / day)
                        </span>
                      )}
                    </div>
                  </div>

                  {/* 1-Click Override Buttons */}
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                    {[1, 3, 8, 12, 20].map((count) => {
                      const isSelected = hasOverride && override?.posts_per_day === count;
                      return (
                        <button
                          key={count}
                          type="button"
                          onClick={() => setPipelineOverride(p.id, count)}
                          style={{
                            fontSize: 11,
                            padding: "3px 8px",
                            borderRadius: 4,
                            background: isSelected ? "#064e3b" : "var(--bg)",
                            color: isSelected ? "#34d399" : "var(--text)",
                            border: isSelected ? "1px solid var(--accent)" : "1px solid var(--border)",
                          }}
                        >
                          {count}/d
                        </button>
                      );
                    })}
                    {hasOverride && (
                      <button
                        type="button"
                        onClick={() => setPipelineOverride(p.id, null)}
                        style={{
                          fontSize: 10,
                          padding: "3px 8px",
                          borderRadius: 4,
                          background: "transparent",
                          color: "var(--yellow)",
                          border: "1px dashed var(--yellow)",
                        }}
                      >
                        ↺ Reset to Global
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* TAB 3: PER-ACCOUNT OVERRIDES */}
      {targetTab === "accounts" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>
            Tune posting velocity per connected Upload-Post account profile. Target account overrides take precedence
            over pipeline overrides.
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {KNOWN_ACCOUNTS.map((acc) => {
              const override = form.account_overrides?.[acc.id];
              const effectivePpd = override?.posts_per_day ?? currentGlobalPpd;
              const hasOverride = override?.posts_per_day !== undefined;

              return (
                <div
                  key={acc.id}
                  style={{
                    background: "var(--row)",
                    border: hasOverride ? "1px solid #059669" : "1px solid var(--border)",
                    borderRadius: 8,
                    padding: "12px 14px",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: 12,
                  }}
                >
                  <div style={{ minWidth: 200 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span>{acc.icon}</span>
                      <span style={{ fontWeight: 700, fontSize: 13 }}>{acc.name}</span>
                      <span style={{ fontSize: 10, color: "var(--muted)", background: "var(--bg)", padding: "1px 5px", borderRadius: 4 }}>
                        profile: {acc.profile}
                      </span>
                    </div>
                    <div style={{ marginTop: 4 }}>
                      {hasOverride ? (
                        <span
                          style={{
                            fontSize: 10,
                            fontWeight: 700,
                            color: "#34d399",
                            background: "#064e3b",
                            padding: "2px 6px",
                            borderRadius: 10,
                          }}
                        >
                          ⚡ OVERRIDE: {effectivePpd} / day
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, color: "var(--muted)" }}>
                          ↳ Inheriting global ({effectivePpd} / day)
                        </span>
                      )}
                    </div>
                  </div>

                  {/* 1-Click Override Buttons */}
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                    {[1, 2, 4, 8, 12].map((count) => {
                      const isSelected = hasOverride && override?.posts_per_day === count;
                      return (
                        <button
                          key={count}
                          type="button"
                          onClick={() => setAccountOverride(acc.id, count)}
                          style={{
                            fontSize: 11,
                            padding: "3px 8px",
                            borderRadius: 4,
                            background: isSelected ? "#064e3b" : "var(--bg)",
                            color: isSelected ? "#34d399" : "var(--text)",
                            border: isSelected ? "1px solid var(--accent)" : "1px solid var(--border)",
                          }}
                        >
                          {count}/d
                        </button>
                      );
                    })}
                    {hasOverride && (
                      <button
                        type="button"
                        onClick={() => setAccountOverride(acc.id, null)}
                        style={{
                          fontSize: 10,
                          padding: "3px 8px",
                          borderRadius: 4,
                          background: "transparent",
                          color: "var(--yellow)",
                          border: "1px dashed var(--yellow)",
                        }}
                      >
                        ↺ Reset to Global
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Advanced Active Hours & Timezone Accordion */}
      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
        <button
          type="button"
          onClick={() => setShowAdvancedHours(!showAdvancedHours)}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--muted)",
            fontSize: 11,
            cursor: "pointer",
            padding: 0,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <span>{showAdvancedHours ? "▼" : "▶"}</span>
          <span>Advanced Active Hours &amp; Timezone ({fmtHour(form.start_hour)} - {fmtHour(form.end_hour)} {form.timezone})</span>
        </button>

        {showAdvancedHours && (
          <div
            style={{
              marginTop: 10,
              padding: 12,
              background: "var(--row)",
              borderRadius: 8,
              display: "flex",
              gap: 14,
              flexWrap: "wrap",
              alignItems: "flex-end",
              fontSize: 11,
            }}
          >
            <label>
              <div style={{ marginBottom: 3, color: "var(--muted)" }}>First Slot</div>
              <select
                value={form.start_hour}
                onChange={(e) => setForm({ ...form, start_hour: Number(e.target.value) })}
                style={{ padding: "4px 8px", fontSize: 12 }}
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {fmtHour(h)}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <div style={{ marginBottom: 3, color: "var(--muted)" }}>Last Slot</div>
              <select
                value={form.end_hour}
                onChange={(e) => setForm({ ...form, end_hour: Number(e.target.value) })}
                style={{ padding: "4px 8px", fontSize: 12 }}
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {fmtHour(h)}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <div style={{ marginBottom: 3, color: "var(--muted)" }}>Timezone</div>
              <select
                value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })}
                style={{ padding: "4px 8px", fontSize: 12 }}
              >
                {TIMEZONES.map((z) => (
                  <option key={z} value={z}>
                    {z}
                  </option>
                ))}
              </select>
            </label>
          </div>
        )}
      </div>

      {/* Next Planned Slots Preview */}
      <div style={{ fontSize: 11, color: "var(--muted)" }}>
        <b>Upcoming Slots ({form.timezone}):</b>{" "}
        {sched.next_slots.slice(0, 6).map((s) => fmtSlot(s, sched.timezone)).join(" • ")}
      </div>

      {/* Action Footer */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 10,
          borderTop: "1px solid var(--border)",
          paddingTop: 12,
        }}
      >
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            className="primary"
            disabled={saving || !isDirty}
            onClick={() => handleSave(false)}
            style={{ fontWeight: 700, fontSize: 12, padding: "6px 14px" }}
          >
            {saving ? "Saving…" : isDirty ? "💾 Save Pacing Schedule" : "✓ Pacing Up-To-Date"}
          </button>

          <button
            type="button"
            style={{
              fontSize: 11,
              padding: "6px 12px",
              background: sched.scheduler.paused ? "#78350f" : "var(--row)",
              color: sched.scheduler.paused ? "#fde68a" : "var(--text)",
              border: sched.scheduler.paused ? "1px solid #d97706" : "1px solid var(--border)",
              fontWeight: 600,
            }}
            onClick={async () => {
              const next = !sched.scheduler.paused;
              if (
                !confirm(
                  next
                    ? "Pause auto-posting? Nothing new will be submitted until you resume."
                    : "Resume auto-posting?"
                )
              ) {
                return;
              }
              await api.setAutopost(next);
              onSaved(await api.schedule());
            }}
          >
            {sched.scheduler.paused ? "▶ Resume Auto-Posting" : "⏸ Pause Auto-Posting"}
          </button>

          <UndoButton
            scope="settings"
            version={JSON.stringify([
              form.posts_per_day,
              form.start_hour,
              form.end_hour,
              form.timezone,
              form.pipeline_overrides,
              form.account_overrides,
              sched.scheduler.paused,
            ])}
            onUndone={async () => onSaved(await api.schedule())}
          />
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {sched.customized && (
            <button
              type="button"
              style={{ fontSize: 11, color: "var(--muted)" }}
              onClick={() => handleSave(true)}
              disabled={saving}
            >
              Reset to Defaults
            </button>
          )}
        </div>
      </div>

      {msg && (
        <div
          style={{
            fontSize: 12,
            color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)",
            marginTop: 4,
          }}
        >
          {msg}
        </div>
      )}
    </div>
  );
}

/** Standalone Scheduler View that loads schedule automatically and displays live status + pacing throttle */
export function SchedulerView({ onSaved }: { onSaved?: () => void }) {
  const [sched, setSched] = useState<ScheduleInfo | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const s = await api.schedule();
      setSched(s);
      setErr("");
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 15000);
    return () => clearInterval(interval);
  }, []);

  if (loading && !sched) {
    return (
      <div style={{ padding: 28, textAlign: "center", color: "var(--muted)", background: "var(--panel)", borderRadius: 10, border: "1px solid var(--border)" }}>
        ⏳ Loading Posting Pacing &amp; Scheduler…
      </div>
    );
  }

  if (err && !sched) {
    return (
      <div style={{ padding: 18, color: "var(--red)", background: "var(--row)", borderRadius: 8, border: "1px solid var(--border)" }}>
        ✕ Error loading schedule: {err}
      </div>
    );
  }

  if (!sched) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Pipeline Status Cards Overview */}
      {Object.keys(sched.pipelines || {}).length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
          {Object.entries(sched.pipelines).map(([name, p]) => (
            <div
              key={name}
              style={{
                background: "var(--panel)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: "12px 14px",
              }}
            >
              <div
                style={{
                  fontWeight: 700,
                  fontSize: 13,
                  marginBottom: 6,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span>📁 {name}</span>
                <span style={{ color: "#34d399", fontWeight: 600, fontSize: 12 }}>{p.ready} ready</span>
              </div>
              {p.next.slice(0, 3).map((n) => (
                <div key={n.id} style={{ fontSize: 11, display: "flex", gap: 8, marginTop: 4 }}>
                  <span style={{ color: "#34d399", whiteSpace: "nowrap" }}>
                    {fmtSlot(n.scheduled_at, sched.timezone)}
                  </span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    #{n.id} {n.title}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      <PacingThrottle
        sched={sched}
        onSaved={(updated) => {
          setSched(updated);
          onSaved?.();
        }}
      />
    </div>
  );
}

