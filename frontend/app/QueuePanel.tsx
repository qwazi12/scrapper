"use client";

import React, { useEffect, useState } from "react";
import { api, QueueItem, SocialAccount } from "../lib/api";

const STATUS_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  review: { bg: "#1e293b", text: "#38bdf8", label: "👁 Review" },
  ready: { bg: "#064e3b", text: "#34d399", label: "● Ready to Post" },
  posting: { bg: "#78350f", text: "#fbbf24", label: "⏳ Posting…" },
  posted: { bg: "#065f46", text: "#10b981", label: "✓ Posted" },
  retry: { bg: "#4c0519", text: "#fb7185", label: "⚠️ Retry" },
  error: { bg: "#450a0a", text: "#f87171", label: "✕ Error" },
  archived: { bg: "#1f2937", text: "#9ca3af", label: "📦 Archived" },
};

const MOVIE_CLIPS_SUBCHANNELS = [
  "@AlphaReels-1",
  "@CoruscateCuts",
  "@EditAetheris",
  "@FrameLegion",
  "@PixelDrift-f3c",
  "@QianaLucy",
  "@SceneVale",
  "@SolarrEditss",
  "@TheUsJournal17",
  "@VynixAE",
  "@clipscav",
  "@comet-cinema",
  "@hanganhoang3071",
  "@roebutt",
];

export function QueuePanel({ onChange }: { onChange: () => void }) {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [pipelineFilter, setPipelineFilter] = useState<string>("all");
  const [sel, setSel] = useState<Set<number>>(new Set());

  // Edit modal state
  const [editItem, setEditItem] = useState<QueueItem | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

  // Shuffle modal / menu
  const [showShuffleMenu, setShowShuffleMenu] = useState(false);
  const [shuffling, setShuffling] = useState(false);

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
  const [showAccountAssignModal, setShowAccountAssignModal] = useState(false);
  const [selectedTargetAccountIds, setSelectedTargetAccountIds] = useState<string[]>([]);

  useEffect(() => {
    loadQueue();
    api.socialAccounts()
      .then((r) => setAccounts(r.accounts || []))
      .catch(() => {});
  }, [statusFilter, pipelineFilter]);

  async function loadQueue() {
    setLoading(true);
    try {
      const data = await api.queue(pipelineFilter, statusFilter);
      setItems(data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }

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

  async function handleApprove(id: number) {
    await api.approveQueueItem(id).catch(() => {});
    loadQueue();
    onChange();
  }

  async function handlePublishNow(id: number) {
    if (!confirm("Publish this item to connected social accounts now?")) return;
    try {
      await api.publishQueueItem(id);
      alert("✓ Successfully published!");
    } catch (err: any) {
      alert(`Publish failed: ${err.message}`);
    }
    loadQueue();
    onChange();
  }

  async function handleGenerateAi(id: number) {
    try {
      await api.generateQueueAi(id);
      loadQueue();
    } catch (err: any) {
      alert(`AI generation failed: ${err.message}`);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this queue item?")) return;
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
      alert(`Bulk status change failed: ${err.message}`);
    }
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
      alert(`Account assignment failed: ${err.message}`);
    }
  }

  async function handleBulkDelete() {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    if (!confirm(`Permanently delete ${ids.length} selected item(s) from the queue?`)) return;
    try {
      await api.bulkQueueAction(ids, "delete");
      setSel(new Set());
      loadQueue();
      onChange();
    } catch (err: any) {
      alert(`Bulk delete failed: ${err.message}`);
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
      alert(`✓ ${res.message || `Successfully mixed ${res.count} items using '${mode}' mode!`}`);
      loadQueue();
      onChange();
    } catch (err: any) {
      alert(`Shuffle failed: ${err.message}`);
    } finally {
      setShuffling(false);
    }
  }

  async function handleRetryAllFailed() {
    const errorIds = items.filter((i) => i.status === "error" || i.status === "retry").map((i) => i.id);
    if (errorIds.length === 0) {
      alert("No failed items to retry.");
      return;
    }
    if (!confirm(`Reset and retry ${errorIds.length} failed item(s)?`)) return;
    try {
      await api.bulkQueueAction(errorIds, "change_status", { target_status: "ready" });
      loadQueue();
      onChange();
      alert(`✓ Reset ${errorIds.length} failed item(s) to 'ready'. Automated scheduler will re-attempt publishing.`);
    } catch (err: any) {
      alert(`Retry failed: ${err.message}`);
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
      alert(`Save failed: ${err.message}`);
    } finally {
      setSavingEdit(false);
    }
  }

  async function handleCreateNew() {
    if (!newTitle.trim()) {
      alert("Please enter a title");
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
      alert(`Create failed: ${err.message}`);
    }
  }

  const reviewCount = items.filter((i) => i.status === "review").length;
  const readyCount = items.filter((i) => i.status === "ready").length;
  const postedCount = items.filter((i) => i.status === "posted").length;
  const errorCount = items.filter((i) => i.status === "error" || i.status === "retry").length;

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
          padding: "14px 18px",
          background: "var(--panel2)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 15, fontWeight: 700 }}>📋 Posting Queue &amp; Content Calendar</span>
          <span
            style={{
              padding: "2px 8px",
              background: "var(--chip)",
              borderRadius: 12,
              fontSize: 11,
              color: "var(--muted)",
            }}
          >
            {items.length} item{items.length === 1 ? "" : "s"}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          {/* Hierarchical Pipeline / Channel Dropdown */}
          <select
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
            <option value="all">📁 All Channels &amp; Folders (700 Total)</option>
            <option value="Movie Clips" style={{ fontWeight: 700, color: "#38bdf8" }}>
              🎬 Movie Clips (Parent - All 14 Channels)
            </option>
            {MOVIE_CLIPS_SUBCHANNELS.map((ch) => (
              <option key={ch} value={ch}>
                &nbsp;&nbsp;&nbsp;&nbsp;↳ {ch}
              </option>
            ))}
            <option value="Abyss Declassified">📁 Abyss Declassified</option>
            <option value="The ICK Room">📁 The ICK Room</option>
            <option value="default">📁 Default</option>
          </select>

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
              <span>🔀</span> {shuffling ? "Mixing…" : "Mix & Shuffle ▾"}
            </button>

            {showShuffleMenu && (
              <div
                style={{
                  position: "absolute",
                  top: "100%",
                  right: 0,
                  marginTop: 6,
                  background: "#1e1b4b",
                  border: "1px solid #4338ca",
                  borderRadius: 8,
                  padding: 8,
                  boxShadow: "0 10px 25px -5px rgba(0, 0, 0, 0.5)",
                  zIndex: 50,
                  width: 270,
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

          <button
            onClick={() => setShowAddModal(true)}
            style={{ background: "#2563eb", borderColor: "#1d4ed8", color: "#fff", fontWeight: 600, fontSize: 11 }}
          >
            + Add Video to Queue
          </button>
        </div>
      </div>

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
              style={{ fontSize: 11, padding: "4px 10px", background: "#1e1b4b", color: "#c4b5fd", borderColor: "#4338ca" }}
              onClick={() => setShowAccountAssignModal(true)}
            >
              🔗 Assign Accounts ({accounts.length})
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
          padding: "8px 18px",
          background: "var(--row)",
          borderBottom: "1px solid var(--border)",
          overflowX: "auto",
        }}
      >
        {[
          { key: "all", label: "All Items", count: items.length },
          { key: "review", label: "👁 Needs Review", count: reviewCount },
          { key: "ready", label: "● Ready to Post", count: readyCount },
          { key: "posted", label: "✓ Posted Archive", count: postedCount },
          { key: "error", label: "⚠️ Errors / Retry", count: errorCount },
        ].map((tab) => {
          const isActive = statusFilter === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => setStatusFilter(tab.key)}
              style={{
                background: isActive ? "var(--chip)" : "transparent",
                border: isActive ? "1px solid var(--border)" : "1px solid transparent",
                color: isActive ? "var(--text)" : "var(--muted)",
                padding: "4px 10px",
                fontSize: 11,
                borderRadius: 6,
                fontWeight: isActive ? 600 : 400,
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <span>{tab.label}</span>
              <span
                style={{
                  background: isActive ? "var(--panel)" : "rgba(255,255,255,0.06)",
                  padding: "1px 5px",
                  borderRadius: 10,
                  fontSize: 10,
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
                <th style={{ padding: "8px 10px" }}>Target Social Accounts</th>
                <th style={{ padding: "8px 10px" }}>Status</th>
                <th style={{ padding: "8px 10px" }}>Drive Link</th>
                <th style={{ padding: "8px 10px", textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
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
                      <div style={{ fontWeight: 600, color: "var(--text)", marginBottom: 4 }}>
                        {item.title || "Untitled Video"}
                      </div>
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
                    </td>

                    {/* Target Social Accounts Column */}
                    <td style={{ padding: "10px", verticalAlign: "top", minWidth: 160 }}>
                      {item.accounts && item.accounts.length > 0 ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                          {item.accounts.map((accId) => {
                            const acc = accounts.find((a) => a.id === accId);
                            const name = acc?.nickname || acc?.username || accId;
                            const network = acc?.network?.toLowerCase() || "outstand";
                            const icon =
                              network.includes("youtube") ? "▶️" :
                              network.includes("tiktok") ? "🎵" :
                              network.includes("insta") ? "📸" :
                              network.includes("face") ? "📘" : "🌐";
                            return (
                              <span
                                key={accId}
                                style={{
                                  fontSize: 10,
                                  background: "#1e1b4b",
                                  color: "#c7d2fe",
                                  padding: "2px 6px",
                                  borderRadius: 4,
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: 4,
                                  width: "fit-content",
                                }}
                              >
                                <span>{icon}</span> {name}
                              </span>
                            );
                          })}
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setSel(new Set([item.id]));
                            setShowAccountAssignModal(true);
                          }}
                          style={{
                            fontSize: 10,
                            padding: "2px 6px",
                            background: "transparent",
                            border: "1px dashed var(--border)",
                            color: "var(--muted)",
                          }}
                        >
                          + Assign Accounts
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
                      {item.published_at && (
                        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                          {new Date(item.published_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
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
                        <button
                          style={{ fontSize: 10, padding: "3px 7px", background: "#7c3aed", borderColor: "#6d28d9", color: "#fff" }}
                          onClick={() => handlePublishNow(item.id)}
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
              Review the error notes above. You can mass retry them once network or Outstand credentials are verified.
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
            <h3 style={{ margin: 0, fontSize: 16 }}>Assign Social Media Accounts</h3>
            <p style={{ margin: 0, color: "var(--muted)", fontSize: 12 }}>
              Choose which connected accounts will post these {sel.size} selected video(s):
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 200, overflowY: "auto" }}>
              {accounts.map((acc) => {
                const checked = selectedTargetAccountIds.includes(acc.id);
                return (
                  <label
                    key={acc.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      padding: "8px 12px",
                      background: "var(--bg)",
                      borderRadius: 6,
                      border: "1px solid var(--border)",
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        if (e.target.checked) {
                          setSelectedTargetAccountIds([...selectedTargetAccountIds, acc.id]);
                        } else {
                          setSelectedTargetAccountIds(selectedTargetAccountIds.filter((id) => id !== acc.id));
                        }
                      }}
                    />
                    <span style={{ fontWeight: 600 }}>{acc.nickname || acc.username}</span>
                    <span style={{ color: "var(--muted)", fontSize: 11 }}>({acc.network})</span>
                  </label>
                );
              })}
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button onClick={() => setShowAccountAssignModal(false)}>Cancel</button>
              <button
                className="primary"
                onClick={() => handleBulkAccountsAssign(selectedTargetAccountIds)}
              >
                Apply to {sel.size} Items
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
                  {MOVIE_CLIPS_SUBCHANNELS.map((ch) => (
                    <option key={ch} value={ch}>{ch}</option>
                  ))}
                  <option value="Abyss Declassified">Abyss Declassified</option>
                  <option value="The ICK Room">The ICK Room</option>
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
