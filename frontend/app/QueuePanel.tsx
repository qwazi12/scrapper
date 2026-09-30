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

export function QueuePanel({
  onChange,
}: {
  onChange: () => void;
}) {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [pipelineFilter, setPipelineFilter] = useState<string>("all");
  const [sel, setSel] = useState<Set<number>>(new Set());

  // Edit modal state
  const [editItem, setEditItem] = useState<QueueItem | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);

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

  useEffect(() => {
    loadQueue();
    api.socialAccounts().then((r) => setAccounts(r.accounts || [])).catch(() => {});
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

  async function handleBulkAction(action: string) {
    const ids = Array.from(sel);
    if (ids.length === 0) return;
    if (!confirm(`Run '${action}' on ${ids.length} selected item(s)?`)) return;
    try {
      await api.bulkQueueAction(ids, action);
      setSel(new Set());
      loadQueue();
      onChange();
    } catch (err: any) {
      alert(`Bulk action failed: ${err.message}`);
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
          <span style={{ fontSize: 15, fontWeight: 700 }}>📋 Posting Queue & Schedule</span>
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
          {/* Pipeline Dropdown */}
          <select
            value={pipelineFilter}
            onChange={(e) => setPipelineFilter(e.target.value)}
            style={{
              background: "var(--row)",
              color: "var(--text)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: "4px 8px",
              fontSize: 12,
            }}
          >
            <option value="all">All Channels / Sheets</option>
            <option value="Movie Clips">Movie Clips</option>
            <option value="Abyss Declassified">Abyss Declassified</option>
            <option value="The ICK Room">The ICK Room</option>
            <option value="default">Default</option>
          </select>

          {/* Bulk Actions */}
          {sel.size > 0 && (
            <div style={{ display: "flex", gap: 6 }}>
              <button
                className="primary"
                style={{ fontSize: 11, padding: "4px 8px" }}
                onClick={() => handleBulkAction("approve")}
              >
                ✓ Approve ({sel.size})
              </button>
              <button
                className="danger"
                style={{ fontSize: 11, padding: "4px 8px" }}
                onClick={() => handleBulkAction("delete")}
              >
                🗑 Delete ({sel.size})
              </button>
            </div>
          )}

          <button
            onClick={() => setShowAddModal(true)}
            style={{ background: "#2563eb", borderColor: "#1d4ed8", color: "#fff", fontWeight: 600, fontSize: 11 }}
          >
            + Add Video to Queue
          </button>
        </div>
      </div>

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
              }}
            >
              {tab.label} {tab.count > 0 ? `(${tab.count})` : ""}
            </button>
          );
        })}
      </div>

      {/* Table Content */}
      <div style={{ padding: 12, overflowX: "auto" }}>
        {items.length === 0 ? (
          <div style={{ padding: "30px 10px", textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
            {loading ? "Loading posting queue…" : "No queue items found. Add items from Compilations or Clips above."}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--muted)", borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                <th style={{ padding: "8px 6px", width: 30 }}>
                  <input
                    type="checkbox"
                    checked={sel.size === items.length && items.length > 0}
                    onChange={(e) => toggleAll(e.target.checked)}
                    style={{ cursor: "pointer" }}
                  />
                </th>
                <th style={{ padding: "8px 10px" }}>ID & Channel</th>
                <th style={{ padding: "8px 10px" }}>Title & Description</th>
                <th style={{ padding: "8px 10px" }}>Source / Origin</th>
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

                    {/* ID & Channel */}
                    <td style={{ padding: "10px", verticalAlign: "top", whiteSpace: "nowrap" }}>
                      <div style={{ fontWeight: 700, color: "var(--accent)" }}>#{item.id}</div>
                      <div
                        style={{
                          fontSize: 10,
                          padding: "2px 6px",
                          background: "var(--chip)",
                          borderRadius: 4,
                          display: "inline-block",
                          marginTop: 4,
                          color: "var(--muted)",
                        }}
                      >
                        {item.pipeline}
                      </div>
                      {item.compilation_id && (
                        <div style={{ fontSize: 10, color: "var(--blue)", marginTop: 2 }}>
                          compilation #{item.compilation_id}
                        </div>
                      )}
                    </td>

                    {/* Title & Description */}
                    <td style={{ padding: "10px", verticalAlign: "top", maxWidth: 380 }}>
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
                        <div style={{ fontSize: 10, color: "var(--yellow)", marginTop: 4 }}>
                          ℹ️ {item.notes}
                        </div>
                      )}
                    </td>

                    {/* Source */}
                    <td style={{ padding: "10px", verticalAlign: "top", color: "var(--muted)" }}>
                      <div>{item.source || "—"}</div>
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

                    {/* Google Drive Link */}
                    <td style={{ padding: "10px", verticalAlign: "top" }}>
                      {item.drive_link ? (
                        <a
                          href={item.drive_link}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "var(--blue)", fontSize: 11 }}
                        >
                          📁 Drive Link ↗
                        </a>
                      ) : (
                        <span style={{ color: "var(--muted)" }}>—</span>
                      )}
                    </td>

                    {/* Actions */}
                    <td style={{ padding: "10px", verticalAlign: "top", textAlign: "right" }}>
                      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end", flexWrap: "wrap" }}>
                        {item.status === "review" && (
                          <button
                            className="primary"
                            style={{ fontSize: 11, padding: "3px 7px" }}
                            onClick={() => handleApprove(item.id)}
                            title="Approve row -> ready for automated posting"
                          >
                            ✓ Approve
                          </button>
                        )}

                        <button
                          style={{ fontSize: 11, padding: "3px 7px", background: "#7c3aed", color: "#fff", borderColor: "#6d28d9" }}
                          onClick={() => handlePublishNow(item.id)}
                          title="Publish immediately to Outstand"
                        >
                          🚀 Post Now
                        </button>

                        <button
                          style={{ fontSize: 11, padding: "3px 7px" }}
                          onClick={() => handleGenerateAi(item.id)}
                          title="Generate viral title & hashtags via AI"
                        >
                          ✨ AI
                        </button>

                        <button
                          style={{ fontSize: 11, padding: "3px 7px" }}
                          onClick={() => setEditItem(item)}
                          title="Edit row details"
                        >
                          ✏️ Edit
                        </button>

                        <button
                          className="danger"
                          style={{ fontSize: 11, padding: "3px 7px" }}
                          onClick={() => handleDelete(item.id)}
                          title="Delete queue item"
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

      {/* Edit Row Modal */}
      {editItem && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 99999,
            padding: 16,
          }}
          onClick={() => setEditItem(null)}
        >
          <div
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              width: "100%",
              maxWidth: 580,
              padding: 20,
              boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <span style={{ fontWeight: 700, fontSize: 15 }}>✏️ Edit Queue Row #{editItem.id}</span>
              <button onClick={() => setEditItem(null)} style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer" }}>✕</button>
            </div>

            <div style={{ display: "grid", gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>TITLE</label>
                <input
                  type="text"
                  value={editItem.title}
                  onChange={(e) => setEditItem({ ...editItem, title: e.target.value })}
                />
              </div>

              <div>
                <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>DESCRIPTION / CAPTION</label>
                <textarea
                  rows={4}
                  value={editItem.description}
                  onChange={(e) => setEditItem({ ...editItem, description: e.target.value })}
                />
              </div>

              <div>
                <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>TAGS & HASHTAGS</label>
                <input
                  type="text"
                  value={editItem.tags}
                  onChange={(e) => setEditItem({ ...editItem, tags: e.target.value })}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div>
                  <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>CHANNEL / PIPELINE</label>
                  <input
                    type="text"
                    value={editItem.pipeline}
                    onChange={(e) => setEditItem({ ...editItem, pipeline: e.target.value })}
                  />
                </div>
                <div>
                  <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>STATUS</label>
                  <select
                    value={editItem.status}
                    onChange={(e) => setEditItem({ ...editItem, status: e.target.value })}
                    style={{ background: "var(--bg)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px", width: "100%" }}
                  >
                    <option value="review">Review</option>
                    <option value="ready">Ready to post</option>
                    <option value="posted">Posted</option>
                    <option value="retry">Retry</option>
                    <option value="error">Error</option>
                    <option value="archived">Archived</option>
                  </select>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div>
                  <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>SOURCE (Origin Folder/Creator)</label>
                  <input
                    type="text"
                    value={editItem.source || ""}
                    onChange={(e) => setEditItem({ ...editItem, source: e.target.value })}
                  />
                </div>
                <div>
                  <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>GOOGLE DRIVE LINK</label>
                  <input
                    type="text"
                    value={editItem.drive_link || ""}
                    onChange={(e) => setEditItem({ ...editItem, drive_link: e.target.value })}
                    placeholder="https://drive.google.com/..."
                  />
                </div>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
              <button onClick={() => setEditItem(null)}>Cancel</button>
              <button className="primary" onClick={handleSaveEdit} disabled={savingEdit}>
                {savingEdit ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add New Item Modal */}
      {showAddModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 99999,
            padding: 16,
          }}
          onClick={() => setShowAddModal(false)}
        >
          <div
            style={{
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 12,
              width: "100%",
              maxWidth: 540,
              padding: 20,
              boxShadow: "0 20px 40px rgba(0,0,0,0.5)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <span style={{ fontWeight: 700, fontSize: 15 }}>+ Add New Video to Queue</span>
              <button onClick={() => setShowAddModal(false)} style={{ background: "none", border: "none", color: "var(--muted)", cursor: "pointer" }}>✕</button>
            </div>

            <div style={{ display: "grid", gap: 12 }}>
              <div>
                <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>TITLE *</label>
                <input
                  type="text"
                  placeholder="Catchy video title"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                />
              </div>

              <div>
                <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>CAPTION / DESCRIPTION</label>
                <textarea
                  rows={3}
                  placeholder="Engaging caption with call to action"
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                />
              </div>

              <div>
                <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>HASHTAGS</label>
                <input
                  type="text"
                  placeholder="#viral #trending #reels #shorts"
                  value={newTags}
                  onChange={(e) => setNewTags(e.target.value)}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div>
                  <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>CHANNEL / PIPELINE</label>
                  <select
                    value={newPipeline}
                    onChange={(e) => setNewPipeline(e.target.value)}
                    style={{ background: "var(--bg)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px", width: "100%" }}
                  >
                    <option value="Movie Clips">Movie Clips</option>
                    <option value="Abyss Declassified">Abyss Declassified</option>
                    <option value="The ICK Room">The ICK Room</option>
                    <option value="default">Default</option>
                  </select>
                </div>
                <div>
                  <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>SOURCE (Origin Folder/Channel)</label>
                  <input
                    type="text"
                    placeholder="e.g. Source: @EditAetheris"
                    value={newSource}
                    onChange={(e) => setNewSource(e.target.value)}
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>GOOGLE DRIVE LINK (Optional)</label>
                <input
                  type="text"
                  placeholder="https://drive.google.com/file/d/..."
                  value={newDriveLink}
                  onChange={(e) => setNewDriveLink(e.target.value)}
                />
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
              <button onClick={() => setShowAddModal(false)}>Cancel</button>
              <button className="primary" onClick={handleCreateNew}>
                Add to Queue (Review)
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
