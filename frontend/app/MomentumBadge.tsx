"use client";

import React from "react";
import { ReleaseMomentum } from "../lib/api";

const VARIANT_STYLES: Record<string, { bg: string; border: string; text: string }> = {
  critical: {
    bg: "rgba(239, 68, 68, 0.2)",
    border: "1px solid rgba(239, 68, 68, 0.5)",
    text: "#fca5a5",
  },
  approaching: {
    bg: "rgba(245, 158, 11, 0.2)",
    border: "1px solid rgba(245, 158, 11, 0.5)",
    text: "#fcd34d",
  },
  upcoming: {
    bg: "rgba(59, 130, 246, 0.2)",
    border: "1px solid rgba(59, 130, 246, 0.4)",
    text: "#93c5fd",
  },
  missed: {
    bg: "rgba(100, 116, 139, 0.25)",
    border: "1px solid rgba(100, 116, 139, 0.4)",
    text: "#cbd5e1",
  },
  neutral: {
    bg: "rgba(100, 116, 139, 0.15)",
    border: "1px solid rgba(100, 116, 139, 0.3)",
    text: "#94a3b8",
  },
};

export function MomentumBadge({
  momentum,
  compact = false,
  showWarning = true,
}: {
  momentum?: ReleaseMomentum | null;
  compact?: boolean;
  showWarning?: boolean;
}) {
  if (!momentum || !momentum.has_release_date) return null;

  const style = VARIANT_STYLES[momentum.badge_variant] || VARIANT_STYLES.neutral;

  return (
    <div style={{ display: "inline-flex", flexDirection: "column", gap: 3, maxWidth: "100%" }}>
      <div
        title={momentum.warning || (momentum.release_date ? `Official Release: ${momentum.release_date}` : undefined)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          padding: compact ? "1px 6px" : "2px 8px",
          borderRadius: 4,
          fontSize: compact ? 10 : 11,
          fontWeight: 600,
          background: style.bg,
          border: style.border,
          color: style.text,
          lineHeight: 1.3,
          width: "fit-content",
        }}
      >
        <span>{momentum.badge_text}</span>
        {momentum.release_date && (
          <span style={{ opacity: 0.75, fontSize: compact ? 9 : 10, fontWeight: 400 }}>
            ({momentum.release_date})
          </span>
        )}
      </div>

      {showWarning && momentum.scheduled_after_release && (
        <div
          style={{
            fontSize: 10,
            color: "#fca5a5",
            background: "rgba(220, 38, 38, 0.2)",
            border: "1px solid rgba(239, 68, 68, 0.5)",
            borderRadius: 4,
            padding: "2px 6px",
            lineHeight: 1.25,
            fontWeight: 500,
          }}
        >
          ⚠️ Scheduled after release date! Move up to capture pre-release momentum.
        </div>
      )}
    </div>
  );
}
