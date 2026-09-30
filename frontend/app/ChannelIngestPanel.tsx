"use client";

import React, { useState } from "react";
import { api } from "../lib/api";

const PRESET_PARENT_FOLDERS = [
  { name: "Movie Clips", id: "1kuOKRQQRL0ws5aOVqwkdUzdnfj5KQGjo" },
  { name: "Flamingo", id: "1Mmrem-JzM1tBArIJ-GrcaDX7qKRhTC5F" },
  { name: "Second Track Clips", id: "1_HWppeJLcrGAo--UFc3u-eUTpvE0T0S6" },
  { name: "TikTok Retry", id: "1rqUIlbpy_MR7Cm0FYCA5TmhECWIgWlIN" },
];

export function ChannelIngestPanel({ onIngested }: { onIngested: () => void }) {
  const [url, setUrl] = useState("");
  const [parentFolder, setParentFolder] = useState(PRESET_PARENT_FOLDERS[0].id);
  const [channelName, setChannelName] = useState("");
  const [maxVideos, setMaxVideos] = useState(25);
  const [autoApprove, setAutoApprove] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [result, setResult] = useState<any | null>(null);

  const selectedFolderName =
    PRESET_PARENT_FOLDERS.find((f) => f.id === parentFolder)?.name || "Movie Clips";

  async function handleIngest() {
    if (!url.trim()) {
      alert("Please enter a YouTube channel, shorts, or video URL.");
      return;
    }
    setLoading(true);
    setStatus("🚀 Starting channel scrape with yt-dlp & connecting to Google Drive...");
    setResult(null);

    try {
      const res = await api.ingestChannel({
        url: url.trim(),
        parent_folder_id: parentFolder,
        parent_folder_name: selectedFolderName,
        channel_name: channelName.trim() || undefined,
        max_videos: Number(maxVideos) || 25,
        auto_approve: autoApprove,
      });

      setStatus(`✓ ${res.message}`);
      setResult(res);
      setUrl("");
      onIngested();
    } catch (err: any) {
      setStatus(`❌ Ingestion failed: ${err.message}`);
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
            <span>📥</span> Bulk Channel Scraper & Auto-Drive Ingestion
          </h2>
          <p style={{ margin: 0, color: "var(--muted)", fontSize: 12 }}>
            Scrape a creator or channel&apos;s shorts, automatically organize into Google Drive subfolders, and queue into SocialPilot AI with zero persistent disk usage.
          </p>
        </div>
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
          ✓ Auto Drive Upload & Local Cleanup
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 20 }}>
        {/* Left: Ingest Controls */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
              Channel Shorts or Video URL
            </label>
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://www.youtube.com/@ChannelName/shorts"
              style={{ fontSize: 13 }}
            />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                Target Parent Drive Folder
              </label>
              <select
                value={parentFolder}
                onChange={(e) => setParentFolder(e.target.value)}
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
                {PRESET_PARENT_FOLDERS.map((f) => (
                  <option key={f.id} value={f.id}>
                    📁 {f.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                Custom Channel Handle (Optional)
              </label>
              <input
                type="text"
                value={channelName}
                onChange={(e) => setChannelName(e.target.value)}
                placeholder="e.g. @MyChannel"
                style={{ fontSize: 13 }}
              />
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                Max Videos to Scrape
              </label>
              <select
                value={maxVideos}
                onChange={(e) => setMaxVideos(Number(e.target.value))}
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
                <option value={10}>10 videos</option>
                <option value={25}>25 videos (Standard Batch)</option>
                <option value={50}>50 videos</option>
                <option value={100}>100 videos</option>
              </select>
            </div>

            <div>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                Initial Queue Status
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
            onClick={handleIngest}
            disabled={loading}
            style={{
              padding: "10px 18px",
              fontSize: 13,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 8,
            }}
          >
            {loading ? "⏳ Scraping Channel & Uploading to Drive…" : "🚀 Scrape & File Directly to Google Drive"}
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

          {result && result.items && result.items.length > 0 && (
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
                Ingested {result.items.length} videos into &apos;{result.parent_folder} / {result.channel}&apos;:
              </div>
              {result.items.map((it: any, idx: number) => (
                <div
                  key={idx}
                  style={{
                    padding: "4px 0",
                    borderBottom: "1px solid var(--border)",
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "70%" }}>
                    {it.title}
                  </span>
                  <a
                    href={it.drive_link}
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: "#38bdf8", textDecoration: "none", fontSize: 10 }}
                  >
                    View in Drive ↗
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Explanatory / OmniStream workflow */}
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
            <span>⚙️</span> OmniStream Ingestion Architecture
          </div>
          <p style={{ margin: 0, color: "var(--muted)", lineHeight: 1.5 }}>
            This follows the exact OmniStream &amp; SocialPilot automated workflow:
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ background: "var(--bg)", padding: 8, borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontWeight: 600, color: "#38bdf8" }}>1. Smart Subfolder Resolution</div>
              <div style={{ color: "var(--muted)", fontSize: 11 }}>
                Detects the channel handle (e.g. <code className="mono">@PixelDrift-f3c</code>) and finds or creates its folder under <code className="mono">Movie Clips</code> in Google Drive.
              </div>
            </div>
            <div style={{ background: "var(--bg)", padding: 8, borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontWeight: 600, color: "#34d399" }}>2. Optimal 1080p Download &amp; Direct Upload</div>
              <div style={{ color: "var(--muted)", fontSize: 11 }}>
                Downloads merged 1080p MP4s to a temporary directory, uploads to Google Drive with Editor permissions, and immediately purges local temp files.
              </div>
            </div>
            <div style={{ background: "var(--bg)", padding: 8, borderRadius: 6, border: "1px solid var(--border)" }}>
              <div style={{ fontWeight: 600, color: "#fbbf24" }}>3. Instant Queue Integration</div>
              <div style={{ color: "var(--muted)", fontSize: 11 }}>
                Adds the item to SocialPilot AI with clickable Drive link, clean title, extracted hashtags, and exempts it from retention sweeps.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
