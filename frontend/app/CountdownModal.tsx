"use client";

import { useEffect, useState } from "react";
import {
  api,
  CinemetaTitle,
  CompilationChapter,
  CompilationPreview,
  EligibleProject,
  FmhyResource,
  StitchResult,
} from "@/lib/api";
import { notify } from "@/lib/dialogs";

interface CountdownModalProps {
  initialProjectIds: number[];
  onClose: () => void;
  onSuccess?: (result: StitchResult) => void;
}

export function CountdownModal({ initialProjectIds, onClose, onSuccess }: CountdownModalProps) {
  const [projects, setProjects] = useState<EligibleProject[]>([]);
  const [orderedIds, setOrderedIds] = useState<number[]>(initialProjectIds);
  const [title, setTitle] = useState<string>(`Top ${initialProjectIds.length} Must-Watch Countdown`);
  const [preview, setPreview] = useState<CompilationPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [stitching, setStitching] = useState(false);
  const [result, setResult] = useState<StitchResult | null>(null);
  const [showScriptEdit, setShowScriptEdit] = useState(false);
  const [customIntro, setCustomIntro] = useState("");
  const [customOutro, setCustomOutro] = useState("");

  // Cinemeta & FMHY state
  const [activeTab, setActiveTab] = useState<"sequence" | "cinemeta" | "fmhy">("sequence");
  const [cinemetaType, setCinemetaType] = useState<"movie" | "tv">("movie");
  const [cinemetaTitles, setCinemetaTitles] = useState<CinemetaTitle[]>([]);
  const [loadingCinemeta, setLoadingCinemeta] = useState(false);
  const [fmhyResources, setFmhyResources] = useState<FmhyResource[]>([]);

  useEffect(() => {
    if (activeTab === "cinemeta" && cinemetaTitles.length === 0) {
      setLoadingCinemeta(true);
      api.cinemetaTop(cinemetaType)
        .then(setCinemetaTitles)
        .catch(() => {})
        .finally(() => setLoadingCinemeta(false));
    } else if (activeTab === "fmhy" && fmhyResources.length === 0) {
      api.fmhyResources().then(setFmhyResources).catch(() => {});
    }
  }, [activeTab, cinemetaType]);

  const loadCinemetaCatalog = (type: "movie" | "tv") => {
    setCinemetaType(type);
    setLoadingCinemeta(true);
    api.cinemetaTop(type)
      .then(setCinemetaTitles)
      .catch(() => {})
      .finally(() => setLoadingCinemeta(false));
  };

  // Load eligible projects
  useEffect(() => {
    setLoading(true);
    api.compilationEligibleProjects()
      .then((all) => {
        setProjects(all);
        if (orderedIds.length === 0 && all.length > 0) {
          const autoPick = all.slice(0, Math.min(10, all.length)).map((p) => p.id);
          setOrderedIds(autoPick);
          setTitle(`Top ${autoPick.length} Must-Watch Countdown`);
        }
      })
      .catch((err) => {
        notify("Failed to load projects: " + err.message);
      })
      .finally(() => setLoading(false));
  }, []);

  // Update preview whenever orderedIds or title changes
  useEffect(() => {
    if (orderedIds.length < 2) {
      setPreview(null);
      return;
    }
    api.compilationPreview({ project_ids: orderedIds, title })
      .then(setPreview)
      .catch(() => {});
  }, [orderedIds, title]);

  const orderedProjects = orderedIds
    .map((id) => projects.find((p) => p.id === id))
    .filter(Boolean) as EligibleProject[];

  const availableUnselected = projects.filter((p) => !orderedIds.includes(p.id));

  function addItem(id: number) {
    if (orderedIds.includes(id)) return;
    const next = [...orderedIds, id];
    setOrderedIds(next);
    setTitle(`Top ${next.length} Must-Watch Countdown`);
  }

  function addAll() {
    const next = [...orderedIds, ...availableUnselected.map((p) => p.id)];
    setOrderedIds(next);
    setTitle(`Top ${next.length} Must-Watch Countdown`);
  }

  function pickTop(count: number) {
    const pool = projects.slice(0, count).map((p) => p.id);
    setOrderedIds(pool);
    setTitle(`Top ${pool.length} Must-Watch Countdown`);
  }

  function moveUp(index: number) {
    if (index <= 0) return;
    const next = [...orderedIds];
    const temp = next[index - 1];
    next[index - 1] = next[index];
    next[index] = temp;
    setOrderedIds(next);
  }

  function moveDown(index: number) {
    if (index >= orderedIds.length - 1) return;
    const next = [...orderedIds];
    const temp = next[index + 1];
    next[index + 1] = next[index];
    next[index] = temp;
    setOrderedIds(next);
  }

  function removeItem(id: number) {
    const next = orderedIds.filter((item) => item !== id);
    setOrderedIds(next);
    if (next.length > 0) {
      setTitle(`Top ${next.length} Must-Watch Countdown`);
    }
  }

  async function handleStitch() {
    if (orderedIds.length < 2) {
      notify("Select at least 2 projects.");
      return;
    }
    setStitching(true);
    try {
      const res = await api.compilationStitch({
        project_ids: orderedIds,
        title,
        custom_intro: customIntro,
        custom_outro: customOutro,
      });
      setResult(res);
      notify(`Countdown compilation rendered! Saved to Posting Queue (#${res.queue_item_id})`);
      if (onSuccess) onSuccess(res);
    } catch (err: any) {
      notify("Failed to assemble countdown: " + (err.message || String(err)));
    } finally {
      setStitching(false);
    }
  }

  const isMonetizable = (preview?.total_minutes || 0) >= 8.0;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        backgroundColor: "rgba(0, 0, 0, 0.85)",
        zIndex: 9999,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "16px",
      }}
    >
      <div
        style={{
          backgroundColor: "#16181f",
          border: "1px solid #2a2e3d",
          borderRadius: "16px",
          width: "100%",
          maxWidth: "880px",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.7)",
          color: "#e2e8f0",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "20px 24px",
            borderBottom: "1px solid #2a2e3d",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            background: "linear-gradient(90deg, #16181f 0%, #1e2230 100%)",
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <span style={{ fontSize: "24px" }}>🎬</span>
              <h2 style={{ margin: 0, fontSize: "20px", fontWeight: 700, color: "#fff" }}>
                Top {orderedIds.length} Countdown Creator
              </h2>
              {isMonetizable && (
                <span
                  style={{
                    backgroundColor: "rgba(34, 197, 94, 0.15)",
                    border: "1px solid #22c55e",
                    color: "#4ade80",
                    fontSize: "12px",
                    fontWeight: 600,
                    padding: "3px 8px",
                    borderRadius: "12px",
                  }}
                >
                  ✓ YouTube Mid-Roll Eligible (≥8m)
                </span>
              )}
            </div>
            <p style={{ margin: "4px 0 0 0", fontSize: "13px", color: "#94a3b8" }}>
              Assembles Intro montage, countdown bumpers (#10 down to #1), normalized audio & chapters.
            </p>
          </div>
          <button
            onClick={onClose}
            style={{
              background: "transparent",
              border: "none",
              color: "#94a3b8",
              fontSize: "24px",
              cursor: "pointer",
              padding: "4px 8px",
            }}
          >
            ✕
          </button>
        </div>

        {/* Tab Navigation */}
        <div
          style={{
            display: "flex",
            gap: "8px",
            padding: "8px 24px 0",
            borderBottom: "1px solid #2a2e3d",
            backgroundColor: "#13151d",
          }}
        >
          <button
            onClick={() => setActiveTab("sequence")}
            style={{
              padding: "8px 16px",
              background: "transparent",
              border: "none",
              borderBottom: activeTab === "sequence" ? "2px solid #e50914" : "2px solid transparent",
              color: activeTab === "sequence" ? "#fff" : "#94a3b8",
              fontWeight: activeTab === "sequence" ? 700 : 500,
              fontSize: "13px",
              cursor: "pointer",
            }}
          >
            📋 Countdown Sequence ({orderedIds.length})
          </button>
          <button
            onClick={() => setActiveTab("cinemeta")}
            style={{
              padding: "8px 16px",
              background: "transparent",
              border: "none",
              borderBottom: activeTab === "cinemeta" ? "2px solid #e50914" : "2px solid transparent",
              color: activeTab === "cinemeta" ? "#fff" : "#94a3b8",
              fontWeight: activeTab === "cinemeta" ? 700 : 500,
              fontSize: "13px",
              cursor: "pointer",
            }}
          >
            ⚡ Stremio Cinemeta Catalog
          </button>
          <button
            onClick={() => setActiveTab("fmhy")}
            style={{
              padding: "8px 16px",
              background: "transparent",
              border: "none",
              borderBottom: activeTab === "fmhy" ? "2px solid #e50914" : "2px solid transparent",
              color: activeTab === "fmhy" ? "#fff" : "#94a3b8",
              fontWeight: activeTab === "fmhy" ? 700 : 500,
              fontSize: "13px",
              cursor: "pointer",
            }}
          >
            📚 FMHY Tools & Resources
          </button>
        </div>

        {/* Body Content */}
        <div style={{ padding: "24px", overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: "20px" }}>
          {activeTab === "cinemeta" ? (
            /* Cinemeta View */
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <h3 style={{ margin: 0, fontSize: "16px", color: "#fff" }}>Stremio Cinemeta Catalog</h3>
                  <p style={{ margin: "4px 0 0", fontSize: "12px", color: "#94a3b8" }}>
                    Public keyless JSON metadata feed · Real-time IMDb ratings, cast, and trailers
                  </p>
                </div>
                <div style={{ display: "flex", gap: "6px" }}>
                  <button
                    onClick={() => loadCinemetaCatalog("movie")}
                    style={{
                      padding: "6px 12px",
                      borderRadius: "6px",
                      border: "none",
                      backgroundColor: cinemetaType === "movie" ? "#e50914" : "#2a2e3d",
                      color: "#fff",
                      fontSize: "12px",
                      cursor: "pointer",
                    }}
                  >
                    🎬 Trending Movies
                  </button>
                  <button
                    onClick={() => loadCinemetaCatalog("tv")}
                    style={{
                      padding: "6px 12px",
                      borderRadius: "6px",
                      border: "none",
                      backgroundColor: cinemetaType === "tv" ? "#e50914" : "#2a2e3d",
                      color: "#fff",
                      fontSize: "12px",
                      cursor: "pointer",
                    }}
                  >
                    📺 Trending Series
                  </button>
                </div>
              </div>

              {loadingCinemeta ? (
                <div style={{ padding: "30px", textAlign: "center", color: "#94a3b8" }}>Loading Cinemeta catalog...</div>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: "12px" }}>
                  {cinemetaTitles.map((item) => (
                    <div
                      key={item.imdb_id}
                      style={{
                        backgroundColor: "#1a1d28",
                        border: "1px solid #2a2e3d",
                        borderRadius: "10px",
                        padding: "12px",
                        display: "flex",
                        flexDirection: "column",
                        gap: "8px",
                      }}
                    >
                      <div style={{ display: "flex", gap: "10px" }}>
                        {item.poster ? (
                          <img src={item.poster} alt="" style={{ width: "48px", height: "72px", objectFit: "cover", borderRadius: "4px" }} />
                        ) : (
                          <div style={{ width: "48px", height: "72px", backgroundColor: "#334155", borderRadius: "4px" }} />
                        )}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 700, fontSize: "13px", color: "#fff", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                            {item.title}
                          </div>
                          <div style={{ fontSize: "11px", color: "#94a3b8" }}>
                            {item.year} · ★ {item.rating > 0 ? item.rating.toFixed(1) : "TBD"}
                          </div>
                          <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>
                            IMDb: {item.imdb_id}
                          </div>
                        </div>
                      </div>

                      {item.genres?.length > 0 && (
                        <div style={{ fontSize: "11px", color: "#93c5fd" }}>
                          {item.genres.slice(0, 3).join(", ")}
                        </div>
                      )}

                      {item.trailers?.length > 0 && (
                        <div style={{ fontSize: "11px", color: "#4ade80" }}>
                          ▶ {item.trailers.length} Official Trailer{item.trailers.length > 1 ? "s" : ""}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : activeTab === "fmhy" ? (
            /* FMHY View */
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              <div>
                <h3 style={{ margin: 0, fontSize: "16px", color: "#fff" }}>FreeMediaHeckYeah (FMHY) Directory</h3>
                <p style={{ margin: "4px 0 0", fontSize: "12px", color: "#94a3b8" }}>
                  Curated video downloaders, media tracking platforms, and copyright-safe audio libraries
                </p>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: "12px" }}>
                {fmhyResources.map((res, i) => (
                  <div
                    key={i}
                    style={{
                      backgroundColor: "#1a1d28",
                      border: "1px solid #2a2e3d",
                      borderRadius: "10px",
                      padding: "14px",
                      display: "flex",
                      flexDirection: "column",
                      gap: "6px",
                    }}
                  >
                    <div style={{ fontSize: "11px", color: "#e50914", fontWeight: 700, textTransform: "uppercase" }}>
                      {res.category}
                    </div>
                    <div style={{ fontWeight: 700, fontSize: "14px", color: "#fff" }}>
                      <a href={res.url} target="_blank" rel="noopener noreferrer" style={{ color: "#fff", textDecoration: "none" }}>
                        {res.name} ↗
                      </a>
                    </div>
                    <p style={{ margin: 0, fontSize: "12px", color: "#94a3b8", lineHeight: "1.4" }}>
                      {res.description}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          ) : result ? (
            /* Success View */
            <div
              style={{
                backgroundColor: "rgba(34, 197, 94, 0.08)",
                border: "1px solid #22c55e",
                borderRadius: "12px",
                padding: "24px",
                display: "flex",
                flexDirection: "column",
                gap: "16px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                <span style={{ fontSize: "32px" }}>🎉</span>
                <div>
                  <h3 style={{ margin: 0, color: "#4ade80", fontSize: "18px" }}>Compilation Successfully Rendered!</h3>
                  <p style={{ margin: "4px 0 0", fontSize: "14px", color: "#cbd5e1" }}>
                    Total Runtime: <strong>{result.total_minutes} min</strong> ({result.total_seconds}s) · 3 Thumbnails Generated
                  </p>
                </div>
              </div>

              {/* Chapters Box */}
              <div>
                <label style={{ fontSize: "12px", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase" }}>
                  YouTube Timestamps & Chapters:
                </label>
                <pre
                  style={{
                    backgroundColor: "#0d0f17",
                    padding: "12px",
                    borderRadius: "8px",
                    fontSize: "12px",
                    color: "#93c5fd",
                    marginTop: "6px",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {result.chapters_text}
                </pre>
              </div>

              <div style={{ display: "flex", gap: "12px", marginTop: "8px" }}>
                <button
                  onClick={onClose}
                  style={{
                    backgroundColor: "#22c55e",
                    color: "#000",
                    fontWeight: 700,
                    padding: "10px 20px",
                    borderRadius: "8px",
                    border: "none",
                    cursor: "pointer",
                  }}
                >
                  Done (View in Posting Queue)
                </button>
              </div>
            </div>
          ) : (

            <>
              {/* Title & Runtime Stat */}
              <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", alignItems: "flex-end" }}>
                <div style={{ flex: 1, minWidth: "280px" }}>
                  <label style={{ display: "block", fontSize: "13px", fontWeight: 600, color: "#cbd5e1", marginBottom: "6px" }}>
                    YouTube Countdown Title:
                  </label>
                  <input
                    type="text"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    style={{
                      width: "100%",
                      padding: "10px 14px",
                      backgroundColor: "#0f111a",
                      border: "1px solid #334155",
                      borderRadius: "8px",
                      color: "#fff",
                      fontSize: "14px",
                    }}
                  />
                </div>
                <div
                  style={{
                    backgroundColor: "#0f111a",
                    border: "1px solid #334155",
                    borderRadius: "8px",
                    padding: "8px 16px",
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontSize: "11px", color: "#94a3b8", textTransform: "uppercase" }}>Estimated Length</div>
                  <div style={{ fontSize: "18px", fontWeight: 700, color: isMonetizable ? "#4ade80" : "#f59e0b" }}>
                    {preview ? `${preview.total_minutes} min` : "Calculating..."}
                  </div>
                </div>
              </div>

              {/* Ranked Items List */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px", flexWrap: "wrap", gap: "6px" }}>
                  <div>
                    <label style={{ fontSize: "13px", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase" }}>
                      Countdown Sequence ({orderedProjects.length} items):
                    </label>
                    <div style={{ fontSize: "11px", color: "#64748b" }}>
                      Top of list plays first (#5) ➔ Bottom plays last (#1)
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "6px" }}>
                    {projects.length >= 5 && (
                      <button
                        type="button"
                        onClick={() => pickTop(5)}
                        style={{ padding: "4px 8px", fontSize: "11px", borderRadius: "4px", backgroundColor: "#272a38", color: "#cbd5e1", border: "1px solid #3b4252", cursor: "pointer" }}
                      >
                        Pick Top 5
                      </button>
                    )}
                    {projects.length >= 10 && (
                      <button
                        type="button"
                        onClick={() => pickTop(10)}
                        style={{ padding: "4px 8px", fontSize: "11px", borderRadius: "4px", backgroundColor: "#272a38", color: "#cbd5e1", border: "1px solid #3b4252", cursor: "pointer" }}
                      >
                        Pick Top 10
                      </button>
                    )}
                    {availableUnselected.length > 0 && (
                      <button
                        type="button"
                        onClick={addAll}
                        style={{ padding: "4px 8px", fontSize: "11px", borderRadius: "4px", backgroundColor: "#272a38", color: "#38bdf8", border: "1px solid #38bdf8", cursor: "pointer", fontWeight: 600 }}
                      >
                        + Add All Available ({availableUnselected.length})
                      </button>
                    )}
                  </div>
                </div>

                {orderedProjects.length === 0 ? (
                  <div style={{ padding: "28px", textAlign: "center", backgroundColor: "#1a1d28", borderRadius: "10px", border: "1px dashed #334155" }}>
                    <p style={{ margin: 0, color: "#94a3b8", fontSize: "14px" }}>
                      No videos currently in this countdown sequence.
                    </p>
                    {projects.length > 0 ? (
                      <button
                        onClick={addAll}
                        style={{ marginTop: "12px", padding: "8px 16px", borderRadius: "6px", backgroundColor: "#e50914", color: "#fff", border: "none", cursor: "pointer", fontWeight: 600 }}
                      >
                        + Add All {projects.length} Available Rendered Videos
                      </button>
                    ) : (
                      <p style={{ margin: "8px 0 0", color: "#64748b", fontSize: "12px" }}>
                        No rendered breakdown videos found. Render at least 2 videos in LongForm Studio first!
                      </p>
                    )}
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {orderedProjects.map((p, idx) => {
                      const rank = orderedProjects.length - idx;
                      const isTopRank = rank === 1;
                      return (
                        <div
                          key={p.id}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "12px",
                            backgroundColor: isTopRank ? "rgba(229, 9, 20, 0.12)" : "#1a1d28",
                            border: isTopRank ? "1px solid rgba(229, 9, 20, 0.6)" : "1px solid #2a2e3d",
                            padding: "10px 14px",
                            borderRadius: "10px",
                          }}
                        >
                          {/* Rank Badge */}
                          <div
                            style={{
                              width: "36px",
                              height: "36px",
                              borderRadius: "8px",
                              backgroundColor: isTopRank ? "#e50914" : "#334155",
                              color: "#fff",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              fontWeight: 800,
                              fontSize: "14px",
                            }}
                          >
                            #{rank}
                          </div>

                          {/* Title & Runtime */}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontWeight: 600, fontSize: "14px", color: "#fff", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                              {p.title}
                            </div>
                            <div style={{ fontSize: "12px", color: "#94a3b8" }}>
                              {p.facts.primary_date ? `Release: ${p.facts.primary_date.slice(0, 4)}` : ""} · {Math.round(p.seconds)}s duration
                            </div>
                          </div>

                          {/* Reordering Controls */}
                          <div style={{ display: "flex", gap: "4px" }}>
                            <button
                              onClick={() => moveUp(idx)}
                              disabled={idx === 0}
                              style={{
                                backgroundColor: "#272a38",
                                border: "none",
                                color: idx === 0 ? "#475569" : "#cbd5e1",
                                padding: "6px 10px",
                                borderRadius: "6px",
                                cursor: idx === 0 ? "default" : "pointer",
                              }}
                            >
                              ▲
                            </button>
                            <button
                              onClick={() => moveDown(idx)}
                              disabled={idx === orderedProjects.length - 1}
                              style={{
                                backgroundColor: "#272a38",
                                border: "none",
                                color: idx === orderedProjects.length - 1 ? "#475569" : "#cbd5e1",
                                padding: "6px 10px",
                                borderRadius: "6px",
                                cursor: idx === orderedProjects.length - 1 ? "default" : "pointer",
                              }}
                            >
                              ▼
                            </button>
                            <button
                              onClick={() => removeItem(p.id)}
                              style={{
                                backgroundColor: "rgba(239, 68, 68, 0.15)",
                                border: "none",
                                color: "#f87171",
                                padding: "6px 10px",
                                borderRadius: "6px",
                                cursor: "pointer",
                              }}
                            >
                              ✕
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {/* Available unselected rendered videos */}
                {availableUnselected.length > 0 && (
                  <div style={{ marginTop: "18px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                      <label style={{ fontSize: "12px", fontWeight: 700, color: "#64748b", textTransform: "uppercase" }}>
                        Available Rendered Videos ({availableUnselected.length}):
                      </label>
                      <button
                        type="button"
                        onClick={addAll}
                        style={{ background: "none", border: "none", color: "#38bdf8", fontSize: "12px", cursor: "pointer", fontWeight: 600 }}
                      >
                        + Add All
                      </button>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                      {availableUnselected.map((p) => (
                        <div
                          key={p.id}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            padding: "8px 12px",
                            backgroundColor: "#13151f",
                            borderRadius: "8px",
                            border: "1px solid #2a2e3d",
                          }}
                        >
                          <div style={{ minWidth: 0, flex: 1 }}>
                            <div style={{ fontSize: "13px", fontWeight: 600, color: "#e2e8f0", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                              {p.title}
                            </div>
                            <div style={{ fontSize: "11px", color: "#64748b" }}>
                              {p.facts.primary_date ? `Release: ${p.facts.primary_date.slice(0, 4)} · ` : ""}{Math.round(p.seconds)}s duration
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => addItem(p.id)}
                            style={{
                              padding: "5px 12px",
                              borderRadius: "6px",
                              backgroundColor: "#272a38",
                              color: "#38bdf8",
                              border: "1px solid #38bdf8",
                              fontSize: "12px",
                              fontWeight: 600,
                              cursor: "pointer",
                            }}
                          >
                            + Add to Countdown
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Optional Custom Voiceover Scripts */}
              <div>
                <button
                  type="button"
                  onClick={() => setShowScriptEdit(!showScriptEdit)}
                  style={{
                    background: "none",
                    border: "none",
                    color: "#93c5fd",
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: "pointer",
                    padding: 0,
                  }}
                >
                  {showScriptEdit ? "▼ Hide Script Customization" : "▶ Customize Intro & Outro Voiceover Hook"}
                </button>

                {showScriptEdit && (
                  <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "10px" }}>
                    <div>
                      <label style={{ fontSize: "11px", color: "#94a3b8" }}>Intro Narration Hook (leave empty for automatic):</label>
                      <textarea
                        rows={2}
                        value={customIntro}
                        onChange={(e) => setCustomIntro(e.target.value)}
                        placeholder="Welcome back to Screen Central! Today we're breaking down..."
                        style={{ width: "100%", padding: "8px", backgroundColor: "#0f111a", border: "1px solid #334155", borderRadius: "6px", color: "#fff", fontSize: "12px" }}
                      />
                    </div>
                    <div>
                      <label style={{ fontSize: "11px", color: "#94a3b8" }}>Outro Discussion Hook (leave empty for automatic):</label>
                      <textarea
                        rows={2}
                        value={customOutro}
                        onChange={(e) => setCustomOutro(e.target.value)}
                        placeholder="Which of these titles are you watching first? Let us know in the comments..."
                        style={{ width: "100%", padding: "8px", backgroundColor: "#0f111a", border: "1px solid #334155", borderRadius: "6px", color: "#fff", fontSize: "12px" }}
                      />
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        {!result && (
          <div
            style={{
              padding: "16px 24px",
              borderTop: "1px solid #2a2e3d",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              backgroundColor: "#13151c",
            }}
          >
            <button
              onClick={onClose}
              disabled={stitching}
              style={{
                backgroundColor: "transparent",
                border: "1px solid #475569",
                color: "#cbd5e1",
                padding: "8px 16px",
                borderRadius: "8px",
                cursor: "pointer",
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleStitch}
              disabled={stitching || orderedIds.length < 2}
              style={{
                backgroundColor: stitching ? "#64748b" : "#e50914",
                color: "#fff",
                fontWeight: 700,
                padding: "10px 24px",
                borderRadius: "8px",
                border: "none",
                cursor: stitching ? "wait" : "pointer",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              {stitching ? (
                <>
                  <span style={{ display: "inline-block", animation: "spin 1s linear infinite" }}>⏳</span>
                  Assembling Countdown ({orderedIds.length} items)...
                </>
              ) : (
                `🚀 Build Top ${orderedIds.length} Countdown & Send to Queue`
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
