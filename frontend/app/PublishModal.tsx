"use client";

import React, { useEffect, useState } from "react";
import { api, Compilation, fmtBytes, fmtDuration, SocialAccount } from "../lib/api";
import { TargetPicker, targetNames } from "./TargetPicker";
import { askConfirm } from "../lib/dialogs";

export function PublishModal({
  compilation,
  onClose,
  onSuccess,
}: {
  compilation: Compilation | null;
  onClose: () => void;
  onSuccess: () => void;
}) {
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [loadingAccounts, setLoadingAccounts] = useState(true);
  const [configured, setConfigured] = useState(true);
  const [targets, setTargets] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [privacy, setPrivacy] = useState("public");

  const [aiPrompt, setAiPrompt] = useState("");
  const [generatingAi, setGeneratingAi] = useState(false);

  const [content, setContent] = useState("");
  const [isScheduled, setIsScheduled] = useState(false);
  const [scheduledAt, setScheduledAt] = useState("");

  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!compilation) return;
    setError(null);
    setSuccessMsg(null);
    setLoadingAccounts(true);

    api
      .socialAccounts()
      .then((res) => {
        setConfigured(res.configured !== false);
        setAccounts(res.accounts || []);
        setProfiles(res.profiles || []);
        if (res.privacy) setPrivacy(res.privacy);
        // Nothing pre-ticked: the owner picks every destination.
        setTargets([]);
      })
      .catch((err) => {
        setError(err.message || "Failed to load connected social accounts");
      })
      .finally(() => {
        setLoadingAccounts(false);
      });
  }, [compilation]);

  if (!compilation) return null;

  async function handleGenerateAi() {
    if (!compilation) return;
    setGeneratingAi(true);
    setError(null);
    try {
      const res = await api.generateMetadata(compilation.id, aiPrompt.trim() || undefined);
      setContent(res.full_text || `${res.title}\n\n${res.caption}\n\n${res.hashtags.join(" ")}`);
    } catch (err: any) {
      setError(err.message || "Failed to generate AI metadata");
    } finally {
      setGeneratingAi(false);
    }
  }

  async function handlePublish() {
    if (!compilation) return;
    if (targets.length === 0) {
      setError("Please select at least one social account.");
      return;
    }
    if (!content.trim()) {
      setError("Please enter a caption / content for your post.");
      return;
    }

    let isoScheduled: string | undefined = undefined;
    if (isScheduled) {
      if (!scheduledAt) {
        setError("Please choose a schedule date and time.");
        return;
      }
      try {
        isoScheduled = new Date(scheduledAt).toISOString();
      } catch {
        setError("Invalid schedule date/time format.");
        return;
      }
    }

    // Name exactly the picked channels — what the server will actually use.
    const names = targetNames(targets, accounts);
    const when = isoScheduled ? `on ${new Date(isoScheduled).toLocaleString()}` : "now";
    if (!await askConfirm(`Publish this compilation ${when} to ${names} as ${privacy} via Upload-Post?`)) return;

    setPublishing(true);
    setError(null);
    setSuccessMsg(null);

    try {
      const res = await api.publishSocial(
        compilation.id,
        targets,
        content.trim(),
        isoScheduled
      );

      setSuccessMsg(
        `Submitted to Upload-Post (post #${res.id}). The real result per channel appears in Live Activity Logs once the platforms confirm.`
      );
      onSuccess();
      setTimeout(() => {
        onClose();
      }, 2000);
    } catch (err: any) {
      setError(err.message || "Failed to publish post.");
    } finally {
      setPublishing(false);
    }
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.75)",
        backdropFilter: "blur(4px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          width: "100%",
          maxWidth: 620,
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
          boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: "16px 20px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "var(--panel2)",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 16, fontWeight: 700 }}>🚀 Post & Schedule Compilation</span>
              <span
                style={{
                  padding: "2px 8px",
                  background: "var(--chip)",
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--accent)",
                }}
              >
                #{compilation.id}
              </span>
            </div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
              {compilation.orientation} · {compilation.clip_ids.length} clips ·{" "}
              {fmtDuration(compilation.duration)} · {fmtBytes(compilation.size_bytes)}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--muted)",
              fontSize: 18,
              padding: 4,
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>

        {/* Body Content */}
        <div style={{ padding: "18px 20px", overflowY: "auto", display: "grid", gap: 16 }}>
          {/* Target Accounts Selection */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 12, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--muted)" }}>
                Post To (Upload-Post profile or channels)
              </span>
              <a
                href="https://app.upload-post.com/manage-users"
                target="_blank"
                rel="noreferrer"
                style={{ fontSize: 11, color: "var(--blue)" }}
              >
                Manage in Upload-Post ↗
              </a>
            </div>

            {loadingAccounts ? (
              <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>Loading accounts from Upload-Post…</div>
            ) : !configured ? (
              <div
                style={{
                  padding: 12,
                  background: "#2a2114",
                  border: "1px solid var(--yellow)",
                  borderRadius: 6,
                  color: "var(--yellow)",
                  fontSize: 12,
                }}
              >
                ⚠️ <strong>UPLOADPOST_API_KEY</strong> is not set in Railway environment variables. Publishing is blocked until it is.
              </div>
            ) : accounts.length === 0 ? (
              <div
                style={{
                  padding: 12,
                  background: "var(--row)",
                  border: "1px dashed var(--border)",
                  borderRadius: 6,
                  color: "var(--muted)",
                  fontSize: 12,
                }}
              >
                No accounts connected in Upload-Post yet.{" "}
                <a href="https://app.upload-post.com/manage-users" target="_blank" rel="noreferrer">
                  Connect TikTok, YouTube, or Instagram in Upload-Post
                </a>
              </div>
            ) : (
              <TargetPicker accounts={accounts} profiles={profiles} value={targets} onChange={setTargets} />
            )}
          </div>

          {/* AI Metadata Generator */}
          <div
            style={{
              padding: 12,
              background: "linear-gradient(145deg, #181d26, #14171e)",
              border: "1px solid #303746",
              borderRadius: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontWeight: 600, fontSize: 12, color: "#a5b4fc" }}>
                ✨ AI Viral Metadata Generator
              </span>
              <button
                type="button"
                onClick={handleGenerateAi}
                disabled={generatingAi}
                style={{
                  background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
                  color: "#ffffff",
                  border: "none",
                  fontWeight: 600,
                  padding: "5px 12px",
                }}
              >
                {generatingAi ? "Generating…" : "Auto-Generate"}
              </button>
            </div>
            <input
              type="text"
              placeholder="Optional: custom vibe or instructions (e.g. gym humor, high energy, question hook)"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              style={{ fontSize: 12, padding: "6px 10px" }}
            />
          </div>

          {/* Caption & Content */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <label style={{ fontWeight: 600, fontSize: 12, color: "var(--muted)", textTransform: "uppercase", letterSpacing: 0.5 }}>
                Post Caption & Hashtags
              </label>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{content.length} characters</span>
            </div>
            <textarea
              rows={6}
              placeholder="Write your post caption, hook, and hashtags here (or click Auto-Generate above)…"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              style={{ resize: "vertical", fontSize: 13, lineHeight: 1.5 }}
            />
          </div>

          {/* Schedule or Post Immediately */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 10,
              padding: 12,
              background: "var(--row)",
              border: "1px solid var(--border)",
              borderRadius: 8,
            }}
          >
            <div style={{ display: "flex", gap: 20 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12 }}>
                <input
                  type="radio"
                  name="scheduleMode"
                  checked={!isScheduled}
                  onChange={() => setIsScheduled(false)}
                  style={{ width: 14, height: 14 }}
                />
                Publish Now (Immediate)
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12 }}>
                <input
                  type="radio"
                  name="scheduleMode"
                  checked={isScheduled}
                  onChange={() => setIsScheduled(true)}
                  style={{ width: 14, height: 14 }}
                />
                Schedule for Later
              </label>
            </div>

            {isScheduled && (
              <div>
                <input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(e) => setScheduledAt(e.target.value)}
                  style={{ width: "auto", fontSize: 12 }}
                />
                <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                  Upload-Post schedules up to 365 days ahead.
                </div>
              </div>
            )}
          </div>

          {/* Error & Success Messages */}
          {error && (
            <div
              style={{
                padding: "10px 12px",
                background: "#2b1414",
                border: "1px solid var(--red)",
                borderRadius: 6,
                color: "var(--red)",
                fontSize: 12,
              }}
            >
              ✕ {error}
            </div>
          )}

          {successMsg && (
            <div
              style={{
                padding: "10px 12px",
                background: "#122a18",
                border: "1px solid var(--accent)",
                borderRadius: 6,
                color: "var(--accent)",
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              ✓ {successMsg}
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div
          style={{
            padding: "14px 20px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            justifyContent: "flex-end",
            gap: 10,
            background: "var(--panel2)",
          }}
        >
          <button type="button" onClick={onClose} disabled={publishing}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            style={{
              background: "#7c3aed",
              borderColor: "#6d28d9",
              color: "#ffffff",
              fontWeight: 600,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
            onClick={handlePublish}
            disabled={publishing || targets.length === 0 || !content.trim()}
          >
            {publishing
              ? "Uploading to Upload-Post…"
              : isScheduled
              ? `Schedule (${targets.length} target${targets.length === 1 ? "" : "s"})`
              : `Publish Now (${targets.length} target${targets.length === 1 ? "" : "s"})`}
          </button>
        </div>
      </div>
    </div>
  );
}
