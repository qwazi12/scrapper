"use client";

import React, { useState } from "react";
import { api } from "../lib/api";

export function DrivePanel({ onIngested }: { onIngested: () => void }) {
  const [folderUrl, setFolderUrl] = useState(
    "https://drive.google.com/drive/u/5/folders/1_VLhKfSEYZFyPBXi7kYu4uw94JUronDP"
  );
  const [channelPipeline, setChannelPipeline] = useState("Movie Clips");
  const [autoApprove, setAutoApprove] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Extract folder ID from URL
  const folderIdMatch = folderUrl.match(/folders\/([a-zA-Z0-9_-]+)/);
  const folderId = folderIdMatch ? folderIdMatch[1] : folderUrl.trim();

  async function handleSync() {
    if (!folderId) {
      alert("Please provide a valid Google Drive folder link or Folder ID.");
      return;
    }
    setLoading(true);
    setStatus("Checking Google Drive folder access...");

    try {
      const res = await api.syncDrive({
        folder_url: folderUrl,
        folder_id: folderId,
        pipeline: channelPipeline,
        auto_approve: autoApprove,
      });
      setStatus(`✓ Sync triggered: ${res.message || "Scanning folder for video clips..."}`);
      onIngested();
    } catch (e: any) {
      // If backend endpoint is pending or needs auth
      setStatus(
        `⚠️ Google Drive Auth Needed: The folder is private to your Google account. Please share folder with service account or run the Mac worker sync script.`
      );
    } finally {
      setLoading(false);
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
            Pull video files directly from Google Drive folders into your posting queue and channels.
          </p>
        </div>
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
                Destination Channel Pipeline
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
                <option value="Movie Clips">Movie Clips (Flamingo Remix)</option>
                <option value="Abyss Declassified">Abyss Declassified</option>
                <option value="The ICK Room">The ICK Room</option>
                <option value="Default">Default</option>
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
            {loading ? "⏳ Connecting to Google Drive…" : "🔄 Sync & Pull Videos into Queue"}
          </button>

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
        </div>

        {/* Right: Authentication Instructions */}
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
            <span>🔑</span> Google Drive Access Requirements
          </div>
          <p style={{ margin: 0, color: "var(--muted)", lineHeight: 1.5 }}>
            Because Google Drive folders are private to your Google account, the automated scraper needs permission to read files. Choose one of the 3 easy setups:
          </p>

          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ background: "var(--bg)", padding: 10, borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontWeight: 600, color: "#38bdf8", marginBottom: 4 }}>
                1. Share Folder with Service Account (Recommended for Railway 24/7)
              </div>
              <div style={{ color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>
                Right-click the folder in Google Drive → <b>Share</b> → add your Google Cloud service account email as <b>Viewer</b>.
              </div>
            </div>

            <div style={{ background: "var(--bg)", padding: 10, borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontWeight: 600, color: "#34d399", marginBottom: 4 }}>
                2. Residential Mac Worker Sync (Zero Setup)
              </div>
              <div style={{ color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>
                Run the local sync command on your Mac. Since your Mac browser is already logged in to Google account <code className="mono">/u/5/</code>, it can download and push straight to Railway:
                <div style={{ background: "#0b0d11", padding: "6px 8px", borderRadius: 4, marginTop: 6, fontFamily: "monospace", color: "#e6e9ef" }}>
                  python3 scripts/sync_drive.py --folder-id {folderId || "YOUR_FOLDER_ID"}
                </div>
              </div>
            </div>

            <div style={{ background: "var(--bg)", padding: 10, borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontWeight: 600, color: "#fbbf24", marginBottom: 4 }}>
                3. Public Link Sharing
              </div>
              <div style={{ color: "var(--muted)", fontSize: 11, lineHeight: 1.4 }}>
                In Drive: Right-click folder → <b>Share</b> → General Access: <b>Anyone with the link (Viewer)</b>.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
