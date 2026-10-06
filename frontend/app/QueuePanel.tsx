"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { StopButton } from "./StopButton";
import { api, apiBase, BulkAiStatus, mediaUrl, parseApiDate, QueueItem, QueueThumbOptions, ScheduleConfig, ScheduleInfo, SocialAccount } from "../lib/api";
import { TargetChip, TargetPicker, targetNames } from "./TargetPicker";

// Owner rule (2026-10-04): Posting Queue and LongForm videos never post to profile "mk"
// (the server refuses it too).
const QUEUE_BLOCKED_PROFILES = ["mk"];
// Posted videos: shown together in "✓ Posted Archive", never in "All Items".
const DONE = ["posted", "archived"];
import { UndoButton } from "./UndoButton";
import { askConfirm, notify } from "../lib/dialogs";
import { QueueScheduleButton } from "./QueueSchedule";

const STATUS_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  review: { bg: "#1e293b", text: "#38bdf8", label: "👁 Review" },
  ready: { bg: "#064e3b", text: "#34d399", label: "● Ready to Post" },
  posting: { bg: "#78350f", text: "#fbbf24", label: "⏳ Posting… (awaiting platforms)" },
  posted: { bg: "#065f46", text: "#10b981", label: "✓ Posted" },
  retry: { bg: "#4c0519", text: "#fb7185", label: "⚠️ Retry" },
  error: { bg: "#450a0a", text: "#f87171", label: "✕ Error" },
  archived: { bg: "#1f2937", text: "#9ca3af", label: "📦 Archived" },
};

// Top-level pipelines only; Drive subfolders (@channels) show on each row.
// LongForm breakdowns live in their own SocialPilot section (mode="longform").
// Only Movie Clips is offered (owner, 2026-10-04: Abyss Declassified, The ICK Room and
// Default were unused). Rows from elsewhere still show under "All Pipelines".
const PIPELINES = ["Movie Clips"];
const LONGFORM = "LongForm";

type SortKey = "position" | "id" | "title" | "channel" | "status" | "scheduled";
const SORT_LABELS: Record<SortKey, string> = {
  position: "Posting order",
  id: "ID",
  title: "Title",
  channel: "Channel",
  status: "Status",
  scheduled: "Scheduled time",
};

function sortValue(it: QueueItem, key: SortKey): string | number {
  switch (key) {
    case "position": return it.position ?? it.id;
    case "id": return it.id;
    case "title": return (it.title || "").toLowerCase();
    case "channel": return (it.source || it.pipeline || "").toLowerCase();
    case "status": return it.status;
    // Unscheduled items sort after scheduled ones in ascending order.
    case "scheduled": return it.scheduled_at ? parseApiDate(it.scheduled_at) : Number.MAX_SAFE_INTEGER;
  }
}

function fmtET(iso: string): string {
  return new Date(parseApiDate(iso)).toLocaleString("en-US", {
    timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  }) + " ET";
}

export function QueuePanel({ onChange, mode = "clips" }: { onChange: () => void; mode?: "clips" | "longform" }) {
  const longform = mode === "longform";
  const [allItems, setAllItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("review");
  const [pipelineFilter, setPipelineFilter] = useState<string>(longform ? LONGFORM : "all");
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("position");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [archiveDays, setArchiveDays] = useState<number | null>(null);
  const [privacy, setPrivacy] = useState("public");

  // Bulk AI rewrite job (runs on the server; we poll its progress)
  const [aiJob, setAiJob] = useState<BulkAiStatus | null>(null);

  useEffect(() => {
    api.bulkAiStatus().then((st) => { if (st.running) setAiJob(st); }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!aiJob?.running) return;
    const t = setInterval(async () => {
      try {
        const st = await api.bulkAiStatus();
        setAiJob(st);
        if (!st.running) {
          loadQueue();
          onChange();
        }
      } catch {
        /* keep polling */
      }
    }, 3000);
    return () => clearInterval(t);
  }, [aiJob?.running]);

  async function handleBulkAi() {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    if (!await askConfirm(
      `Rewrite the title, description and hashtags of ${ids.length} video(s) with AI?\n\n` +
      "This replaces their current text. It runs in the background; you can keep working."
    )) return;
    try {
      setAiJob(await api.bulkAi(ids));
      setSel(new Set());
    } catch (err: any) {
      notify(`AI rewrite failed to start: ${err.message}`);
    }
  }

  // Mass edit modal
  const [showBulkEdit, setShowBulkEdit] = useState(false);
  const [bulkTitle, setBulkTitle] = useState("");
  const [bulkDesc, setBulkDesc] = useState("");
  const [bulkTags, setBulkTags] = useState("");
  const [bulkPipeline, setBulkPipeline] = useState("");

  // Edit modal state
  const [editItem, setEditItem] = useState<QueueItem | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

  // Shuffle modal / menu
  const [showShuffleMenu, setShowShuffleMenu] = useState(false);
  const [shuffling, setShuffling] = useState(false);

  // Pacing / posting frequency menu
  const [schedInfo, setSchedInfo] = useState<ScheduleInfo | null>(null);

  // New item modal
  const [showAddModal, setShowAddModal] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newTags, setNewTags] = useState("");
  const [newSource, setNewSource] = useState("");
  const [newDriveLink, setNewDriveLink] = useState("");
  const [newPipeline, setNewPipeline] = useState("Movie Clips");

  // Accounts for assignment
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [showAccountAssignModal, setShowAccountAssignModal] = useState(false);
  const [selectedTargetAccountIds, setSelectedTargetAccountIds] = useState<string[]>([]);

  const [undoVersion, setUndoVersion] = useState(0);
  // Phones get stacked cards instead of the 6-column table.
  const [isNarrow, setIsNarrow] = useState(false);
  useEffect(() => {
    const check = () => setIsNarrow(window.innerWidth < 760);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  const [autopostPaused, setAutopostPaused] = useState<boolean | null>(null);

  async function toggleAutopost() {
    const next = !autopostPaused;
    if (!await askConfirm(next
      ? "Pause auto-posting?\n\nNothing new will be submitted until you resume. A video already uploading finishes."
      : "Resume auto-posting? Ready videos will post at their scheduled times.")) return;
    try {
      setAutopostPaused((await api.setAutopost(next)).paused);
      setUndoVersion((v) => v + 1);
    } catch (e: any) {
      notify(`Could not change auto-posting: ${e.message || e}`);
    }
  }

  async function stopAiRewrite() {
    try {
      const j = (await api.jobs()).find((x) => x.kind === "ai" && ["running", "stopping"].includes(x.status));
      if (j) await api.stopJob(j.id);
    } catch (e: any) {
      notify(`Could not stop: ${e.message || e}`);
    }
  }

  // Filter items in memory based on statusFilter so switching tabs is instant
  const items = React.useMemo(() => {
    // "All Items" = everything still in play; posted videos live in the Posted Archive (owner, 2026-10-04).
    if (statusFilter === "all") return allItems.filter((i) => !DONE.includes(i.status));
    if (statusFilter === "error") {
      return allItems.filter((i) => i.status === "error" || i.status === "retry");
    }
    if (statusFilter === "posted") return allItems.filter((i) => DONE.includes(i.status));
    return allItems.filter((i) => i.status === statusFilter);
  }, [allItems, statusFilter]);

  // Signature of the last list drawn: a background refresh that brings back the
  // same data changes nothing on screen (it used to redraw ~700 rows every 4 s).
  const lastSig = useRef("");

  async function loadQueue(isBackground = false) {
    if (!isBackground) {
      setLoading(true);
      setUndoVersion((v) => v + 1); // a user action just happened: refresh the Undo label
    }
    try {
      // Always load all items for the selected pipeline/channel so badge counts are 100% accurate across all tabs
      const data = await api.queue(pipelineFilter, "all");
      const rows = longform ? data : data.filter((i) => i.pipeline !== LONGFORM);
      const sig = JSON.stringify(rows);
      if (sig !== lastSig.current) {
        lastSig.current = sig;
        setAllItems(rows);
      }
    } catch {
      // silent
    } finally {
      if (!isBackground) setLoading(false);
    }
  }

  useEffect(() => {
    loadQueue();
    api.socialAccounts()
      .then((r) => {
        setAccounts(r.accounts || []);
        setProfiles(r.profiles || []);
      })
      .catch(() => {});

    // Background refresh every 20 s, only while the page is visible (it was every 4 s,
    // always: ~2.5 MB a minute on a phone). Your own actions refresh at once.
    const timer = setInterval(() => {
      if (!document.hidden) loadQueue(true);
    }, 20000);
    const onShow = () => { if (!document.hidden) loadQueue(true); };
    document.addEventListener("visibilitychange", onShow);

    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", onShow); };
  }, [pipelineFilter]);

  useEffect(() => {
    api.schedule().then((r) => {
      setSchedInfo(r);
      setArchiveDays(r.archive_delete_days);
      setPrivacy(r.publisher.privacy);
      setAutopostPaused(!!r.scheduler.paused);
    }).catch(() => {});
  }, []);

  // Draw 50 rows at a time (678 cards at once froze phones); "Show more" adds 50.
  const [shownLimit, setShownLimit] = useState(50);
  useEffect(() => setShownLimit(50), [statusFilter, pipelineFilter]);

  const sortedItems = useMemo(() => {
    const dir = sortDir === "asc" ? 1 : -1;
    return [...items].sort((a, b) => {
      const va = sortValue(a, sortKey);
      const vb = sortValue(b, sortKey);
      if (va < vb) return -dir;
      if (va > vb) return dir;
      return (a.id - b.id) * dir;
    });
  }, [items, sortKey, sortDir]);

  function toggleSel(id: number) {
    setSel((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll(checked: boolean) {
    if (checked) {
      setSel(new Set(items.map((i) => i.id)));
    } else {
      setSel(new Set());
    }
  }

  async function handlePostNext(item: QueueItem) {
    try {
      const r = await api.queuePostNext(item.id);
      notify(`✓ "${item.title}" is next${r.scheduled_at ? ` — posts ${fmtET(r.scheduled_at)}` : ""}.`);
      loadQueue();
    } catch (e: any) {
      notify(`Could not move it: ${e.message || e}`);
    }
  }

  async function handleApprove(id: number) {
    await api.approveQueueItem(id).catch(() => {});
    loadQueue();
    onChange();
  }

  async function handlePublishNow(item: QueueItem) {
    if (!item.accounts || item.accounts.length === 0) {
      notify("Pick where this video posts first: click ⚠ None picked in its row (or 🔗 Set Target Accounts).");
      return;
    }
    const names = targetNames(item.accounts, accounts);
    if (!await askConfirm(`Post "${item.title}" now to ${names} as ${privacy} via Upload-Post?`)) return;
    try {
      await api.publishQueueItem(item.id);
      notify("✓ Submitted to Upload-Post. It shows ⏳ Posting until each platform confirms (usually a few minutes); results and links appear in the row's notes.");
    } catch (err: any) {
      notify(`Publish failed: ${err.message}`);
    }
    loadQueue();
    onChange();
  }

  const [generatingVoiceId, setGeneratingVoiceId] = useState<number | null>(null);

  async function handleGenerateAi(id: number) {
    try {
      await api.generateQueueAi(id);
      loadQueue();
    } catch (err: any) {
      notify(`AI generation failed: ${err.message}`);
    }
  }

  async function handleGenerateVoiceover(id: number) {
    setGeneratingVoiceId(id);
    try {
      const res = await api.generateQueueVoiceover(id);
      notify(`✓ Generated ${res.duration}s voiceover hook (${res.voice}):\n"${res.script}"`);
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Voiceover generation failed: ${err.message || err}`);
    } finally {
      setGeneratingVoiceId(null);
    }
  }

  async function handleDelete(id: number) {
    if (!await askConfirm("Delete this queue item?")) return;
    await api.deleteQueueItem(id).catch(() => {});
    loadQueue();
    onChange();
  }

  async function handleBulkStatusChange(targetStatus: string) {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    try {
      await api.bulkQueueAction(ids, "change_status", { target_status: targetStatus });
      setSel(new Set());
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Bulk status change failed: ${err.message}`);
    }
  }

  // Always open with nothing ticked — the owner picks every destination.
  function openAssignAccounts() {
    setSelectedTargetAccountIds([]);
    setShowAccountAssignModal(true);
  }

  async function handleBulkAccountsAssign(accountIds: string[]) {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    try {
      await api.bulkQueueAction(ids, "set_accounts", { accounts: accountIds });
      setShowAccountAssignModal(false);
      setSel(new Set());
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Account assignment failed: ${err.message}`);
    }
  }

  async function handleBulkEdit() {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    const opts: { title?: string; description?: string; tags?: string; pipeline?: string } = {};
    if (bulkTitle.trim()) opts.title = bulkTitle.trim();
    if (bulkDesc.trim()) opts.description = bulkDesc.trim();
    if (bulkTags.trim()) opts.tags = bulkTags.trim();
    if (bulkPipeline) opts.pipeline = bulkPipeline;
    if (Object.keys(opts).length === 0) {
      notify("Fill in at least one field to change.");
      return;
    }
    if (!await askConfirm(`Apply ${Object.keys(opts).join(", ")} to ${ids.length} selected item(s)?`)) return;
    try {
      await api.bulkQueueAction(ids, "edit", opts);
      setShowBulkEdit(false);
      setBulkTitle("");
      setBulkDesc("");
      setBulkTags("");
      setBulkPipeline("");
      setSel(new Set());
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Mass edit failed: ${err.message}`);
    }
  }

  async function handleBulkDelete() {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    if (!await askConfirm(`Permanently delete ${ids.length} selected item(s) from the queue?`)) return;
    try {
      await api.bulkQueueAction(ids, "delete");
      setSel(new Set());
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Bulk delete failed: ${err.message}`);
    }
  }

  async function handleShuffle(mode: "round_robin" | "random" | "by_channel") {
    setShuffling(true);
    setShowShuffleMenu(false);
    try {
      const res = await api.shuffleQueue({
        mode,
        pipeline: pipelineFilter === "all" ? undefined : pipelineFilter,
        status: statusFilter === "all" ? undefined : statusFilter,
      });
      notify(`✓ ${res.message || `Re-ordered ${res.count} items ('${mode}'). Table now shows the new posting order.`}`);
      setSortKey("position");
      setSortDir("asc");
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Shuffle failed: ${err.message}`);
    } finally {
      setShuffling(false);
    }
  }

  async function handleRetryAllFailed() {
    const errorIds = allItems.filter((i) => i.status === "error" || i.status === "retry").map((i) => i.id);
    if (errorIds.length === 0) {
      notify("No failed items to retry.");
      return;
    }
    if (!await askConfirm(`Reset and retry ${errorIds.length} failed item(s)?`)) return;
    try {
      await api.bulkQueueAction(errorIds, "change_status", { target_status: "ready" });
      loadQueue();
      onChange();
      notify(`✓ Reset ${errorIds.length} failed item(s) to 'ready'. Automated scheduler will re-attempt publishing.`);
    } catch (err: any) {
      notify(`Retry failed: ${err.message}`);
    }
  }

  async function handleSaveEdit() {
    if (!editItem) return;
    setSavingEdit(true);
    try {
      await api.updateQueueItem(editItem.id, {
        title: editItem.title,
        description: editItem.description,
        tags: editItem.tags,
        source: editItem.source,
        drive_link: editItem.drive_link,
        pipeline: editItem.pipeline,
        status: editItem.status,
        accounts: editItem.accounts,
      });
      setEditItem(null);
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Save failed: ${err.message}`);
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleCreateNew() {
    if (!newTitle.trim()) {
      notify("Please enter a title");
      return;
    }
    try {
      await api.createQueueItem({
        title: newTitle.trim(),
        description: newDesc.trim(),
        tags: newTags.trim(),
        source: newSource.trim() || undefined,
        drive_link: newDriveLink.trim() || undefined,
        pipeline: newPipeline,
        status: "review",
      });
      setShowAddModal(false);
      setNewTitle("");
      setNewDesc("");
      setNewTags("");
      setNewSource("");
      setNewDriveLink("");
      loadQueue();
      onChange();
    } catch (err: any) {
      notify(`Create failed: ${err.message}`);
    }
  }

  const allCount = allItems.filter((i) => !DONE.includes(i.status)).length;
  const reviewCount = allItems.filter((i) => i.status === "review").length;
  const readyCount = allItems.filter((i) => i.status === "ready").length;
  const postedCount = allItems.filter((i) => DONE.includes(i.status)).length;
  const errorCount = allItems.filter((i) => i.status === "error" || i.status === "retry").length;

  return (
    <section
      style={{
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        marginBottom: 20,
        overflow: "hidden",
      }}
    >
      {/* Top Header */}
      <div
        style={{
          padding: isNarrow ? "10px 12px" : "14px 18px",
          background: "var(--panel2)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: isNarrow ? 8 : 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: isNarrow ? 6 : 10, flexWrap: "wrap", minWidth: 0 }}>
          <span style={{ fontSize: isNarrow ? 14 : 15, fontWeight: 700, ...(isNarrow ? { width: "100%" } : {}) }}>
            {longform
              ? isNarrow ? "🎬 LongForm Queue" : "🎬 LongForm Breakdowns Queue"
              : isNarrow ? "📋 Posting Queue" : "📋 Posting Queue & Content Calendar"}
          </span>
          <span
            style={{
              padding: "2px 8px",
              background: "var(--chip)",
              borderRadius: 12,
              fontSize: 11,
              color: "var(--muted)",
            }}
          >
            {items.length} shown {statusFilter !== "all" ? `(${statusFilter})` : ""} • {allCount} total
          </span>
          <button
            onClick={() => loadQueue(false)}
            title="Click to refresh queue data from database"
            style={{
              background: "rgba(255, 255, 255, 0.05)",
              border: "1px solid var(--border)",
              color: refreshing ? "var(--accent)" : "var(--muted)",
              borderRadius: 6,
              padding: "3px 8px",
              fontSize: 11,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <span style={{ display: "inline-block", transform: refreshing ? "rotate(180deg)" : "none", transition: "transform 0.4s" }}>
              ↻
            </span>
            <span style={{ fontSize: 10 }}>{refreshing ? "Updating…" : "Live"}</span>
          </button>
          <UndoButton
            scope="queue"
            version={undoVersion}
            compact
            onUndone={(label) => {
              loadQueue();
              onChange();
            }}
          />
          {autopostPaused !== null && (
            <button
              onClick={toggleAutopost}
              title={autopostPaused ? "Auto-posting is paused" : "Auto-posting is on"}
              style={{
                fontSize: 11,
                padding: "5px 10px",
                fontWeight: 700,
                background: autopostPaused ? "#78350f" : "transparent",
                color: autopostPaused ? "#fde68a" : "var(--text)",
              }}
            >
              {autopostPaused
                ? isNarrow ? "▶ Resume posting" : "▶ Resume auto-posting"
                : isNarrow ? "⏸ Pause posting" : "⏸ Pause auto-posting"}
            </button>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: isNarrow ? 6 : 8, flexWrap: "wrap", ...(isNarrow ? { width: "100%" } : {}) }}>
          {/* Hierarchical Pipeline / Channel Dropdown */}
          {!longform && <select
            value={pipelineFilter}
            onChange={(e) => setPipelineFilter(e.target.value)}
            style={{
              background: "var(--row)",
              color: "var(--text)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: "5px 10px",
              fontSize: 12,
              fontWeight: 500,
            }}
          >
            <option value="all">📁 All Pipelines</option>
            {PIPELINES.map((p) => (
              <option key={p} value={p}>📁 {p === "default" ? "Default" : p}</option>
            ))}
          </select>}

          {/* Sort: field + direction */}
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            title="Sort by"
            style={{
              background: "var(--row)",
              color: "var(--text)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: "5px 10px",
              fontSize: 12,
            }}
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
              <option key={k} value={k}>Sort: {SORT_LABELS[k]}</option>
            ))}
          </select>
          <button
            onClick={() => setSortDir(sortDir === "asc" ? "desc" : "asc")}
            title="Toggle ascending / descending"
            style={{ fontSize: 11, padding: "5px 10px", fontWeight: 600 }}
          >
            {sortDir === "asc" ? (isNarrow ? "↑" : "↑ Ascending") : isNarrow ? "↓" : "↓ Descending"}
          </button>

          {/* Mix & Shuffle Button */}
          <div style={{ position: "relative" }}>
            <button
              onClick={() => setShowShuffleMenu(!showShuffleMenu)}
              disabled={shuffling || items.length === 0}
              style={{
                background: "#4c1d95",
                borderColor: "#6d28d9",
                color: "#c4b5fd",
                fontWeight: 600,
                fontSize: 11,
                padding: "6px 12px",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <span>🔀</span> {shuffling ? "Mixing…" : isNarrow ? "Mix ▾" : "Mix & Shuffle ▾"}
            </button>

            {showShuffleMenu && (
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  ...(isNarrow ? { left: 0 } : { right: 0 }), // phones: open rightwards, stay on screen
                  marginTop: 6,
                  background: "#1e1b4b",
                  border: "1px solid #4338ca",
                  borderRadius: 8,
                  padding: 8,
                  boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5)",
                  zIndex: 50,
                  width: "min(270px, 85vw)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                <div style={{ padding: "4px 8px", fontSize: 10, color: "#a5b4fc", fontWeight: 700, textTransform: "uppercase" }}>
                  Shuffle &amp; Schedule Distribution
                </div>
                <button
                  onClick={() => handleShuffle("round_robin")}
                  style={{
                    background: "rgba(255, 255, 255, 0.05)",
                    border: "none",
                    color: "#e0e7ff",
                    textAlign: "left",
                    padding: "8px 10px",
                    borderRadius: 6,
                    fontSize: 11,
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  <span style={{ fontWeight: 600, color: "#38bdf8" }}>🔄 Round-Robin Mix (Recommended)</span>
                  <span style={{ fontSize: 10, color: "#94a3b8" }}>
                    Interleaves videos across all 14 channels so posts alternate creators cleanly.
                  </span>
                </button>
                <button
                  onClick={() => handleShuffle("random")}
                  style={{
                    background: "rgba(255, 255, 255, 0.05)",
                    border: "none",
                    color: "#e0e7ff",
                    textAlign: "left",
                    padding: "8px 10px",
                    borderRadius: 6,
                    fontSize: 11,
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  <span style={{ fontWeight: 600, color: "#a78bfa" }}>🎲 Full Random Shuffle</span>
                  <span style={{ fontSize: 10, color: "#94a3b8" }}>
                    Randomizes all selected items regardless of channel.
                  </span>
                </button>
                <button
                  onClick={() => handleShuffle("by_channel")}
                  style={{
                    background: "rgba(255, 255, 255, 0.05)",
                    border: "none",
                    color: "#e0e7ff",
                    textAlign: "left",
                    padding: "8px 10px",
                    borderRadius: 6,
                    fontSize: 11,
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  <span style={{ fontWeight: 600, color: "#34d399" }}>🎯 Shuffle by Channel</span>
                  <span style={{ fontSize: 10, color: "#94a3b8" }}>
                    Randomizes internal order inside each channel bucket.
                  </span>
                </button>
              </div>
            )}
          </div>

          {/* This queue's own schedule: posts/day + hours or exact times (owner, 2026-10-05) */}
          <QueueScheduleButton
            queue={longform ? "LongForm" : "Movie Clips"}
            schedInfo={schedInfo}
            isNarrow={isNarrow}
            onSaved={(u) => { setSchedInfo(u); setUndoVersion((v) => v + 1); loadQueue(true); onChange(); }}
          />

          <button
            onClick={() => setShowAddModal(true)}
            style={{ background: "#2563eb", borderColor: "#1d4ed8", color: "#fff", fontWeight: 600, fontSize: 11 }}
          >
            {isNarrow ? "+ Add" : "+ Add Video to Queue"}
          </button>
        </div>
      </div>

      {/* Bulk AI progress */}
      {aiJob && (
        <div
          style={{
            padding: "8px 18px",
            fontSize: 12,
            borderBottom: "1px solid var(--border)",
            background: aiJob.running ? "#2e1065" : aiJob.failed ? "#450a0a" : "#064e3b",
            color: "#f5f3ff",
            display: "flex",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <span>
            {aiJob.running ? "✨ AI rewriting…" : aiJob.aborted ? "✕ AI rewrite stopped" : "✓ AI rewrite finished"}{" "}
            <b>{aiJob.done + aiJob.failed}/{aiJob.total}</b>
            {aiJob.failed > 0 && ` · ${aiJob.failed} failed`}
          </span>
          <div style={{ flex: 1, minWidth: 120, height: 4, background: "rgba(255,255,255,0.15)", borderRadius: 2 }}>
            <div style={{
              height: "100%", borderRadius: 2, background: "#c084fc",
              width: `${aiJob.total ? Math.round(((aiJob.done + aiJob.failed) / aiJob.total) * 100) : 0}%`,
            }} />
          </div>
          {(aiJob.aborted || aiJob.errors.length > 0) && (
            <span style={{ fontSize: 11, color: "#fecaca", width: "100%" }}>
              {aiJob.aborted || aiJob.errors[0]}
            </span>
          )}
          {aiJob.running && (
            <StopButton style={{ fontSize: 11, padding: "3px 12px" }}
              what="Videos already rewritten keep their new text (use Undo to revert them)" onStop={stopAiRewrite} />
          )}
          {!aiJob.running && (
            <button style={{ fontSize: 10, padding: "1px 8px" }} onClick={() => setAiJob(null)}>dismiss</button>
          )}
        </div>
      )}

      {/* Mass Action Toolbar (Active when items selected) */}
      {sel.size > 0 && (
        <div
          style={{
            background: "#1e293b",
            borderBottom: "1px solid #3b82f6",
            padding: "8px 18px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 12,
            fontSize: 12,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontWeight: 700, color: "#38bdf8" }}>
              ✓ Selected {sel.size} of {items.length} item{items.length === 1 ? "" : "s"}
            </span>
            <button
              onClick={() => toggleAll(sel.size < items.length)}
              style={{ fontSize: 11, padding: "2px 8px", background: "rgba(255,255,255,0.1)", border: "none" }}
            >
              {sel.size === items.length ? "Deselect All" : `Select All (${items.length})`}
            </button>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ color: "var(--muted)", fontSize: 11 }}>Mass Status:</span>
            <button
              style={{ fontSize: 11, padding: "4px 10px", background: "#064e3b", color: "#34d399", borderColor: "#059669" }}
              onClick={() => handleBulkStatusChange("ready")}
            >
              ● Set Ready to Post
            </button>
            <button
              style={{ fontSize: 11, padding: "4px 10px", background: "#1e293b", color: "#38bdf8", borderColor: "#334155" }}
              onClick={() => handleBulkStatusChange("review")}
            >
              👁 Set Needs Review
            </button>
            <button
              style={{ fontSize: 11, padding: "4px 10px", background: "#065f46", color: "#10b981", borderColor: "#047857" }}
              onClick={() => handleBulkStatusChange("posted")}
            >
              ✓ Set Posted
            </button>
            <button
              style={{ fontSize: 11, padding: "4px 10px", background: "#1f2937", color: "#9ca3af", borderColor: "#374151" }}
              onClick={() => handleBulkStatusChange("archived")}
            >
              📦 Set Archived
            </button>

            <button
              style={{ fontSize: 11, padding: "4px 10px", background: "#312e81", color: "#e0e7ff", borderColor: "#4338ca" }}
              onClick={() => setShowBulkEdit(true)}
            >
              ✏️ Mass Edit ({sel.size})
            </button>

            <button
              style={{ fontSize: 11, padding: "4px 10px", background: "#4a1d96", color: "#f5d0fe", borderColor: "#a855f7" }}
              onClick={handleBulkAi}
              disabled={!!aiJob?.running}
              title={aiJob?.running ? "An AI rewrite is already running" : "Gemini rewrites title, description and hashtags"}
            >
              ✨ AI Rewrite ({sel.size})
            </button>

            <button
              style={{ fontSize: 11, padding: "4px 10px", background: "#1e1b4b", color: "#c4b5fd", borderColor: "#4338ca" }}
              onClick={openAssignAccounts}
            >
              🔗 Set Target Accounts
            </button>

            <button
              className="danger"
              style={{ fontSize: 11, padding: "4px 10px" }}
              onClick={handleBulkDelete}
            >
              🗑 Delete ({sel.size})
            </button>
          </div>
        </div>
      )}

      {/* Filter Tabs */}
      <div
        style={{
          display: "flex",
          gap: 6,
          padding: isNarrow ? "8px 10px" : "8px 18px",
          background: "var(--row)",
          borderBottom: "1px solid var(--border)",
          overflowX: "auto",
          flexWrap: "nowrap",
        }}
      >
        {[
          { key: "review", label: "👁 Needs Review", count: reviewCount, accent: "#38bdf8", bg: "rgba(56, 189, 248, 0.15)" },
          { key: "ready", label: "● Ready to Post", count: readyCount, accent: "#34d399", bg: "rgba(52, 211, 153, 0.15)" },
          { key: "posted", label: "✓ Posted Archive", count: postedCount, accent: "#10b981", bg: "rgba(16, 185, 129, 0.15)" },
          { key: "error", label: "⚠️ Errors / Retry", count: errorCount, accent: "#f87171", bg: "rgba(248, 113, 113, 0.15)" },
          { key: "all", label: "All Items", count: allCount, accent: "#e2e8f0", bg: "rgba(255, 255, 255, 0.12)" },
        ].map((tab) => {
          const isActive = statusFilter === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setStatusFilter(tab.key)}
              style={{
                background: isActive ? tab.bg : "transparent",
                border: isActive ? `1px solid ${tab.accent}` : "1px solid transparent",
                color: isActive ? tab.accent : "var(--muted)",
                padding: isNarrow ? "5px 9px" : "5px 12px",
                fontSize: 12,
                borderRadius: 6,
                fontWeight: isActive ? 700 : 500,
                display: "flex",
                alignItems: "center",
                gap: 7,
                whiteSpace: "nowrap",      // "Ready to Post" stayed on one line, not three
                flex: "0 0 auto",
                transition: "all 0.15s ease",
                cursor: "pointer",
              }}
            >
              <span>{tab.label}</span>
              <span
                style={{
                  background: isActive ? tab.accent : "rgba(255,255,255,0.08)",
                  color: isActive ? "#0f172a" : "var(--muted)",
                  padding: "1px 6px",
                  borderRadius: 10,
                  fontSize: 11,
                  fontWeight: 700,
                }}
              >
                {tab.count}
              </span>
            </button>
          );
        })}
      </div>

      {/* Table */}
      <div style={{ overflowX: "auto" }}>
        {loading ? (
          <div style={{ padding: 40, textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
            ⏳ Loading posting queue…
          </div>
        ) : items.length === 0 ? (
          <div style={{ padding: 40, textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
            {statusFilter === "error" ? (
              <div style={{ color: "#34d399" }}>
                <span>✓</span> No failed items or errors! All videos are healthy and ready.
              </div>
            ) : (
              "No queue items found for this selection."
            )}
          </div>
        ) : isNarrow ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 8 }}>
            <label style={{ fontSize: 12, display: "flex", gap: 8, alignItems: "center", padding: "0 4px" }}>
              <input type="checkbox" style={{ width: "auto" }}
                     checked={sel.size === items.length && items.length > 0}
                     onChange={(e) => toggleAll(e.target.checked)} />
              Select all {items.length}
            </label>
            {sortedItems.slice(0, shownLimit).map((item) => {
              const st = STATUS_COLORS[item.status.toLowerCase()] || { bg: "var(--chip)", text: "var(--text)", label: item.status };
              return (
                <div key={item.id} style={{ border: `1px solid ${sel.has(item.id) ? "var(--accent)" : "var(--border)"}`,
                                            borderRadius: 10, padding: 10, background: "var(--row)",
                                            display: "flex", flexDirection: "column", gap: 6 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                    <input type="checkbox" style={{ width: 20, height: 20, flex: "0 0 auto", marginTop: 2 }}
                           checked={sel.has(item.id)} onChange={() => toggleSel(item.id)} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <RowThumb item={item} onOpen={() => setEditItem(item)} wide />
                      <div style={{ fontWeight: 700, fontSize: 13, lineHeight: 1.3 }}>{item.title || "Untitled Video"}</div>
                      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>
                        #{item.id} · {item.source || item.pipeline}
                      </div>
                      {item.research?.matched && (
                        <div style={{ fontSize: 10, color: "#93c5fd" }}>
                          🎬 {item.research.title}{item.research.year ? ` (${item.research.year})` : ""}
                        </div>
                      )}
                    </div>
                    <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 4, background: st.bg, color: st.text,
                                   whiteSpace: "nowrap" }}>{st.label}</span>
                  </div>
                  {item.status === "ready" && item.scheduled_at && (
                    <div style={{ fontSize: 11, color: "#34d399" }}>{item.pinned_at ? "📌" : "⏰"} {fmtET(item.scheduled_at)}{item.pinned_at ? " (pinned)" : ""}</div>
                  )}
                  {item.notes && (item.status === "retry" || item.status === "error" || DONE.includes(item.status)) && (
                    <div style={{ fontSize: 10, color: DONE.includes(item.status) ? "var(--muted)" : "#f87171",
                                  wordBreak: "break-word" }}>{item.notes}</div>
                  )}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {(item.accounts || []).length ? (item.accounts || []).map((a) => <TargetChip key={a} id={a} accounts={accounts} />) : (
                      <span style={{ fontSize: 10, color: "var(--yellow)" }}>⚠ no target picked</span>
                    )}
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {item.status === "review" && (
                      <button style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => handleApprove(item.id)}>✓ Approve</button>
                    )}
                    {item.status === "ready" && !item.pinned_at && (
                      <button style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => handlePostNext(item)}
                              title="Put this video first in line — it takes the queue's next post time">⏫ Post next</button>
                    )}
                    <button style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => handlePublishNow(item)}>🚀 Post now</button>
                    <button style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => handleGenerateAi(item.id)}>✨ AI</button>
                    <button style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => setEditItem(item)}>✎ Edit</button>
                    {item.drive_link && (
                      <a href={item.drive_link} target="_blank" rel="noreferrer">
                        <button style={{ fontSize: 12, padding: "6px 10px" }}>▶ Watch</button>
                      </a>
                    )}
                    <button className="danger" style={{ fontSize: 12, padding: "6px 10px" }} onClick={() => handleDelete(item.id)}>🗑</button>
                  </div>
                </div>
              );
            })}
            {sortedItems.length > shownLimit && (
              <button style={{ margin: "8px auto", display: "block", fontSize: 12, padding: "6px 14px" }}
                      onClick={() => setShownLimit((n) => n + 50)}>
                Show 50 more ({sortedItems.length - shownLimit} not shown)
              </button>
            )}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, textAlign: "left" }}>
            <thead>
              <tr style={{ background: "var(--panel2)", borderBottom: "1px solid var(--border)", color: "var(--muted)" }}>
                <th style={{ width: 36, padding: "8px 10px" }}>
                  <input
                    type="checkbox"
                    checked={sel.size === items.length && items.length > 0}
                    onChange={(e) => toggleAll(e.target.checked)}
                    style={{ cursor: "pointer" }}
                  />
                </th>
                <th style={{ padding: "8px 10px" }}>ID &amp; Folder / Channel</th>
                <th style={{ padding: "8px 10px" }}>Title &amp; Description</th>
                <th style={{ padding: "8px 10px" }}>Posts To (Upload-Post)</th>
                <th style={{ padding: "8px 10px" }}>Status</th>
                <th style={{ padding: "8px 10px" }}>Drive Link</th>
                <th style={{ padding: "8px 10px", textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.slice(0, shownLimit).map((item) => {
                const st = STATUS_COLORS[item.status.toLowerCase()] || {
                  bg: "var(--chip)",
                  text: "var(--text)",
                  label: item.status,
                };
                return (
                  <tr
                    key={item.id}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      background: sel.has(item.id) ? "var(--row-alt)" : "transparent",
                      transition: "background 0.1s",
                    }}
                  >
                    {/* Checkbox */}
                    <td style={{ padding: "10px 6px" }}>
                      <input
                        type="checkbox"
                        checked={sel.has(item.id)}
                        onChange={() => toggleSel(item.id)}
                        style={{ cursor: "pointer" }}
                      />
                    </td>

                    {/* ID & Folder / Channel */}
                    <td style={{ padding: "10px", verticalAlign: "top", whiteSpace: "nowrap" }}>
                      <div style={{ fontWeight: 700, color: "var(--accent)" }}>#{item.id}</div>
                      <div
                        style={{
                          fontSize: 10,
                          padding: "2px 6px",
                          background: "#1e293b",
                          color: "#38bdf8",
                          border: "1px solid #334155",
                          borderRadius: 4,
                          display: "inline-block",
                          marginTop: 4,
                          fontWeight: 500,
                        }}
                      >
                        📁 {item.source || item.pipeline}
                      </div>
                      {item.compilation_id && (
                        <div style={{ fontSize: 10, color: "var(--blue)", marginTop: 2 }}>
                          compilation #{item.compilation_id}
                        </div>
                      )}
                    </td>

                    {/* Title & Description */}
                    <td style={{ padding: "10px", verticalAlign: "top", maxWidth: 360 }}>
                      <RowThumb item={item} onOpen={() => setEditItem(item)} />
                      <div style={{ fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>
                        {item.title || "Untitled Video"}
                      </div>
                      {item.research && (
                        item.research.matched ? (
                          <a href={item.research.source} target="_blank" rel="noreferrer"
                             title={`AI matched this clip on TMDB (${Math.round((item.research.confidence || 0) * 100)}% sure): ${item.research.reason || ""}`}
                             style={{ fontSize: 10, color: "#93c5fd", display: "inline-block", marginBottom: 4 }}>
                            🎬 {item.research.title}{item.research.year ? ` (${item.research.year})` : ""} · TMDB
                          </a>
                        ) : (
                          <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}
                               title={item.research.reason || ""}>
                            🎬 no confident TMDB match{item.research.guess ? ` (guessed “${item.research.guess}”)` : ""}
                          </div>
                        )
                      )}
                      {item.description && (
                        <div
                          style={{
                            fontSize: 11,
                            color: "var(--muted)",
                            overflow: "hidden",
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                            lineHeight: 1.4,
                          }}
                        >
                          {item.description}
                        </div>
                      )}
                      {item.tags && (
                        <div style={{ fontSize: 10, color: "#818cf8", marginTop: 4 }}>
                          {item.tags}
                        </div>
                      )}
                      {item.notes && (
                        <div
                          style={{
                            fontSize: 10,
                            color: item.status === "error" || item.status === "retry" ? "#f87171" : "var(--yellow)",
                            marginTop: 4,
                          }}
                        >
                          {item.status === "error" ? "❌ Error:" : "ℹ️"} {item.notes}
                        </div>
                      )}
                      {item.media_url && (
                        <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 6 }}>
                          <span style={{ fontSize: 10, color: "#34d399", fontWeight: 700 }}>🎙️ Hook:</span>
                          <audio
                            controls
                            src={mediaUrl(item.media_url)}
                            style={{ height: 26, width: "100%", maxWidth: 240 }}
                          />
                        </div>
                      )}
                    </td>

                    {/* Target Social Accounts Column */}
                    <td style={{ padding: "10px", verticalAlign: "top", minWidth: 160 }}>
                      {item.accounts && item.accounts.length > 0 ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                          {item.accounts.map((accId) => (
                            <TargetChip key={accId} id={accId} accounts={accounts} />
                          ))}
                          <button
                            onClick={() => {
                              setSel(new Set([item.id]));
                              openAssignAccounts();
                            }}
                            style={{ fontSize: 10, padding: 0, background: "transparent", border: "none",
                                     color: "var(--blue)", width: "fit-content" }}
                          >
                            ✎ change
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setSel(new Set([item.id]));
                            openAssignAccounts();
                          }}
                          style={{
                            fontSize: 10,
                            padding: "2px 6px",
                            background: "transparent",
                            border: "1px dashed var(--yellow)",
                            color: "var(--yellow)",
                          }}
                        >
                          ⚠ None picked — choose
                        </button>
                      )}
                    </td>

                    {/* Status Badge */}
                    <td style={{ padding: "10px", verticalAlign: "top", whiteSpace: "nowrap" }}>
                      <span
                        style={{
                          padding: "3px 8px",
                          borderRadius: 4,
                          fontSize: 11,
                          fontWeight: 600,
                          background: st.bg,
                          color: st.text,
                        }}
                      >
                        {st.label}
                      </span>
                      {item.status === "ready" && (!item.accounts || item.accounts.length === 0) && (
                        <div style={{ fontSize: 10, color: "var(--yellow)", marginTop: 4 }}>
                          ⚠ no accounts picked — won&apos;t be scheduled
                        </div>
                      )}
                      {item.status === "ready" && item.scheduled_at && (
                        <div style={{ fontSize: 10, color: "#34d399", marginTop: 4 }} title="Next posting slot">
                          {item.pinned_at ? "📌" : "⏰"} {fmtET(item.scheduled_at)}{item.pinned_at ? " (pinned)" : ""}
                        </div>
                      )}
                      {item.published_at && (
                        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                          posted {fmtET(item.published_at)}
                        </div>
                      )}
                      {item.published_at && archiveDays !== null && archiveDays > 0 &&
                        (item.status === "posted" || item.status === "archived") && (
                        <div style={{ fontSize: 10, color: "var(--yellow)", marginTop: 2 }}
                          title="Row is removed and the Drive file moved to trash">
                          🗑 deletes {fmtET(new Date(parseApiDate(item.published_at) + archiveDays * 86400000).toISOString())}
                        </div>
                      )}
                    </td>

                    {/* Drive Link */}
                    <td style={{ padding: "10px", verticalAlign: "top", whiteSpace: "nowrap" }}>
                      {item.drive_link ? (
                        <a
                          href={item.drive_link}
                          target="_blank"
                          rel="noreferrer"
                          style={{
                            fontSize: 11,
                            color: "#38bdf8",
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                            textDecoration: "none",
                            padding: "3px 6px",
                            background: "#0c4a6e",
                            borderRadius: 4,
                          }}
                        >
                          <span>📁</span> Drive Link ↗
                        </a>
                      ) : (
                        <span style={{ color: "var(--muted)", fontSize: 11 }}>—</span>
                      )}
                    </td>

                    {/* Actions */}
                    <td style={{ padding: "10px", verticalAlign: "top", textAlign: "right", whiteSpace: "nowrap" }}>
                      <div style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
                        {item.status === "review" && (
                          <button
                            className="primary"
                            style={{ fontSize: 10, padding: "3px 7px" }}
                            onClick={() => handleApprove(item.id)}
                            title="Approve for posting"
                          >
                            ✓ Approve
                          </button>
                        )}
                        {item.status === "ready" && !item.pinned_at && (
                          <button style={{ fontSize: 10, padding: "3px 7px" }} onClick={() => handlePostNext(item)}
                                  title="Put this video first in line — it takes the queue's next post time">⏫ Post next</button>
                        )}
                        <button
                          style={{ fontSize: 10, padding: "3px 7px", background: "#7c3aed", borderColor: "#6d28d9", color: "#fff" }}
                          onClick={() => handlePublishNow(item)}
                          title="Post to social media now"
                        >
                          🚀 Post Now
                        </button>
                        <button
                          style={{ fontSize: 10, padding: "3px 7px" }}
                          onClick={() => handleGenerateAi(item.id)}
                          title="Auto-generate AI caption & tags"
                        >
                          ✨ AI
                        </button>
                        <button
                          style={{
                            fontSize: 10,
                            padding: "3px 7px",
                            background: item.media_url ? "rgba(52, 211, 153, 0.15)" : undefined,
                            borderColor: item.media_url ? "#059669" : undefined,
                            color: item.media_url ? "#34d399" : undefined,
                          }}
                          onClick={() => handleGenerateVoiceover(item.id)}
                          disabled={generatingVoiceId === item.id}
                          title="Generate Gemini 3.8 Flash TTS Voiceover Hook"
                        >
                          {generatingVoiceId === item.id ? "⏳…" : "🎙️ Voice"}
                        </button>
                        <button
                          style={{ fontSize: 10, padding: "3px 7px" }}
                          onClick={() => setEditItem(item)}
                          title="Edit video metadata"
                        >
                          ✏️ Edit
                        </button>
                        <button
                          className="danger"
                          style={{ fontSize: 10, padding: "3px 6px" }}
                          onClick={() => handleDelete(item.id)}
                          title="Delete"
                        >
                          ✕
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {!isNarrow && sortedItems.length > 0 && (
          <>
            {sortedItems.length > shownLimit && (
              <button style={{ margin: "8px auto", display: "block", fontSize: 12, padding: "6px 14px" }}
                      onClick={() => setShownLimit((n) => n + 50)}>
                Show 50 more ({sortedItems.length - shownLimit} not shown)
              </button>
            )}
          </>
        )}
      </div>

      {/* Errors & Retry Console (Shows when errors exist or filter is on errors) */}
      {(statusFilter === "error" || errorCount > 0) && (
        <div
          style={{
            padding: "14px 18px",
            background: "#450a0a",
            borderTop: "1px solid #7f1d1d",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 12,
            fontSize: 12,
          }}
        >
          <div>
            <span style={{ fontWeight: 700, color: "#f87171", display: "flex", alignItems: "center", gap: 6 }}>
              <span>⚠️</span> {errorCount} failed item{errorCount === 1 ? "" : "s"} detected in posting queue
            </span>
            <span style={{ color: "#fca5a5", fontSize: 11 }}>
              Review the error notes above. You can mass retry them once the cause is fixed (retries go only to the accounts that failed).
            </span>
          </div>
          <button
            onClick={handleRetryAllFailed}
            style={{
              background: "#b91c1c",
              borderColor: "#ef4444",
              color: "#fff",
              fontWeight: 600,
              fontSize: 11,
              padding: "6px 14px",
            }}
          >
            ↻ Retry All {errorCount} Failed Item(s)
          </button>
        </div>
      )}

      {/* Account Assign Modal */}
      {showAccountAssignModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: 24,
              width: "100%",
              maxWidth: 480,
              display: "flex",
              flexDirection: "column",
              gap: 16,
            }}
          >
            <h3 style={{ margin: 0, fontSize: 16 }}>Where should {sel.size === 1 ? "this video" : `these ${sel.size} videos`} post?</h3>
            <p style={{ margin: 0, color: "var(--muted)", fontSize: 12 }}>
              Tick a <b>profile</b> to post to every channel connected to it in Upload-Post, or open it to pick
              specific channels. This replaces the current targets of the selected video(s).
            </p>

            <div style={{ maxHeight: 340, overflowY: "auto" }}>
              <TargetPicker
                accounts={accounts}
                profiles={profiles}
                value={selectedTargetAccountIds}
                onChange={setSelectedTargetAccountIds}
                blockedProfiles={QUEUE_BLOCKED_PROFILES}
              />
            </div>
            {selectedTargetAccountIds.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--accent)" }}>
                Will post to: {targetNames(selectedTargetAccountIds, accounts)}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button onClick={() => setShowAccountAssignModal(false)}>Cancel</button>
              <button
                className="primary"
                disabled={selectedTargetAccountIds.length === 0}
                onClick={() => handleBulkAccountsAssign(selectedTargetAccountIds)}
              >
                Apply to {sel.size} item{sel.size === 1 ? "" : "s"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit Item Modal */}
      {editItem && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: 24,
              width: "100%",
              maxWidth: 540,
              display: "flex",
              flexDirection: "column",
              gap: 14,
            }}
          >
            <h3 style={{ margin: 0, fontSize: 16 }}>Edit Queue Item #{editItem.id}</h3>

            <div>
              <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Title</label>
              <input
                type="text"
                value={editItem.title}
                onChange={(e) => setEditItem({ ...editItem, title: e.target.value })}
                style={{ width: "100%" }}
              />
            </div>

            <div>
              <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Description</label>
              <textarea
                rows={3}
                value={editItem.description}
                onChange={(e) => setEditItem({ ...editItem, description: e.target.value })}
                style={{ width: "100%", fontSize: 12 }}
              />
            </div>

            <div>
              <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Hashtags</label>
              <input
                type="text"
                value={editItem.tags}
                onChange={(e) => setEditItem({ ...editItem, tags: e.target.value })}
                style={{ width: "100%" }}
              />
            </div>

            <QueueThumbEditor itemId={editItem.id} onChanged={() => loadQueue(true)} />
            <PinTimeEditor item={editItem} onChanged={() => loadQueue(true)} />

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Status</label>
                <select
                  value={editItem.status}
                  onChange={(e) => setEditItem({ ...editItem, status: e.target.value })}
                  style={{ width: "100%", padding: 6, fontSize: 12 }}
                >
                  <option value="review">👁 Needs Review</option>
                  <option value="ready">● Ready to Post</option>
                  <option value="posted">✓ Posted</option>
                  <option value="archived">📦 Archived</option>
                  <option value="retry">⚠️ Retry</option>
                </select>
              </div>

              <div>
                <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Folder / Pipeline</label>
                <input
                  type="text"
                  value={editItem.pipeline}
                  onChange={(e) => setEditItem({ ...editItem, pipeline: e.target.value })}
                  style={{ width: "100%" }}
                />
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
              <button onClick={() => setEditItem(null)}>Cancel</button>
              <button className="primary" onClick={handleSaveEdit} disabled={savingEdit}>
                {savingEdit ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Mass Edit Modal */}
      {showBulkEdit && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: 24,
              width: "100%",
              maxWidth: 500,
              display: "flex",
              flexDirection: "column",
              gap: 14,
            }}
          >
            <h3 style={{ margin: 0, fontSize: 16 }}>✏️ Mass Edit {sel.size} Item{sel.size === 1 ? "" : "s"}</h3>
            <div style={{ fontSize: 11, color: "var(--muted)" }}>
              Only filled-in fields are changed. Empty fields keep each item&apos;s current value.
            </div>

            <div>
              <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Title</label>
              <input type="text" value={bulkTitle} onChange={(e) => setBulkTitle(e.target.value)}
                placeholder="(unchanged)" style={{ width: "100%" }} />
            </div>
            <div>
              <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Description</label>
              <textarea value={bulkDesc} onChange={(e) => setBulkDesc(e.target.value)}
                placeholder="(unchanged)" rows={3} style={{ width: "100%" }} />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Hashtags</label>
                <input type="text" value={bulkTags} onChange={(e) => setBulkTags(e.target.value)}
                  placeholder="(unchanged)" style={{ width: "100%" }} />
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Pipeline</label>
                <select value={bulkPipeline} onChange={(e) => setBulkPipeline(e.target.value)}
                  style={{ width: "100%", padding: 6, fontSize: 12 }}>
                  <option value="">(unchanged)</option>
                  {(longform ? [LONGFORM] : PIPELINES).map((p) => (
                    <option key={p} value={p}>{p === "default" ? "Default" : p}</option>
                  ))}
                </select>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
              <button onClick={() => setShowBulkEdit(false)}>Cancel</button>
              <button className="primary" onClick={handleBulkEdit}>Apply to {sel.size} Items</button>
            </div>
          </div>
        </div>
      )}

      {/* Add Item Modal */}
      {showAddModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
          }}
        >
          <div
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: 24,
              width: "100%",
              maxWidth: 500,
              display: "flex",
              flexDirection: "column",
              gap: 14,
            }}
          >
            <h3 style={{ margin: 0, fontSize: 16 }}>+ Add Video to Posting Queue</h3>

            <div>
              <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Title *</label>
              <input
                type="text"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Video title"
                style={{ width: "100%" }}
              />
            </div>

            <div>
              <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Google Drive Link</label>
              <input
                type="text"
                value={newDriveLink}
                onChange={(e) => setNewDriveLink(e.target.value)}
                placeholder="https://drive.google.com/file/d/..."
                style={{ width: "100%" }}
              />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Pipeline Channel</label>
                <select
                  value={newPipeline}
                  onChange={(e) => setNewPipeline(e.target.value)}
                  style={{ width: "100%", padding: 6, fontSize: 12 }}
                >
                  <option value="Movie Clips">Movie Clips</option>
                </select>
              </div>

              <div>
                <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Hashtags</label>
                <input
                  type="text"
                  value={newTags}
                  onChange={(e) => setNewTags(e.target.value)}
                  placeholder="#shorts #movies"
                  style={{ width: "100%" }}
                />
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
              <button onClick={() => setShowAddModal(false)}>Cancel</button>
              <button className="primary" onClick={handleCreateNew}>
                Add to Queue
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}


/** Edit dialog: the row's thumbnail — breakdown options (LongForm) or an uploaded image. */
function QueueThumbEditor({ itemId, onChanged }: { itemId: number; onChanged?: () => void }) {
  const [o, setO] = React.useState<QueueThumbOptions | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  React.useEffect(() => { api.queueThumbOptions(itemId).then(setO).catch(() => {}); }, [itemId]);

  async function act(fn: () => Promise<QueueThumbOptions>, done: string) {
    setBusy(true);
    setMsg("");
    try { setO(await fn()); setMsg(done); onChanged?.(); }
    catch (e: any) { setMsg(`✕ ${e.message || e}`); }
    finally { setBusy(false); }
  }

  return (
    <div>
      <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>Thumbnail</label>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start", flexWrap: "wrap" }}>
        {o?.has_thumbnail
          ? <img src={api.queueThumbUrl(itemId, o.updated_at)} alt="current thumbnail"
                 style={{ width: 160, aspectRatio: "16/9", objectFit: "cover", borderRadius: 6, border: "2px solid var(--accent)" }} />
          : <div style={{ width: 160, aspectRatio: "16/9", borderRadius: 6, background: "var(--chip)", display: "flex",
                          alignItems: "center", justifyContent: "center", fontSize: 11, color: "var(--muted)" }}>No thumbnail</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
          <label style={{ fontSize: 11, cursor: busy ? "default" : "pointer" }}>
            <span className="ui-btn" style={{ display: "inline-block", padding: "4px 10px", border: "1px solid var(--border)", borderRadius: 6 }}>
              {busy ? "⏳ Working…" : "⬆ Upload image"}
            </span>
            <input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} style={{ display: "none" }}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) act(() => api.queueThumbUpload(itemId, f), "✓ Thumbnail uploaded"); e.target.value = ""; }} />
          </label>
          {o?.project_id && (
            <button style={{ fontSize: 11, padding: "4px 10px" }} disabled={busy}
                    onClick={() => act(() => api.queueThumbStudio(itemId, "refresh"), "✓ New options from the breakdown")}>
              🔄 New options
            </button>
          )}
          <span style={{ fontSize: 10, color: "var(--muted)" }}>JPG/PNG/WebP up to 5 MB · YouTube shows custom thumbnails on verified channels</span>
        </div>
      </div>
      {o?.project_id && o.options.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginTop: 8 }}>
          {o.options.map((t) => (
            <button key={t.id} disabled={busy} title={t.desc}
                    onClick={() => act(() => api.queueThumbStudio(itemId, t.id as "poster" | "shot1" | "shot2"), `✓ Using ${t.label}`)}
                    style={{ padding: 3, border: o.selected === t.id ? "2px solid var(--accent)" : "1px solid var(--border)",
                             borderRadius: 6, background: "var(--row)" }}>
              <img src={api.studioFileUrl(o.project_id!, t.file, String(o.updated_at || ""))} alt={t.label}
                   style={{ width: "100%", aspectRatio: "16/9", objectFit: "cover", borderRadius: 4 }} />
              <div style={{ fontSize: 10, marginTop: 2 }}>{o.selected === t.id ? "✓ " : ""}{t.label}</div>
            </button>
          ))}
        </div>
      )}
      {msg && <div style={{ fontSize: 11, marginTop: 4, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>{msg}</div>}
    </div>
  );
}


/** The thumbnail that will post with this row (tap → Edit, where it can be changed). */
function RowThumb({ item, onOpen, wide = false }: { item: QueueItem; onOpen: () => void; wide?: boolean }) {
  if (!item.thumb_path) return null;
  return (
    <button onClick={onOpen} title="Thumbnail that posts with this video — click to change it"
            style={{ padding: 0, border: "1px solid var(--border)", borderRadius: 6, background: "var(--row)",
                     display: "block", marginBottom: 6, width: wide ? "100%" : 168, maxWidth: "100%", overflow: "hidden" }}>
      <img src={api.queueThumbUrl(item.id, item.thumb_version)} alt="thumbnail" loading="lazy"
           style={{ width: "100%", aspectRatio: "16/9", objectFit: "cover", display: "block" }}
           onError={(e) => { (e.currentTarget.parentElement as HTMLElement).style.display = "none"; }} />
    </button>
  );
}


/** Edit dialog: pin this video to an exact post time (never re-planned) or unpin it. */
function PinTimeEditor({ item, onChanged }: { item: QueueItem; onChanged?: () => void }) {
  const toLocal = (iso: string) => {
    const d = new Date(iso);
    const p = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  const [pinned, setPinned] = React.useState<string | null>(item.pinned_at || null);
  const [value, setValue] = React.useState(toLocal(item.pinned_at || item.scheduled_at || new Date(Date.now() + 3600e3).toISOString()));
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");

  async function apply(unpin: boolean) {
    setBusy(true);
    setMsg("");
    try {
      const iso = unpin ? "" : new Date(value).toISOString();      // this device's local time → UTC
      const r = await api.queuePin(item.id, iso);
      setPinned(r.pinned_at || null);
      setMsg(unpin ? "✓ Unpinned — it goes back to the queue's normal times" : "✓ Pinned — it posts at exactly this time");
      onChanged?.();
    } catch (e: any) {
      setMsg(`✕ ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <label style={{ fontSize: 11, fontWeight: 600, display: "block", marginBottom: 4 }}>
        📌 Post at a set time {pinned ? <span style={{ color: "var(--accent)" }}>(pinned)</span> : <span style={{ color: "var(--muted)", fontWeight: 400 }}>(optional)</span>}
      </label>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <input type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} style={{ flex: "1 1 180px" }} />
        <button style={{ fontSize: 11 }} disabled={busy || !value} onClick={() => apply(false)}>{pinned ? "Update pin" : "📌 Pin this time"}</button>
        {pinned && <button style={{ fontSize: 11 }} disabled={busy} onClick={() => apply(true)}>Unpin</button>}
      </div>
      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 3 }}>
        Time on this device. A pinned video keeps its time and doesn't take one of the queue's regular daily posts. It must be Ready to Post with a destination.
      </div>
      {msg && <div style={{ fontSize: 11, marginTop: 3, color: msg.startsWith("✓") ? "var(--accent)" : "var(--red)" }}>{msg}</div>}
    </div>
  );
}
