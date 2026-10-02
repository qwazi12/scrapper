"use client";

import React, { useState } from "react";
import { StopButton } from "./StopButton";
import { api } from "../lib/api";

const DETECTED_CHANNELS = [
  "@VynixAE",
  "@PixelDrift-f3c",
  "@SolarrEditss",
  "@AlphaReels-1",
  "@EditAetheris",
  "@CoruscateCuts",
  "@FrameLegion",
  "@roebutt",
  "@TheUsJournal17",
  "@SceneVale",
  "@QianaLucy",
  "@clipscav",
  "@hanganhoang3071",
  "@comet-cinema",
];

export function DrivePanel({ onIngested }: { onIngested: () => void }) {
  const [folderUrl, setFolderUrl] = useState(
    "https://drive.google.com/drive/folders/1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo?usp=sharing"
  );
  const [channelPipeline, setChannelPipeline] = useState("All Channels (Auto-Detect Subfolders)");
  const [autoApprove, setAutoApprove] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [syncSummary, setSyncSummary] = useState<any | null>(null);

  // Extract folder ID from URL
  const folderIdMatch = folderUrl.match(/folders\/([a-zA-Z0-9_-]+)/);
  const folderId = folderIdMatch ? folderIdMatch[1] : folderUrl.trim();

  async function handleSync() {
    if (!folderId) {
      alert("Please provide a valid Google Drive folder link or Folder ID.");
      return;
    }
    setLoading(true);
    setStatus("Connecting to Google Drive API & scanning folders...");
    setSyncSummary(null);

    try {
      const pipelineParam =
        channelPipeline === "All Channels (Auto-Detect Subfolders)"
          ? "Movie Clips"
          : channelPipeline;

      const started = await api.syncDrive({
        folder_url: folderUrl,
        folder_id: folderId,
        pipeline: pipelineParam,
        auto_approve: autoApprove,
      });
      setJobId(started.job_id);
      const res = await api.waitJob(started.job_id, (j) => j.message && setStatus(`⏳ ${j.message}`));

      setStatus(`✓ ${res.message || "Drive sync completed successfully."}`);
      if ((res as any).channels) {
        setSyncSummary(res);
      }
      onIngested();
    } catch (e: any) {
      setStatus(e.message === "Stopped"
        ? "■ Sync stopped. Videos already added stay in the queue (↶ Undo there removes them)."
        : `⚠️ Sync notice: ${e.message || "Could not complete cloud sync."}`);
      onIngested();
    } finally {
      setLoading(false);
      setJobId(null);
    }
  }

  return (
    <div
      style={{
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: 24,
        display: "flex",
        flexDirection: "column",
        gap: 20,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 style={{ margin: "0 0 6px", fontSize: 18, display: "flex", alignItems: "center", gap: 8 }}>
            <span>☁️</span> Google Drive Video Ingestion
          </h2>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 12 }}>
            Scan Google Drive folders and automatically map channel subfolders (<code className="mono">@VynixAE</code>, <code className="mono">@PixelDrift</code>, etc.) into the SocialPilot posting queue.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span
            style={{
              fontSize: 11,
              background: "#064e3b",
              color: "#34d399",
              border: "1px solid #059669",
              padding: "4px 10px",
              borderRadius: 6,
              fontWeight: 600,
            }}
          >
            ✓ Service Account Connected (Editor)
          </span>
          <span
            style={{
              fontSize: 11,
              background: "#1e293b",
              color: "#38bdf8",
              border: "1px solid #334155",
              padding: "4px 10px",
              borderRadius: 6,
              fontWeight: 500,
            }}
          >
            Folder ID: <span className="mono">{folderId || "None"}</span>
          </span>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 20 }}>
        {/* Left: Input Form */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
              Google Drive Folder Link or ID
            </label>
            <input
              type="text"
              value={folderUrl}
              onChange={(e) => setFolderUrl(e.target.value)}
              placeholder="https://drive.google.com/drive/folders/..."
              style={{ fontSize: 13 }}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                Channel / Subfolder Mode
              </label>
              <select
                value={channelPipeline}
                onChange={(e) => setChannelPipeline(e.target.value)}
                style={{
                  width: "100%",
                  padding: 8,
                  background: "var(--bg)",
                  color: "var(--text)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                <option value="All Channels (Auto-Detect Subfolders)">
                  ✨ All 14 Channels (Auto-Detect Subfolders)
                </option>
                <option value="Movie Clips">Movie Clips (Root)</option>
                {DETECTED_CHANNELS.map((ch) => (
                  <option key={ch} value={ch}>
                    {ch}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                Initial Status
              </label>
              <select
                value={autoApprove ? "ready" : "review"}
                onChange={(e) => setAutoApprove(e.target.value === "ready")}
                style={{
                  width: "100%",
                  padding: 8,
                  background: "var(--bg)",
                  color: "var(--text)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  fontSize: 13,
                }}
              >
                <option value="review">👁 Needs Review</option>
                <option value="ready">● Ready to Post</option>
              </select>
            </div>
          </div>

          <button
            className="primary"
            onClick={handleSync}
            disabled={loading}
            style={{ padding: "10px 18px", fontSize: 13, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}
          >
            {loading ? "⏳ Scanning & Syncing Google Drive…" : "🔄 Sync & Pull Videos into Queue"}
          </button>
          {loading && jobId && (
            <StopButton what="Videos already added are kept" onStop={() => api.stopJob(jobId)} />
          )}

          {status && (
            <div
              style={{
                padding: "10px 14px",
                borderRadius: 6,
                background: status.startsWith("✓") ? "#064e3b" : "#451a03",
                color: status.startsWith("✓") ? "#34d399" : "#fbbf24",
                border: `1px solid ${status.startsWith("✓") ? "#059669" : "#78350f"}`,
                fontSize: 12,
                lineHeight: 1.4,
              }}
            >
              {status}
            </div>
          )}

          {syncSummary && syncSummary.channels && (
            <div
              style={{
                background: "var(--bg)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                padding: 12,
                maxHeight: 180,
                overflowY: "auto",
                fontSize: 11,
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: 8, color: "var(--accent)" }}>
                Channel Ingestion Breakdown ({syncSummary.added} added, {syncSummary.skipped} already present):
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                {syncSummary.channels.map((c: any) => (
                  <div
                    key={c.channel}
                    style={{
                      background: "var(--panel)",
                      padding: "4px 8px",
                      borderRadius: 4,
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <span>{c.channel}</span>
                    <span style={{ color: "#34d399", fontWeight: 600 }}>+{c.added} new</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right: Detected Channels & Quick Mac Worker */}
        <div
          style={{
            background: "var(--panel2)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 12,
            fontSize: 12,
          }}
        >
          <div style={{ fontWeight: 600, color: "var(--text)", fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
            <span>📺</span> 14 Detected Movie Clip Channels
          </div>
          <p style={{ margin: 0, color: "var(--muted)", lineHeight: 1.5 }}>
            Subfolders are automatically detected as distinct channels. Videos will be routed directly to each channel&apos;s tab in SocialPilot AI:
          </p>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(2, 1fr)",
              gap: 6,
              background: "var(--bg)",
              padding: 10,
              borderRadius: 6,
              border: "1px solid var(--border)",
              maxHeight: 140,
              overflowY: "auto",
            }}
          >
            {DETECTED_CHANNELS.map((ch) => (
              <div key={ch} style={{ color: "#38bdf8", fontFamily: "monospace", fontSize: 11 }}>
                • {ch}
              </div>
            ))}
          </div>

          <div style={{ background: "var(--bg)", padding: 10, borderRadius: 6, border: "1px solid var(--border)" }}>
            <div style={{ fontWeight: 600, color: "#34d399", marginBottom: 4 }}>
              Mac Terminal Sync Command
            </div>
            <div style={{ color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>
              You can also trigger a sync anytime directly from your terminal:
              <div style={{ background: "#0b0d11", padding: "6px 8px", borderRadius: 4, marginTop: 6, fontFamily: "monospace", color: "#e6e9ef" }}>
                python3 scripts/sync_drive.py
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
