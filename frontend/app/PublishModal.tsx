"use client";

import React, { useEffect, useState } from "react";
import { api, Compilation, fmtBytes, fmtDuration, SocialAccount } from "../lib/api";

const NETWORK_COLORS: Record<string, { bg: string; text: string; icon: string }> = {
  tiktok: { bg: "#000000", text: "#00f2fe", icon: "🎵 TikTok" },
  youtube: { bg: "#ff0000", text: "#ffffff", icon: "▶️ YouTube" },
  instagram: { bg: "#e1306c", text: "#ffffff", icon: "📸 Instagram" },
  x: { bg: "#14171a", text: "#1da1f2", icon: "𝕏 Twitter" },
  facebook: { bg: "#1877f2", text: "#ffffff", icon: "👤 Facebook" },
  threads: { bg: "#000000", text: "#ffffff", icon: "🧵 Threads" },
  linkedin: { bg: "#0077b5", text: "#ffffff", icon: "💼 LinkedIn" },
  bluesky: { bg: "#0085ff", text: "#ffffff", icon: "🦋 Bluesky" },
  pinterest: { bg: "#e60023", text: "#ffffff", icon: "📌 Pinterest" },
};

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
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(new Set());

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
        const accs = res.accounts || [];
        setAccounts(accs);
        // By default, select all active accounts
        const initial = new Set<string>();
        accs.forEach((a) => {
          if (a.isActive !== 0 && a.isActive !== false) {
            initial.add(a.id);
          }
        });
        setSelectedAccounts(initial);
      })
      .catch((err) => {
        setError(err.message || "Failed to load connected social accounts");
      })
      .finally(() => {
        setLoadingAccounts(false);
      });
  }, [compilation]);

  if (!compilation) return null;

  function toggleAccount(id: string) {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

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
    if (selectedAccounts.size === 0) {
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

    setPublishing(true);
    setError(null);
    setSuccessMsg(null);

    try {
      const res = await api.publishSocial(
        compilation.id,
        Array.from(selectedAccounts),
        content.trim(),
        isoScheduled
      );

      const statusText = isScheduled ? "scheduled" : "published";
      setSuccessMsg(
        `Successfully ${statusText}! (Post ID: ${res.outstand_post_id || res.id})`
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
                Target Social Accounts
              </span>
              <a
                href="https://www.outstand.so/app"
                target="_blank"
                rel="noreferrer"
                style={{ fontSize: 11, color: "var(--blue)" }}
              >
                Manage in Outstand ↗
              </a>
            </div>

            {loadingAccounts ? (
              <div style={{ color: "var(--muted)", fontSize: 12, padding: "8px 0" }}>Loading accounts from Outstand…</div>
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
                ⚠️ <strong>OUTSTAND_API_KEY</strong> is not set in Railway environment variables. Add your key in Railway to load accounts and post directly.
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
                No accounts connected in Outstand yet.{" "}
                <a href="https://www.outstand.so/app" target="_blank" rel="noreferrer">
                  Connect TikTok, YouTube, or Instagram in Outstand
                </a>
              </div>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {accounts.map((acc) => {
                  const isSelected = selectedAccounts.has(acc.id);
                  const net = NETWORK_COLORS[acc.network.toLowerCase()] || {
                    bg: "#232833",
                    text: "#ffffff",
                    icon: acc.network,
                  };
                  return (
                    <div
                      key={acc.id}
                      onClick={() => toggleAccount(acc.id)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "6px 12px",
                        borderRadius: 6,
                        cursor: "pointer",
                        border: isSelected ? "1px solid var(--accent)" : "1px solid var(--border)",
                        background: isSelected ? "var(--row-alt)" : "var(--panel2)",
                        transition: "all 0.15s ease",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => {}}
                        style={{ width: 14, height: 14, cursor: "pointer" }}
                      />
                      <span style={{ fontSize: 12, fontWeight: 600 }}>{net.icon}</span>
                      <span style={{ fontSize: 12, color: "var(--muted)" }}>@{acc.username}</span>
                    </div>
                  );
                })}
              </div>
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
                  Outstand supports scheduling up to 30 days in advance.
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
            disabled={publishing || selectedAccounts.size === 0 || !content.trim()}
          >
            {publishing
              ? "Publishing to Outstand…"
              : isScheduled
              ? `Schedule (${selectedAccounts.size} account${selectedAccounts.size === 1 ? "" : "s"})`
              : `Publish Now (${selectedAccounts.size} account${selectedAccounts.size === 1 ? "" : "s"})`}
          </button>
        </div>
      </div>
    </div>
  );
}
