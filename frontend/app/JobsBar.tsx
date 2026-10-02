"use client";

import React, { useEffect, useState } from "react";
import { StopButton } from "./StopButton";
import { api, Job, parseApiDate } from "../lib/api";

// Shown at the top of every tab: everything running or queued on the server,
// each with a Stop button. Stop is cooperative + kills ffmpeg/yt-dlp at once
// (backend/app/control.py), so it lands within a second or two.

const ACTIVE = ["queued", "running", "stopping"];

function ago(iso?: string | null): number {
  return iso ? (Date.now() - parseApiDate(iso)) / 1000 : Infinity;
}

function fmtElapsed(s?: number): string {
  if (!s && s !== 0) return "";
  const m = Math.floor(s / 60);
  return m ? `${m}m ${s % 60}s` : `${s}s`;
}

export function JobsBar({ scope }: { scope?: string }) {
  const [jobs, setJobs] = useState<Job[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      if (document.hidden) return;
      api.jobs().then((j) => alive && setJobs(j)).catch(() => {});
    };
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const shown = jobs.filter(
    (j) =>
      (!scope || j.scope === scope) &&
      (ACTIVE.includes(j.status) || ((j.status === "cancelled" || j.status === "error") && ago(j.finished_at) < 60))
  );
  if (shown.length === 0) return null;

  async function stop(j: Job) {
    await api.stopJob(j.id);
    setJobs(await api.jobs());
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, margin: "12px 0" }}>
      {shown.map((j) => {
        const active = ACTIVE.includes(j.status);
        const color = j.status === "error" ? "#fca5a5" : j.status === "cancelled" ? "#fcd34d" : "#c7d2fe";
        return (
          <div
            key={j.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              flexWrap: "wrap",
              padding: "8px 12px",
              borderRadius: 8,
              background: j.status === "error" ? "#450a0a" : j.status === "cancelled" ? "#422006" : "#1e1b4b",
              border: "1px solid var(--border)",
              fontSize: 12,
            }}
          >
            <span style={{ color, fontWeight: 700 }}>
              {j.status === "running" ? "⏳" : j.status === "queued" ? "⏸" : j.status === "stopping" ? "✋" : j.status === "cancelled" ? "■" : "✕"}{" "}
              {j.label}
            </span>
            <span style={{ color: "var(--muted)", flex: 1, minWidth: 120 }}>
              {j.status === "stopping" ? "stopping…" : j.status === "cancelled" ? "stopped" : j.message}
              {active && j.elapsed !== undefined ? ` · ${fmtElapsed(j.elapsed)}` : ""}
            </span>
            {active && j.status !== "stopping" && (
              <StopButton style={{ fontSize: 11, padding: "3px 12px" }}
                what="Stops at the next safe point; any render or download in progress is killed"
                onStop={() => stop(j)} />
            )}
          </div>
        );
      })}
    </div>
  );
}
