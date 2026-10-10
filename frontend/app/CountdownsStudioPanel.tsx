"use client";

import React, { useEffect, useState, useMemo } from "react";
import {
  api,
  CountdownTrigger,
  CountdownTopicCandidate,
  ResearchedCountdown,
  CountdownEntry,
  StudioProject,
} from "../lib/api";
import { notify, askConfirm } from "../lib/dialogs";
import { CountdownModal } from "./CountdownModal";

const cardStyle: React.CSSProperties = {
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  padding: "16px 18px",
};

const badgeStyle = (bg: string, fg: string): React.CSSProperties => ({
  fontSize: 11,
  fontWeight: 700,
  padding: "3px 8px",
  borderRadius: 6,
  background: bg,
  color: fg,
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
});

export function CountdownsStudioPanel({ projects = [], onReloadProjects }: { projects?: StudioProject[]; onReloadProjects?: () => void }) {
  const [subView, setSubView] = useState<"discovery" | "researched" | "saved">("discovery");
  const [triggers, setTriggers] = useState<CountdownTrigger[]>([]);
  const [selectedTrigger, setSelectedTrigger] = useState<string>("all");
  const [candidates, setCandidates] = useState<CountdownTopicCandidate[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState(false);

  // Custom Topic Form
  const [customTopic, setCustomTopic] = useState("");
  const [customFormat, setCustomFormat] = useState<"top5" | "top10" | "top15">("top10");
  const [customAngle, setCustomAngle] = useState("");
  const [researching, setResearching] = useState(false);
  const [researchStatusText, setResearchStatusText] = useState("");

  // Researched Countdown Data
  const [currentCountdown, setCurrentCountdown] = useState<ResearchedCountdown | null>(null);

  // Saved Countdowns
  const [savedList, setSavedList] = useState<ResearchedCountdown[]>([]);
  const [loadingSaved, setLoadingSaved] = useState(false);

  // Stitch Modal
  const [showStitchModal, setShowStitchModal] = useState(false);

  // Load triggers and candidates on mount
  useEffect(() => {
    api.countdownTriggers()
      .then(setTriggers)
      .catch((e) => console.error("Error loading triggers:", e));

    loadCandidates("all");
    loadSaved();
  }, []);

  const loadCandidates = async (triggerFilter: string, forceRefresh = false) => {
    setLoadingCandidates(true);
    try {
      const data = await api.countdownTopics(triggerFilter, forceRefresh);
      setCandidates(data);
    } catch (e: any) {
      notify("Failed to discover topics: " + (e.message || String(e)));
    } finally {
      setLoadingCandidates(false);
    }
  };

  const loadSaved = async () => {
    setLoadingSaved(true);
    try {
      const data = await api.savedCountdowns();
      setSavedList(data);
      if (data && data.length > 0) {
        const lastId = typeof window !== "undefined" ? localStorage.getItem("scrapper_last_countdown_id") : null;
        const found = lastId ? data.find((d) => d.id === lastId) : null;
        setCurrentCountdown((prev) => prev || found || data[0]);
      }
    } catch (e) {
      console.error("Failed to load saved countdowns:", e);
    } finally {
      setLoadingSaved(false);
    }
  };

  const handleSelectTrigger = (tId: string) => {
    setSelectedTrigger(tId);
    loadCandidates(tId, false);
  };

  const handleResearchTopic = async (topic: string, format: string, instructions = "") => {
    if (!topic.trim()) {
      notify("Please enter a countdown topic");
      return;
    }
    setResearching(true);
    setResearchStatusText("Querying IMDb, JustWatch & Letterboxd signals...");
    try {
      setTimeout(() => {
        setResearchStatusText("Normalizing composite ranking scores (Ratings 35%, Impact 25%, Buzz 20%, Sentiment 20%)...");
      }, 2500);
      setTimeout(() => {
        setResearchStatusText("Drafting retention hook script, verifying US streaming & controversy pacing...");
      }, 5500);

      const res = await api.countdownResearch({
        topic: topic.trim(),
        format,
        custom_instructions: instructions,
      });
      setCurrentCountdown(res);
      setSubView("researched");
      if (typeof window !== "undefined" && res.id) {
        localStorage.setItem("scrapper_last_countdown_id", res.id);
      }
      loadSaved();
      notify(`Research complete for "${res.topic}"!`);
    } catch (e: any) {
      notify("Research failed: " + (e.message || String(e)));
    } finally {
      setResearching(false);
      setResearchStatusText("");
    }
  };

  const handleSaveCountdown = async () => {
    if (!currentCountdown) return;
    try {
      await api.saveCountdown(currentCountdown);
      notify("Saved to Countdowns Library!");
      loadSaved();
    } catch (e: any) {
      notify("Failed to save: " + (e.message || String(e)));
    }
  };

  const handleDeleteSaved = async (id?: string) => {
    if (!id) return;
    const ok = await askConfirm("Delete this saved countdown list?");
    if (!ok) return;
    try {
      await api.deleteSavedCountdown(id);
      notify("Countdown deleted");
      loadSaved();
      if (currentCountdown?.id === id) {
        setCurrentCountdown(null);
        setSubView("discovery");
      }
    } catch (e: any) {
      notify("Failed to delete: " + (e.message || String(e)));
    }
  };

  const copyToClipboard = (text: string, label = "Copied to clipboard!") => {
    navigator.clipboard.writeText(text);
    notify(label);
  };

  const copyFullPackage = () => {
    if (!currentCountdown) return;
    const c = currentCountdown;
    let md = `# ${c.topic}\n\n`;
    md += `**Format:** ${c.format.toUpperCase()}\n`;
    md += `**Why Now:** ${c.why_now}\n\n`;
    md += `### YouTube Title Options:\n`;
    c.title_options.forEach((t, i) => (md += `${i + 1}. ${t}\n`));
    md += `\n**Thumbnail Text:** ${c.thumbnail_text}\n\n`;
    md += `### First 10-Second Hook Script:\n"${c.hook_script}"\n\n`;
    md += `### Ranked Entries:\n`;
    c.entries.forEach((e) => {
      md += `\n#${e.rank}: ${e.title} (${e.year}) — Score: ${e.score}/100\n`;
      md += `- Key Stat: ${e.key_stat}\n`;
      md += `- Why It Ranks: ${e.why_it_ranks}\n`;
      md += `- Fun Fact: ${e.fun_fact}\n`;
      md += `- Where to Watch: ${e.where_to_watch}\n`;
      md += `- Sources: ${e.sources.join(", ")}\n`;
    });
    if (c.honorable_mentions?.length) {
      md += `\n### Honorable Mentions:\n`;
      c.honorable_mentions.forEach((h) => (md += `- ${h}\n`));
    }
    md += `\n### Closing Debate Question:\n"${c.closing_question}"\n`;

    copyToClipboard(md, "Full Countdown package copied as Markdown!");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* ─── Top Studio Banner ─── */}
      <div
        style={{
          ...cardStyle,
          background: "linear-gradient(135deg, rgba(229, 9, 20, 0.12) 0%, rgba(15, 23, 42, 0.8) 100%)",
          border: "1px solid rgba(229, 9, 20, 0.4)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontSize: 22 }}>🏆</span>
            <h1 style={{ margin: 0, fontSize: 18, color: "#f8fafc", fontWeight: 700 }}>
              Top 5 & Top 10 Countdown Studio
            </h1>
            <span style={badgeStyle("#7f1d1d", "#fca5a5")}>Autonomous Research Engine</span>
            <span style={badgeStyle("#1e293b", "#94a3b8")}>TMDB · JustWatch · Letterboxd · IMDb</span>
          </div>
          <p style={{ margin: "6px 0 0", color: "#94a3b8", fontSize: 12 }}>
            Autonomous film/TV list engine: discover high-demand topics, build defensible 0–100 rankings from real data, and script for high retention (8–12m YouTube compilations).
          </p>
        </div>

        {/* Quick Action: Stitcher */}
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            onClick={() => setShowStitchModal(true)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "#b91c1c",
              color: "#fff",
              border: "1px solid #ef4444",
              borderRadius: 8,
              padding: "8px 14px",
              fontWeight: 600,
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            <span>🎬</span>
            <span>Stitch Video (8–12m)</span>
          </button>
        </div>
      </div>

      {/* ─── Sub-Tab Navigation Bar ─── */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: 6,
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => setSubView("discovery")}
            style={{
              padding: "7px 14px",
              borderRadius: 6,
              border: "none",
              background: subView === "discovery" ? "var(--accent)" : "transparent",
              color: subView === "discovery" ? "#fff" : "var(--muted)",
              fontWeight: 600,
              fontSize: 12,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span>🎯</span>
            <span>Topic Discovery & Scoring</span>
          </button>

          <button
            onClick={() => setSubView("researched")}
            style={{
              padding: "7px 14px",
              borderRadius: 6,
              border: "none",
              background: subView === "researched" ? "var(--accent)" : "transparent",
              color: subView === "researched" ? "#fff" : "var(--muted)",
              fontWeight: 600,
              fontSize: 12,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span>📊</span>
            <span>Researched Countdown</span>
            {currentCountdown && (
              <span style={{ fontSize: 10, background: "rgba(255,255,255,0.2)", padding: "1px 6px", borderRadius: 10 }}>
                {currentCountdown.format.toUpperCase()}
              </span>
            )}
          </button>

          <button
            onClick={() => {
              setSubView("saved");
              loadSaved();
            }}
            style={{
              padding: "7px 14px",
              borderRadius: 6,
              border: "none",
              background: subView === "saved" ? "var(--accent)" : "transparent",
              color: subView === "saved" ? "#fff" : "var(--muted)",
              fontWeight: 600,
              fontSize: 12,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <span>📁</span>
            <span>Saved Countdowns ({savedList.length})</span>
          </button>
        </div>

        {subView === "discovery" && (
          <button
            onClick={() => loadCandidates(selectedTrigger, true)}
            disabled={loadingCandidates}
            style={{
              padding: "6px 12px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg)",
              color: "var(--text)",
              fontSize: 11,
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: 5,
            }}
          >
            <span>🔄</span>
            <span>{loadingCandidates ? "Scoring Market Signals..." : "Refresh Demand Signals"}</span>
          </button>
        )}
      </div>

      {/* ────────────────────────────────────────────────────────── */}
      {/* VIEW 1: TOPIC DISCOVERY & SCORING (STEP 1)                 */}
      {/* ────────────────────────────────────────────────────────── */}
      {subView === "discovery" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Custom Topic Input & Formatter */}
          <div style={{ ...cardStyle, background: "rgba(15, 23, 42, 0.4)" }}>
            <h3 style={{ margin: "0 0 8px", fontSize: 14, color: "#f8fafc" }}>
              ⚡ Research Any Film/TV Topic On-Demand
            </h3>
            <p style={{ margin: "0 0 12px", fontSize: 12, color: "var(--muted)" }}>
              The List Engine will query IMDb, Letterboxd, JustWatch streaming availability, box office data, and craft a retention-scripted countdown.
            </p>

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <input
                  type="text"
                  placeholder="e.g. Top 10 Mind-Bending Sci-Fi Movies on Netflix Right Now, Top 10 Highest Grossing Films of All Time, Top 5 Horror Gems..."
                  value={customTopic}
                  onChange={(e) => setCustomTopic(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !researching) {
                      handleResearchTopic(customTopic, customFormat, customAngle);
                    }
                  }}
                  style={{
                    flex: 1,
                    minWidth: 280,
                    background: "var(--bg)",
                    border: "1px solid var(--border)",
                    borderRadius: 6,
                    padding: "10px 14px",
                    color: "var(--text)",
                    fontSize: 13,
                  }}
                />

                {/* Format Selector */}
                <div style={{ display: "flex", gap: 4, background: "var(--bg)", padding: 3, borderRadius: 6, border: "1px solid var(--border)" }}>
                  <button
                    onClick={() => setCustomFormat("top5")}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 4,
                      border: "none",
                      background: customFormat === "top5" ? "#ea580c" : "transparent",
                      color: customFormat === "top5" ? "#fff" : "var(--muted)",
                      fontWeight: 600,
                      fontSize: 11,
                      cursor: "pointer",
                    }}
                  >
                    Top 5 (Fast)
                  </button>
                  <button
                    onClick={() => setCustomFormat("top10")}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 4,
                      border: "none",
                      background: customFormat === "top10" ? "var(--accent)" : "transparent",
                      color: customFormat === "top10" ? "#fff" : "var(--muted)",
                      fontWeight: 600,
                      fontSize: 11,
                      cursor: "pointer",
                    }}
                  >
                    Top 10 (Default)
                  </button>
                  <button
                    onClick={() => setCustomFormat("top15")}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 4,
                      border: "none",
                      background: customFormat === "top15" ? "#7c3aed" : "transparent",
                      color: customFormat === "top15" ? "#fff" : "var(--muted)",
                      fontWeight: 600,
                      fontSize: 11,
                      cursor: "pointer",
                    }}
                  >
                    Top 15 (Deep)
                  </button>
                </div>

                <button
                  onClick={() => handleResearchTopic(customTopic, customFormat, customAngle)}
                  disabled={researching || !customTopic.trim()}
                  style={{
                    background: "var(--accent)",
                    color: "#fff",
                    border: "none",
                    borderRadius: 6,
                    padding: "10px 18px",
                    fontWeight: 700,
                    fontSize: 13,
                    cursor: researching || !customTopic.trim() ? "not-allowed" : "pointer",
                    opacity: researching || !customTopic.trim() ? 0.6 : 1,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                >
                  <span>{researching ? "⏳" : "🔬"}</span>
                  <span>{researching ? "Researching..." : "Research & Rank Countdown"}</span>
                </button>
              </div>

              {/* Optional Custom Angle */}
              <input
                type="text"
                placeholder="Optional custom instructions or criteria (e.g. 'Must include inflation-adjusted box office' or 'Focus on psychological twists')"
                value={customAngle}
                onChange={(e) => setCustomAngle(e.target.value)}
                style={{
                  background: "var(--bg)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: "7px 12px",
                  color: "var(--muted)",
                  fontSize: 11,
                }}
              />

              {researching && (
                <div
                  style={{
                    padding: "10px 14px",
                    borderRadius: 6,
                    background: "rgba(59, 130, 246, 0.1)",
                    border: "1px solid rgba(59, 130, 246, 0.3)",
                    color: "#93c5fd",
                    fontSize: 12,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span className="spinner" />
                  <span>{researchStatusText || "Analyzing database and generating defensible rankings..."}</span>
                </div>
              )}
            </div>
          </div>

          {/* Searched & Researched Countdowns */}
          {savedList.length > 0 && (
            <div style={{ ...cardStyle, background: "rgba(30, 41, 59, 0.45)", border: "1px solid rgba(229, 9, 20, 0.3)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, flexWrap: "wrap", gap: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 16 }}>🕒</span>
                  <h3 style={{ margin: 0, fontSize: 13, color: "#f8fafc", fontWeight: 700 }}>
                    Searched & Researched Countdowns ({savedList.length})
                  </h3>
                  <span style={{ fontSize: 11, color: "var(--muted)" }}>
                    Instant access to previously generated scripts, rankings & streaming data
                  </span>
                </div>
                <button
                  onClick={() => setSubView("saved")}
                  style={{
                    padding: "3px 8px",
                    borderRadius: 4,
                    border: "1px solid var(--border)",
                    background: "transparent",
                    color: "var(--muted)",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  View All Saved Library →
                </button>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
                {savedList.slice(0, 6).map((item) => (
                  <div
                    key={item.id || item.topic}
                    onClick={() => {
                      setCurrentCountdown(item);
                      setSubView("researched");
                      if (typeof window !== "undefined" && item.id) {
                        localStorage.setItem("scrapper_last_countdown_id", item.id);
                      }
                    }}
                    style={{
                      background: "rgba(15, 23, 42, 0.7)",
                      border: "1px solid rgba(255, 255, 255, 0.08)",
                      borderRadius: 8,
                      padding: "10px 12px",
                      cursor: "pointer",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      gap: 8,
                      transition: "border-color 0.15s ease",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                    onMouseLeave={(e) => (e.currentTarget.style.borderColor = "rgba(255, 255, 255, 0.08)")}
                  >
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                        <span style={badgeStyle("#7f1d1d", "#fca5a5")}>
                          {(item.format || "top10").toUpperCase()}
                        </span>
                        <span style={{ fontSize: 10, color: "var(--muted)" }}>
                          {(item.entries || []).length} ranked titles
                        </span>
                      </div>
                      <h4 style={{ margin: "2px 0 0", fontSize: 13, color: "#f8fafc", lineHeight: 1.3 }}>
                        {item.topic}
                      </h4>
                    </div>

                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 11, color: "var(--accent)", fontWeight: 600 }}>
                      <span>View Script & Rankings</span>
                      <span>→</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 5 Demand Trigger Filters */}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
              <div>
                <h3 style={{ margin: 0, fontSize: 14, color: "#f8fafc" }}>
                  🎯 5 Demand Triggers (Step 1 Candidate Discovery)
                </h3>
                <span style={{ fontSize: 11, color: "var(--muted)" }}>
                  Audience-scored candidates: Demand (30%) + Momentum (25%) + Debate (20%) + Gap (15%) + Visuals (10%)
                </span>
              </div>
            </div>

            {/* Filter Pills */}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button
                onClick={() => handleSelectTrigger("all")}
                style={{
                  padding: "6px 12px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: selectedTrigger === "all" ? "var(--accent)" : "var(--panel)",
                  color: selectedTrigger === "all" ? "#fff" : "var(--muted)",
                  fontWeight: 600,
                  fontSize: 11,
                  cursor: "pointer",
                }}
              >
                All Triggers ({candidates.length})
              </button>
              {triggers.map((t) => (
                <button
                  key={t.id}
                  onClick={() => handleSelectTrigger(t.id)}
                  style={{
                    padding: "6px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--border)",
                    background: selectedTrigger === t.id ? "var(--accent)" : "var(--panel)",
                    color: selectedTrigger === t.id ? "#fff" : "var(--muted)",
                    fontWeight: 600,
                    fontSize: 11,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 5,
                  }}
                >
                  <span>{t.icon}</span>
                  <span>{t.name}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Candidate Topics Grid */}
          {loadingCandidates ? (
            <div style={{ ...cardStyle, textAlign: "center", padding: 40, color: "var(--muted)" }}>
              <span>Scoring candidate topics across market signals...</span>
            </div>
          ) : candidates.length === 0 ? (
            <div style={{ ...cardStyle, textAlign: "center", padding: 30, color: "var(--muted)" }}>
              <span>No candidate topics found for this trigger filter.</span>
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 14 }}>
              {candidates.map((c) => {
                const triggerObj = triggers.find((t) => t.id === c.trigger);
                return (
                  <div
                    key={c.id || c.topic}
                    style={{
                      ...cardStyle,
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "space-between",
                      gap: 12,
                      transition: "transform 0.15s ease, border-color 0.15s ease",
                      border: "1px solid rgba(255, 255, 255, 0.08)",
                      background: "rgba(30, 41, 59, 0.4)",
                    }}
                  >
                    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                        <span style={badgeStyle("#1e293b", "#94a3b8")}>
                          {triggerObj?.icon || "🔥"} {c.trigger_name || c.trigger}
                        </span>
                        <div style={{ display: "flex", gap: 4 }}>
                          <span style={badgeStyle("#7f1d1d", "#fca5a5")}>
                            {c.format.toUpperCase()}
                          </span>
                          <span style={badgeStyle("#064e3b", "#34d399")}>
                            ★ {c.total_score}
                          </span>
                        </div>
                      </div>

                      <h4 style={{ margin: "2px 0 0", fontSize: 14, color: "#f8fafc", lineHeight: 1.3 }}>
                        {c.topic}
                      </h4>

                      <p style={{ margin: 0, fontSize: 11, color: "#cbd5e1", lineHeight: 1.4 }}>
                        {c.why_now}
                      </p>

                      {/* 5-Factor Score Matrix */}
                      <div
                        style={{
                          display: "grid",
                          gridTemplateColumns: "repeat(5, 1fr)",
                          gap: 4,
                          background: "rgba(15, 23, 42, 0.6)",
                          padding: "6px 8px",
                          borderRadius: 6,
                          textAlign: "center",
                          fontSize: 10,
                        }}
                      >
                        <div>
                          <div style={{ color: "var(--muted)" }}>Demand</div>
                          <div style={{ fontWeight: 700, color: "#38bdf8" }}>{c.demand}</div>
                        </div>
                        <div>
                          <div style={{ color: "var(--muted)" }}>Momentum</div>
                          <div style={{ fontWeight: 700, color: "#34d399" }}>{c.momentum}</div>
                        </div>
                        <div>
                          <div style={{ color: "var(--muted)" }}>Debate</div>
                          <div style={{ fontWeight: 700, color: "#f472b6" }}>{c.debate}</div>
                        </div>
                        <div>
                          <div style={{ color: "var(--muted)" }}>Gap</div>
                          <div style={{ fontWeight: 700, color: "#fbbf24" }}>{c.gap}</div>
                        </div>
                        <div>
                          <div style={{ color: "var(--muted)" }}>Visuals</div>
                          <div style={{ fontWeight: 700, color: "#a78bfa" }}>{c.visuals}</div>
                        </div>
                      </div>

                      {/* Suggested entries preview */}
                      {c.suggested_entries && c.suggested_entries.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                          {c.suggested_entries.slice(0, 4).map((entry, idx) => (
                            <span
                              key={idx}
                              style={{
                                fontSize: 10,
                                background: "rgba(255, 255, 255, 0.05)",
                                color: "#94a3b8",
                                padding: "2px 6px",
                                borderRadius: 4,
                              }}
                            >
                              {entry}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    <button
                      onClick={() => handleResearchTopic(c.topic, c.format)}
                      disabled={researching}
                      style={{
                        width: "100%",
                        padding: "8px 12px",
                        borderRadius: 6,
                        border: "1px solid var(--accent)",
                        background: "rgba(16, 185, 129, 0.1)",
                        color: "var(--accent)",
                        fontWeight: 600,
                        fontSize: 12,
                        cursor: researching ? "not-allowed" : "pointer",
                        display: "flex",
                        justifyContent: "center",
                        alignItems: "center",
                        gap: 6,
                      }}
                    >
                      <span>🔬</span>
                      <span>Research & Rank This Topic</span>
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ────────────────────────────────────────────────────────── */}
      {/* VIEW 2: RESEARCHED COUNTDOWN VIEWER (STEPS 2–5)            */}
      {/* ────────────────────────────────────────────────────────── */}
      {subView === "researched" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {!currentCountdown ? (
            <div style={{ ...cardStyle, textAlign: "center", padding: 40, color: "var(--muted)" }}>
              <p>No countdown researched yet. Select a topic from Topic Discovery or input a custom query above!</p>
              <button
                onClick={() => setSubView("discovery")}
                style={{
                  background: "var(--accent)",
                  color: "#fff",
                  border: "none",
                  borderRadius: 6,
                  padding: "8px 16px",
                  fontWeight: 600,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                Browse Candidate Topics
              </button>
            </div>
          ) : (
            <>
              {/* Header Card */}
              <div
                style={{
                  ...cardStyle,
                  background: "linear-gradient(135deg, rgba(30, 41, 59, 0.8) 0%, rgba(15, 23, 42, 0.95) 100%)",
                  border: "1px solid var(--border)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 14,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                      <span style={badgeStyle("#7f1d1d", "#fca5a5")}>
                        {currentCountdown.format.toUpperCase()} COUNTDOWN
                      </span>
                      <span style={badgeStyle("#064e3b", "#34d399")}>
                        {currentCountdown.entries.length} RANKED ENTRIES
                      </span>
                      {currentCountdown.generated_at && (
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>
                          Generated {new Date(currentCountdown.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </span>
                      )}
                    </div>
                    <h2 style={{ margin: "8px 0 4px", fontSize: 20, color: "#fff", fontWeight: 700 }}>
                      {currentCountdown.topic}
                    </h2>
                    <p style={{ margin: 0, fontSize: 13, color: "#93c5fd" }}>
                      💡 <strong>Why Now:</strong> {currentCountdown.why_now}
                    </p>
                  </div>

                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button
                      onClick={handleSaveCountdown}
                      style={{
                        padding: "7px 12px",
                        borderRadius: 6,
                        border: "1px solid var(--border)",
                        background: "var(--panel)",
                        color: "var(--text)",
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                      }}
                    >
                      <span>💾</span>
                      <span>Save Countdown</span>
                    </button>

                    <button
                      onClick={copyFullPackage}
                      style={{
                        padding: "7px 12px",
                        borderRadius: 6,
                        border: "1px solid var(--border)",
                        background: "var(--panel)",
                        color: "var(--text)",
                        fontSize: 12,
                        fontWeight: 600,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                      }}
                    >
                      <span>📋</span>
                      <span>Copy Full Package</span>
                    </button>

                    <button
                      onClick={() => setShowStitchModal(true)}
                      style={{
                        padding: "7px 14px",
                        borderRadius: 6,
                        border: "1px solid #ef4444",
                        background: "#b91c1c",
                        color: "#fff",
                        fontSize: 12,
                        fontWeight: 700,
                        cursor: "pointer",
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                      }}
                    >
                      <span>🎬</span>
                      <span>Stitch 8–12m Video</span>
                    </button>
                  </div>
                </div>

                {/* Packaging & Retention Strategy Card */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                    gap: 12,
                    background: "rgba(15, 23, 42, 0.6)",
                    padding: 14,
                    borderRadius: 8,
                    border: "1px solid rgba(255, 255, 255, 0.05)",
                  }}
                >
                  {/* YouTube Title Options */}
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 700, color: "#38bdf8", marginBottom: 6 }}>
                      🏷️ Clickable YouTube Titles (Under 60 Chars)
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                      {currentCountdown.title_options.map((title, i) => (
                        <div
                          key={i}
                          onClick={() => copyToClipboard(title, "Title copied!")}
                          style={{
                            padding: "6px 10px",
                            borderRadius: 4,
                            background: "rgba(255, 255, 255, 0.04)",
                            border: "1px solid rgba(255, 255, 255, 0.08)",
                            fontSize: 12,
                            color: "#f1f5f9",
                            cursor: "pointer",
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                          }}
                          title="Click to copy title"
                        >
                          <span>{title}</span>
                          <span style={{ fontSize: 10, color: "var(--muted)", marginLeft: 6 }}>
                            {title.length}c · 📋
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Thumbnail Hook & Retention Hook */}
                  <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 700, color: "#f472b6", marginBottom: 4 }}>
                        🖼️ Thumbnail Text Overlay
                      </div>
                      <div
                        style={{
                          fontSize: 14,
                          fontWeight: 800,
                          color: "#fff",
                          background: "#e11d48",
                          display: "inline-block",
                          padding: "4px 10px",
                          borderRadius: 4,
                          letterSpacing: "0.05em",
                        }}
                      >
                        {currentCountdown.thumbnail_text}
                      </div>
                    </div>

                    <div>
                      <div style={{ fontSize: 11, fontWeight: 700, color: "#fbbf24", marginBottom: 4 }}>
                        ⏱️ First 10-Second Retention Hook (Tease #1 pick)
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          color: "#cbd5e1",
                          fontStyle: "italic",
                          background: "rgba(251, 191, 36, 0.05)",
                          borderLeft: "3px solid #fbbf24",
                          padding: "6px 10px",
                          borderRadius: "0 4px 4px 0",
                        }}
                      >
                        "{currentCountdown.hook_script}"
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {/* ─── Ranked Entries Cards ─── */}
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <h3 style={{ margin: 0, fontSize: 16, color: "#f8fafc" }}>
                    🏆 Defensible Countdown Rankings ({currentCountdown.entries.length} to #1)
                  </h3>
                  <span style={{ fontSize: 11, color: "var(--muted)" }}>
                    Weighted: Critic/User Ratings (35%) · Cultural Impact (25%) · Current Buzz (20%) · Sentiment (20%)
                  </span>
                </div>

                {currentCountdown.entries.map((entry) => {
                  const isNumberOne = entry.rank === 1;
                  const isControversial = (currentCountdown.entries.length <= 5 && entry.rank === 3) ||
                                          (currentCountdown.entries.length > 5 && entry.rank === 4);

                  return (
                    <div
                      key={entry.rank}
                      style={{
                        ...cardStyle,
                        background: isNumberOne
                          ? "linear-gradient(135deg, rgba(202, 138, 4, 0.15) 0%, rgba(30, 41, 59, 0.9) 100%)"
                          : "var(--panel)",
                        border: isNumberOne
                          ? "1px solid rgba(234, 179, 8, 0.6)"
                          : isControversial
                          ? "1px solid rgba(236, 72, 153, 0.5)"
                          : "1px solid var(--border)",
                        display: "flex",
                        gap: 16,
                        flexWrap: "wrap",
                      }}
                    >
                      {/* Left: Poster + Rank Badge */}
                      <div style={{ position: "relative", width: 90, flexShrink: 0 }}>
                        {entry.poster ? (
                          <img
                            src={entry.poster}
                            alt={entry.title}
                            style={{
                              width: 90,
                              height: 135,
                              borderRadius: 6,
                              objectFit: "cover",
                              border: "1px solid rgba(255, 255, 255, 0.1)",
                            }}
                          />
                        ) : (
                          <div
                            style={{
                              width: 90,
                              height: 135,
                              borderRadius: 6,
                              background: "#1e293b",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                              color: "var(--muted)",
                              fontSize: 10,
                            }}
                          >
                            No Poster
                          </div>
                        )}

                        {/* Rank Badge */}
                        <div
                          style={{
                            position: "absolute",
                            top: -6,
                            left: -6,
                            width: 32,
                            height: 32,
                            borderRadius: "50%",
                            background: isNumberOne ? "#eab308" : isControversial ? "#db2777" : "var(--accent)",
                            color: isNumberOne ? "#000" : "#fff",
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                            fontWeight: 800,
                            fontSize: 14,
                            boxShadow: "0 2px 6px rgba(0,0,0,0.5)",
                          }}
                        >
                          #{entry.rank}
                        </div>
                      </div>

                      {/* Middle: Entry Info & Scoring */}
                      <div style={{ flex: 1, minWidth: 260, display: "flex", flexDirection: "column", gap: 6 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                          <h4 style={{ margin: 0, fontSize: 16, color: "#fff" }}>
                            {entry.title}
                          </h4>
                          <span style={{ color: "var(--muted)", fontSize: 13 }}>({entry.year})</span>

                          {isNumberOne && (
                            <span style={badgeStyle("#854d0e", "#fef08a")}>
                              👑 #1 DEFENDER PICK
                            </span>
                          )}

                          {isControversial && (
                            <span style={badgeStyle("#831843", "#fbcfe8")}>
                              🔥 SURPRISE / DEBATE PICK
                            </span>
                          )}

                          <span style={badgeStyle("#065f46", "#6ee7b7")}>
                            Score: {entry.score}/100
                          </span>
                        </div>

                        {/* Key Stat & JustWatch Streaming */}
                        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                          <div
                            style={{
                              fontSize: 11,
                              color: "#e2e8f0",
                              background: "rgba(255, 255, 255, 0.06)",
                              padding: "3px 8px",
                              borderRadius: 4,
                            }}
                          >
                            📊 <strong>Key Stat:</strong> {entry.key_stat}
                          </div>

                          {entry.where_to_watch && (
                            <div
                              style={{
                                fontSize: 11,
                                color: "#93c5fd",
                                background: "rgba(59, 130, 246, 0.1)",
                                border: "1px solid rgba(59, 130, 246, 0.2)",
                                padding: "3px 8px",
                                borderRadius: 4,
                                display: "flex",
                                alignItems: "center",
                                gap: 4,
                              }}
                            >
                              <span>📺 JustWatch (US):</span>
                              <strong>{entry.where_to_watch}</strong>
                              {entry.justwatch_url && (
                                <a
                                  href={entry.justwatch_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  style={{ color: "#60a5fa", marginLeft: 4, textDecoration: "none" }}
                                >
                                  ↗
                                </a>
                              )}
                            </div>
                          )}
                        </div>

                        {/* Why It Ranks */}
                        <div style={{ fontSize: 12, color: "#cbd5e1", lineHeight: 1.4 }}>
                          <strong style={{ color: "#38bdf8" }}>Why It Ranks:</strong> {entry.why_it_ranks}
                        </div>

                        {/* Fun Fact */}
                        <div style={{ fontSize: 12, color: "#94a3b8", lineHeight: 1.4 }}>
                          <strong style={{ color: "#fbbf24" }}>Fun Fact:</strong> {entry.fun_fact}
                        </div>

                        {/* Footing Status & Sources */}
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginTop: 4 }}>
                          <div style={{ display: "flex", gap: 4 }}>
                            {entry.sources.map((s, idx) => (
                              <span key={idx} style={{ fontSize: 10, color: "var(--muted)", background: "rgba(0,0,0,0.3)", padding: "1px 6px", borderRadius: 4 }}>
                                {s}
                              </span>
                            ))}
                          </div>

                          {/* Local Footage check */}
                          {entry.has_footage ? (
                            <span style={badgeStyle("#064e3b", "#34d399")}>
                              ✓ Local 1080p Breakdown Footage Ready ({entry.linked_project?.seconds}s)
                            </span>
                          ) : (
                            <span style={badgeStyle("#1e293b", "#94a3b8")}>
                              No local breakdown rendered yet
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* ─── Honorable Mentions & Retention Ending ─── */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
                  gap: 12,
                }}
              >
                {/* Honorable Mentions */}
                <div style={{ ...cardStyle, background: "rgba(15, 23, 42, 0.6)" }}>
                  <h4 style={{ margin: "0 0 8px", fontSize: 14, color: "#fbbf24" }}>
                    ⭐ Honorable Mentions (Cut Right Before #1)
                  </h4>
                  <p style={{ margin: "0 0 10px", fontSize: 11, color: "var(--muted)" }}>
                    Addressed in the script just prior to unveiling the #1 pick to satisfy loyal viewers:
                  </p>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "#cbd5e1", lineHeight: 1.5 }}>
                    {currentCountdown.honorable_mentions.map((h, i) => (
                      <li key={i} style={{ marginBottom: 4 }}>{h}</li>
                    ))}
                  </ul>
                </div>

                {/* Closing Debate Question */}
                <div style={{ ...cardStyle, background: "rgba(15, 23, 42, 0.6)" }}>
                  <h4 style={{ margin: "0 0 8px", fontSize: 14, color: "#f472b6" }}>
                    💬 Closing Debate Question (Comment Section Bait)
                  </h4>
                  <p style={{ margin: "0 0 10px", fontSize: 11, color: "var(--muted)" }}>
                    Video outro question specifically formulated to ignite heated debate in YouTube comments:
                  </p>
                  <div
                    style={{
                      fontSize: 13,
                      color: "#fff",
                      fontStyle: "italic",
                      background: "rgba(244, 114, 182, 0.08)",
                      borderLeft: "3px solid #f472b6",
                      padding: "8px 12px",
                      borderRadius: "0 6px 6px 0",
                    }}
                  >
                    "{currentCountdown.closing_question}"
                  </div>
                </div>
              </div>

              {/* ─── Fact Verification & Unverified Checker ─── */}
              <div style={{ ...cardStyle, background: "rgba(15, 23, 42, 0.4)", fontSize: 11 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                  <span style={{ color: currentCountdown.unverified_flags?.length ? "#fbbf24" : "#34d399" }}>
                    {currentCountdown.unverified_flags?.length ? "⚠️" : "✓"}
                  </span>
                  <strong style={{ color: "#f8fafc" }}>
                    Fact & Verification Status (Step 4 Audit)
                  </strong>
                </div>
                {currentCountdown.unverified_flags && currentCountdown.unverified_flags.length > 0 ? (
                  <div style={{ color: "#fbbf24" }}>
                    Items flagged for manual verification:
                    <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>
                      {currentCountdown.unverified_flags.map((f, i) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <div style={{ color: "#34d399" }}>
                    All numbers, release years, Academy Awards, box office stats, and JustWatch US streaming platforms verified against official database catalogs.
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* ────────────────────────────────────────────────────────── */}
      {/* VIEW 3: SAVED COUNTDOWNS LIST                              */}
      {/* ────────────────────────────────────────────────────────── */}
      {subView === "saved" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0, fontSize: 15, color: "#f8fafc" }}>
              📁 Saved Researched Countdowns ({savedList.length})
            </h3>
            <button
              onClick={loadSaved}
              style={{
                background: "var(--panel)",
                border: "1px solid var(--border)",
                color: "var(--text)",
                borderRadius: 6,
                padding: "4px 10px",
                fontSize: 11,
                cursor: "pointer",
              }}
            >
              🔄 Refresh List
            </button>
          </div>

          {loadingSaved ? (
            <div style={{ ...cardStyle, textAlign: "center", padding: 30, color: "var(--muted)" }}>
              Loading saved countdowns...
            </div>
          ) : savedList.length === 0 ? (
            <div style={{ ...cardStyle, textAlign: "center", padding: 30, color: "var(--muted)" }}>
              No saved countdowns yet. Research a countdown and click "Save Countdown"!
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {savedList.map((item, idx) => (
                <div
                  key={item.id || idx}
                  style={{
                    ...cardStyle,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: 12,
                  }}
                >
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 250 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={badgeStyle("#7f1d1d", "#fca5a5")}>
                        {item.format.toUpperCase()}
                      </span>
                      <span style={badgeStyle("#064e3b", "#34d399")}>
                        {item.entries?.length || 0} Entries
                      </span>
                      {item.generated_at && (
                        <span style={{ fontSize: 11, color: "var(--muted)" }}>
                          {new Date(item.generated_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>

                    <h4 style={{ margin: "2px 0 0", fontSize: 15, color: "#f8fafc" }}>
                      {item.topic}
                    </h4>

                    <span style={{ fontSize: 12, color: "var(--muted)" }}>
                      {item.why_now}
                    </span>
                  </div>

                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      onClick={() => {
                        setCurrentCountdown(item);
                        setSubView("researched");
                      }}
                      style={{
                        padding: "6px 12px",
                        borderRadius: 6,
                        border: "1px solid var(--accent)",
                        background: "rgba(16, 185, 129, 0.1)",
                        color: "var(--accent)",
                        fontWeight: 600,
                        fontSize: 12,
                        cursor: "pointer",
                      }}
                    >
                      Open & View
                    </button>

                    <button
                      onClick={() => handleDeleteSaved(item.id)}
                      style={{
                        padding: "6px 10px",
                        borderRadius: 6,
                        border: "1px solid rgba(239, 68, 68, 0.3)",
                        background: "rgba(239, 68, 68, 0.1)",
                        color: "#ef4444",
                        fontSize: 12,
                        cursor: "pointer",
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ─── Video Stitch Modal (Compilations Assembly) ─── */}
      {showStitchModal && (
        <CountdownModal
          initialProjectIds={projects.filter((p) => p.has?.render).map((p) => p.id)}
          onClose={() => setShowStitchModal(false)}
          onSuccess={() => {
            setShowStitchModal(false);
            if (onReloadProjects) onReloadProjects();
          }}
        />
      )}
    </div>
  );
}
