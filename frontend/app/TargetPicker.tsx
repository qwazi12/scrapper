"use client";

import React, { useState } from "react";
import { SocialAccount } from "../lib/api";

// Targets are Upload-Post ids: "<profile>:*" = every channel in that profile,
// "<profile>:<network>" = one specific channel.
export const WHOLE = "*";

// globals.css gives every input width:100%; checkboxes must stay box-sized.
const CHECKBOX: React.CSSProperties = { width: "auto", flex: "0 0 auto", padding: 0, margin: "2px 0 0" };

const NETWORK_ICON: Record<string, string> = {
  youtube: "▶️",
  tiktok: "🎵",
  instagram: "📸",
  facebook: "📘",
  x: "𝕏",
  twitter: "𝕏",
  linkedin: "💼",
  threads: "🧵",
  pinterest: "📌",
  bluesky: "🦋",
};

function cap(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export function profileOf(id: string): string {
  return id.split(":")[0];
}

export function groupProfiles(accounts: SocialAccount[], profiles?: string[]): Record<string, SocialAccount[]> {
  const out: Record<string, SocialAccount[]> = {};
  for (const p of profiles || []) out[p] = [];
  for (const a of accounts) (out[a.profile || profileOf(a.id)] ||= []).push(a);
  return out;
}

/** Human description of one target id, e.g. "📁 default · all channels". */
export function describeTarget(
  id: string,
  accounts: SocialAccount[]
): { icon: string; label: string; detail: string; connected: boolean } {
  const [profile, network] = id.split(":");
  if (network === WHOLE) {
    const chans = accounts.filter((a) => (a.profile || profileOf(a.id)) === profile);
    return {
      icon: "📁",
      label: `${profile} · all channels`,
      detail: chans.length
        ? chans.map((a) => `${a.nickname || a.username} (${cap(a.network)})`).join(", ")
        : "no channels connected",
      connected: chans.length > 0,
    };
  }
  const acc = accounts.find((a) => a.id === id);
  return {
    icon: NETWORK_ICON[(network || "").toLowerCase()] || "🌐",
    label: acc ? `${acc.nickname || acc.username}` : id,
    detail: acc ? `${cap(acc.network)} · profile ${profile}` : "not connected in Upload-Post",
    connected: !!acc,
  };
}

/** One line naming every destination, for confirm dialogs. */
export function targetNames(ids: string[], accounts: SocialAccount[]): string {
  return ids
    .map((id) => {
      const d = describeTarget(id, accounts);
      return id.endsWith(`:${WHOLE}`) ? `profile "${profileOf(id)}" (${d.detail})` : `${d.label} (${d.detail})`;
    })
    .join("; ");
}

export function TargetChip({ id, accounts }: { id: string; accounts: SocialAccount[] }) {
  const d = describeTarget(id, accounts);
  return (
    <span
      title={d.detail}
      style={{
        fontSize: 10,
        background: d.connected ? "#1e1b4b" : "#450a0a",
        color: d.connected ? "#c7d2fe" : "#fca5a5",
        padding: "2px 6px",
        borderRadius: 4,
        display: "inline-flex",
        flexDirection: "column",
        width: "fit-content",
        lineHeight: 1.35,
      }}
    >
      <span style={{ fontWeight: 600 }}>
        {d.icon} {d.label}
        {!d.connected && " ⚠"}
      </span>
      <span style={{ opacity: 0.75 }}>{d.detail}</span>
    </span>
  );
}

/**
 * Pick by Upload-Post profile (all its channels), or open a profile to pick
 * specific channels. Starts with whatever `value` holds — callers pass [] so
 * nothing is ever pre-ticked.
 */
export function TargetPicker({
  accounts,
  profiles,
  value,
  onChange,
  blockedProfiles = [],
}: {
  accounts: SocialAccount[];
  profiles?: string[];
  value: string[];
  onChange: (next: string[]) => void;
  /** Profiles that must never be offered here (the Posting Queue blocks "mk"). */
  blockedProfiles?: string[];
}) {
  const grouped = groupProfiles(accounts, profiles);
  for (const b of blockedProfiles) delete grouped[b];
  const [open, setOpen] = useState<Record<string, boolean>>({});

  function toggle(id: string) {
    onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  }

  function toggleWhole(profile: string) {
    const whole = `${profile}:${WHOLE}`;
    if (value.includes(whole)) {
      onChange(value.filter((v) => v !== whole));
    } else {
      // The profile covers its channels; drop individual picks inside it.
      onChange([...value.filter((v) => profileOf(v) !== profile), whole]);
    }
  }

  const names = Object.keys(grouped);
  if (names.length === 0) {
    return <div style={{ fontSize: 12, color: "var(--muted)" }}>No Upload-Post profiles found.</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {names.map((profile) => {
        const chans = grouped[profile];
        const whole = value.includes(`${profile}:${WHOLE}`);
        const specific = chans.filter((a) => value.includes(a.id)).length;
        const expanded = open[profile] || specific > 0;
        return (
          <div
            key={profile}
            style={{
              border: whole || specific ? "1px solid var(--accent)" : "1px solid var(--border)",
              borderRadius: 8,
              background: "var(--bg)",
              padding: "8px 10px",
            }}
          >
            <label style={{ display: "flex", alignItems: "flex-start", gap: 10, cursor: chans.length ? "pointer" : "not-allowed" }}>
              <input
                type="checkbox"
                checked={whole}
                disabled={chans.length === 0}
                onChange={() => toggleWhole(profile)}
                style={CHECKBOX}
              />
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span style={{ fontSize: 12, fontWeight: 700 }}>
                  📁 Profile “{profile}” — all channels ({chans.length})
                </span>
                <span style={{ fontSize: 11, color: "var(--muted)" }}>
                  {chans.length
                    ? chans.map((a) => `${NETWORK_ICON[a.network] || "🌐"} ${a.nickname || a.username}`).join("  ·  ")
                    : "No channels connected to this profile yet"}
                </span>
              </span>
            </label>

            {chans.length > 0 && (
              <div style={{ marginTop: 6, marginLeft: 24 }}>
                <button
                  type="button"
                  onClick={() => setOpen({ ...open, [profile]: !expanded })}
                  style={{ fontSize: 10, padding: "1px 6px", background: "transparent", border: "none", color: "var(--blue)" }}
                >
                  {expanded ? "▾" : "▸"} pick specific channels{specific ? ` (${specific} picked)` : ""}
                </button>
                {expanded && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                    {chans.map((a) => (
                      <label
                        key={a.id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          fontSize: 12,
                          opacity: whole ? 0.5 : 1,
                          cursor: whole ? "not-allowed" : "pointer",
                        }}
                        title={whole ? "Covered by the whole-profile pick" : undefined}
                      >
                        <input
                          type="checkbox"
                          checked={whole || value.includes(a.id)}
                          disabled={whole}
                          onChange={() => toggle(a.id)}
                          style={CHECKBOX}
                        />
                        <span>{NETWORK_ICON[a.network] || "🌐"}</span>
                        <span style={{ fontWeight: 600 }}>{a.nickname || a.username}</span>
                        <span style={{ color: "var(--muted)", fontSize: 11 }}>{cap(a.network)}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
