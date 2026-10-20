"use client";

import type { QueueItem } from "../lib/api";
import { ICON, platformChips } from "../lib/platformStatus";

export function PlatformStatus({ item }: { item: QueueItem }) {
  const chips = platformChips(item);
  if (!chips.length) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }} aria-label="Status on each platform">
      {chips.map((c) => (
        <span key={c.net} title={c.why || undefined}
              style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, border: `1px solid ${c.color}`, color: c.color }}>
          {ICON[c.net] || "•"} {c.net}: {c.url ? <a href={c.url} target="_blank" rel="noreferrer" style={{ color: c.color }}>{c.label} ↗</a> : c.label}
        </span>
      ))}
    </div>
  );
}
