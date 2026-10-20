import type { QueueItem } from "./api";

// One chip per platform (roadmap C13, 2026-10-20): what happened on YouTube, Facebook and Instagram
// separately, instead of one status for the whole video.
export type Chip = { net: string; label: string; color: string; url?: string; why?: string };

export const ICON: Record<string, string> = { youtube: "▶️", facebook: "📘", instagram: "📸", tiktok: "🎵" };
const LINK = /✓ ([^:\s]+):(\w+) (https?:\S+)/g;

export function platformChips(item: Pick<QueueItem, "notes" | "publish_requests" | "catchup" | "status" | "accounts">): Chip[] {
  const chips = new Map<string, Chip>();
  const links = new Map<string, string>();
  for (const [acc, url] of (item.catchup?.done || [])) links.set(acc.split(":")[1], url);
  for (const m of (item.notes || "").matchAll(LINK)) links.set(m[2], m[3]);
  for (const [net, url] of links) chips.set(net, { net, label: "posted", color: "#34d399", url });
  for (const e of item.publish_requests || []) {
    if (e.skipped) {
      for (const acc of e.skipped) { const net = acc.split(":")[1]; if (!chips.has(net)) chips.set(net, { net, label: "waits for its slot", color: "var(--muted)" }); }
      continue;
    }
    const net = e.platforms?.[0];
    if (!e.profile || !net || chips.get(net)?.label === "posted") continue;
    const state = e.state || (e.request_id ? "accepted" : e.error ? "rejected" : "");
    const chip: Chip =
      state === "intent" ? { net, label: "sending…", color: "var(--yellow)" }
      : state === "accepted" ? { net, label: "submitted — waiting for the platform", color: "var(--yellow)" }
      : state === "completed" ? { net, label: "posted", color: "#34d399" }
      : state === "rejected" ? { net, label: "refused", color: "#f87171", why: e.error }
      : state === "unknown" ? { net, label: "unknown — check the platform", color: "#fb923c", why: e.error }
      : state === "confirmed_not_accepted" ? { net, label: "not posted", color: "#f87171", why: e.error }
      : { net, label: state || "sent", color: "var(--muted)" };
    chips.set(net, chip);
  }
  if (item.status === "ready" || item.status === "retry") {
    for (const acc of item.accounts || []) {
      const net = acc.split(":")[1];
      if (net && net !== "*" && !chips.has(net)) chips.set(net, { net, label: item.status === "retry" ? "to retry" : "waiting for its slot", color: "var(--muted)" });
    }
  }
  return [...chips.values()];
}

