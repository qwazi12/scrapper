"use client";

import React, { useEffect, useState } from "react";
import { api, CostSummary } from "../lib/api";
import { UndoButton } from "./UndoButton";
import { notify } from "../lib/dialogs";

// Settings → Spending. Every paid call is recorded server-side (backend/app/costs.py)
// with its estimated cost; this card shows the month, the budget, the free tiers
// and what spent the money. Prices are the official list prices (editable on the server).

const usd = (n: number) => (n >= 100 ? `$${n.toFixed(0)}` : n >= 1 ? `$${n.toFixed(2)}` : `$${n.toFixed(n ? 4 : 2)}`);
const num = (n: number) => n.toLocaleString("en-US");

const SERVICE_NAMES: Record<string, string> = {
  gemini: "Gemini AI (captions, scripts, research)",
  "gemini-search": "Google Search grounding",
  tts: "Text-to-speech (Chirp 3 HD)",
  "upload-post": "Upload-Post uploads",
  tmdb: "TMDB (free)",
  imdb: "IMDb trailers (free)",
  drive: "Google Drive (free)",
};

const OP_NAMES: Record<string, string> = {
  "queue:ai": "✨ AI on one clip",
  "queue:bulk_ai": "✨ Bulk AI",
  "post:auto_seo": "Auto-SEO before posting",
  post: "Posting",
  other: "Other",
};
const opName = (o: string) => OP_NAMES[o] || (o.startsWith("studio:") ? `LongForm Studio — ${o.slice(7)}` : o);

/** A labelled part-of-whole meter. Over the limit turns red with a ⚠ label (never colour alone). */
function Meter({ label, used, limit, fmt, note }: { label: string; used: number; limit: number; fmt: (n: number) => string; note?: string }) {
  const pct = limit > 0 ? (used / limit) * 100 : 0;
  const over = limit > 0 && used >= limit;
  return (
    <div title={`${label}: ${fmt(used)} of ${fmt(limit)} (${pct.toFixed(1)}%)`}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 4, gap: 8 }}>
        <span>{label}</span>
        <span style={{ color: over ? "var(--red)" : "var(--muted)", whiteSpace: "nowrap" }}>
          {over && "⚠ "}{fmt(used)} / {fmt(limit)}{note ? ` ${note}` : ""}
        </span>
      </div>
      <div style={{ height: 8, background: "var(--row)", border: "1px solid var(--border)", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ width: `${Math.min(100, pct)}%`, minWidth: used > 0 ? 3 : 0, height: "100%",
                      background: over ? "var(--red)" : "var(--accent)", borderRadius: 4 }} />
      </div>
    </div>
  );
}

const PLANS: Record<string, string> = { Free: "$0 · 10 uploads/mo", Basic: "$24/mo · unlimited", Professional: "$50/mo",
  Advanced: "$147/mo", Business: "$438/mo" };

export function SpendingCard({ card, h2 }: { card: React.CSSProperties; h2: React.CSSProperties }) {
  const [data, setData] = useState<CostSummary | null>(null);
  const [err, setErr] = useState("");
  const [editing, setEditing] = useState(false);
  const [budget, setBudget] = useState("");
  const [fixed, setFixed] = useState<{ name: string; usd: string }[]>([]);
  const [saving, setSaving] = useState(false);

  const load = () => api.costs().then((d) => { setData(d); setErr(""); }).catch((e) => setErr(String(e.message || e)));
  useEffect(() => {
    load();
    const t = setInterval(() => !document.hidden && load(), 30000);
    return () => clearInterval(t);
  }, []);

  if (err) return <div style={card}><h2 style={h2}>💰 Spending</h2><div style={{ fontSize: 12, color: "var(--red)" }}>{err}</div></div>;
  if (!data) return <div style={card}><h2 style={h2}>💰 Spending</h2><div style={{ fontSize: 12, color: "var(--muted)" }}>Loading…</div></div>;

  const cfg = data.settings;
  const ft = data.free_tiers;
  const services = Object.entries(data.by_service).sort((a, b) => b[1].cost - a[1].cost);

  async function save(patch: Parameters<typeof api.setCostSettings>[0], what: string) {
    setSaving(true);
    try {
      setData(await api.setCostSettings(patch));
    } catch (e: any) {
      notify(`Could not save ${what}: ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  function openEdit() {
    setBudget(cfg.budget_usd ? String(cfg.budget_usd) : "");
    setFixed(cfg.fixed_costs.map((f) => ({ name: f.name, usd: String(f.usd) })));
    setEditing(true);
  }

  async function saveEdit() {
    const b = budget.trim() === "" ? 0 : Number(budget);
    if (!Number.isFinite(b) || b < 0) return notify("Budget must be a number, 0 or more (0 = no budget).");
    const fc = fixed.filter((f) => f.name.trim() || f.usd.trim()).map((f) => ({ name: f.name.trim(), usd: Number(f.usd) }));
    if (fc.some((f) => !f.name || !Number.isFinite(f.usd) || f.usd < 0)) return notify("Each fixed cost needs a name and an amount.");
    await save({ budget_usd: b, fixed_costs: fc }, "budget");
    setEditing(false);
  }

  const monthName = new Date(`${data.month}-01T12:00:00Z`).toLocaleString("en-US", { month: "long", year: "numeric" });

  return (
    <div style={card}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
        <h2 style={{ ...h2, margin: 0 }}>💰 Spending — {monthName}</h2>
        <UndoButton scope="settings" version={JSON.stringify(cfg)} compact onUndone={load} />
      </div>

      {/* Headline */}
      <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline", marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 28, fontWeight: 800 }}>{usd(data.total_usd)}</div>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>this month so far</div>
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>
          <div>Pay-as-you-go: <b style={{ color: "var(--text)" }}>{usd(data.variable_usd)}</b></div>
          <div>Fixed (subscriptions): <b style={{ color: "var(--text)" }}>{usd(data.fixed_usd)}</b></div>
        </div>
      </div>

      <DailyCap data={data} saving={saving} onSave={(v) => save({ daily_usd: v }, "daily cap")} onReload={load} />
      {!!data.breakdowns?.length && <BreakdownCosts rows={data.breakdowns} />}

      {data.budget_usd > 0 ? (
        <div style={{ marginBottom: 14 }}>
          <Meter label={`Monthly budget${data.hard_stop ? " (hard stop on)" : ""}`} used={data.total_usd} limit={data.budget_usd} fmt={usd} />
          {data.hard_stop && data.total_usd >= data.budget_usd && (
            <div style={{ fontSize: 11, color: "var(--red)", marginTop: 4 }}>
              ⚠ Budget reached — AI, voice-over and research calls are refused until you raise it or turn off the hard stop.
            </div>
          )}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--yellow)", marginBottom: 14 }}>No monthly budget set — spending is tracked but never limited.</div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 14 }}>
        <button style={{ fontSize: 11, padding: "4px 10px" }} onClick={openEdit} disabled={saving}>✏️ Budget &amp; fixed costs</button>
        <label style={{ fontSize: 11, display: "flex", gap: 6, alignItems: "center" }}
               title="When on, paid calls (Gemini, TTS) are refused once the budget is reached">
          <input type="checkbox" checked={cfg.hard_stop} disabled={saving || !cfg.budget_usd}
                 onChange={(e) => save({ hard_stop: e.target.checked }, "hard stop")} />
          Stop paid calls at the budget
        </label>
        <label style={{ fontSize: 11, display: "flex", gap: 6, alignItems: "center" }}>
          Upload-Post plan
          <select style={{ width: "auto", fontSize: 11, padding: "2px 6px" }} value={cfg.upload_post_plan} disabled={saving}
                  onChange={(e) => save({ upload_post_plan: e.target.value }, "plan")}>
            {Object.entries(PLANS).map(([k, v]) => <option key={k} value={k}>{k} — {v}</option>)}
          </select>
        </label>
      </div>

      {editing && (
        <div style={{ padding: 12, background: "var(--row)", border: "1px solid var(--border)", borderRadius: 8, marginBottom: 14, fontSize: 11 }}>
          <label>
            <div style={{ color: "var(--muted)", marginBottom: 3 }}>Monthly budget in USD (empty or 0 = none) — includes fixed costs</div>
            <input style={{ width: 120, fontSize: 12 }} inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="e.g. 50" />
          </label>
          <div style={{ color: "var(--muted)", margin: "10px 0 4px" }}>Other fixed monthly costs (hosting, domains…) — Upload-Post is added from the plan above</div>
          {fixed.map((f, i) => (
            <div key={i} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
              <input style={{ width: 160, fontSize: 12 }} value={f.name} placeholder="Name (e.g. Railway)"
                     onChange={(e) => setFixed(fixed.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
              <input style={{ width: 80, fontSize: 12 }} inputMode="decimal" value={f.usd} placeholder="$/month"
                     onChange={(e) => setFixed(fixed.map((x, j) => (j === i ? { ...x, usd: e.target.value } : x)))} />
              <button style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setFixed(fixed.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
          <button style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setFixed([...fixed, { name: "", usd: "" }])}>+ Add fixed cost</button>
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button className="primary" style={{ fontSize: 11 }} disabled={saving} onClick={saveEdit}>{saving ? "Saving…" : "Save"}</button>
            <button style={{ fontSize: 11 }} disabled={saving} onClick={() => setEditing(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Free tiers */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12, marginBottom: 14 }}>
        <Meter label="Voice-over free characters" used={ft.tts_chars.used} limit={ft.tts_chars.free} fmt={num} />
        <Meter label="Search grounding free requests" used={ft.search_requests.used} limit={ft.search_requests.free} fmt={num} />
        {ft.uploads.limit ? (
          <Meter label={`Upload-Post ${ft.uploads.plan} uploads`} used={ft.uploads.used} limit={ft.uploads.limit} fmt={num} />
        ) : (
          <div style={{ fontSize: 11 }}>
            Upload-Post {ft.uploads.plan}: <b>{num(ft.uploads.used)}</b> uploads this month
            <div style={{ color: "var(--muted)" }}>unlimited on this plan</div>
          </div>
        )}
      </div>

      {/* Breakdowns */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16 }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4, fontWeight: 700 }}>BY SERVICE</div>
          {services.length === 0 ? (
            <div style={{ fontSize: 11, color: "var(--muted)" }}>Nothing used yet this month.</div>
          ) : (
            <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
              <tbody>
                {services.map(([k, v]) => (
                  <tr key={k} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "4px 0" }}>{SERVICE_NAMES[k] || k}</td>
                    <td style={{ color: "var(--muted)", textAlign: "right", padding: "4px 6px", whiteSpace: "nowrap" }}>
                      {v.chars ? `${num(v.chars)} chars` : v.input_tokens ? `${num(v.input_tokens + v.output_tokens)} tokens` : `${num(v.requests)} calls`}
                    </td>
                    <td style={{ textAlign: "right", fontWeight: 700, whiteSpace: "nowrap" }}>{usd(v.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4, fontWeight: 700 }}>WHAT IT WAS FOR</div>
          {data.by_operation.length === 0 ? (
            <div style={{ fontSize: 11, color: "var(--muted)" }}>Nothing yet.</div>
          ) : (
            <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
              <tbody>
                {data.by_operation.map((o) => (
                  <tr key={o.operation} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "4px 0" }}>{opName(o.operation)}</td>
                    <td style={{ color: "var(--muted)", textAlign: "right", padding: "4px 6px" }}>{num(o.requests)} calls</td>
                    <td style={{ textAlign: "right", fontWeight: 700 }}>{usd(o.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 12 }}>
        Estimates from list prices: Gemini Flash ${cfg.gemini["gemini-3.8-flash"]?.in}/${cfg.gemini["gemini-3.8-flash"]?.out} per 1M
        tokens in/out (thinking counts as out; doubles in 2027) · search grounding {num(cfg.grounding_free_per_month)} free/mo then
        ${cfg.grounding_usd_per_1000}/1,000 · voice {num(cfg.tts_free_chars_per_month)} chars free/mo then
        ${cfg.tts_usd_per_million_chars}/1M · TMDB, IMDb, Drive free. Check your Google Cloud billing for the exact bill.
      </div>
    </div>
  );
}

function DailyCap({ data, saving, onSave, onReload }: { data: CostSummary; saving: boolean; onSave: (v: number) => void; onReload: () => void }) {
  const t = data.today;
  const base = t.base_cap_usd ?? t.cap_usd;          // the saved cap, without today's extra
  const [val, setVal] = useState(String(base || ""));
  useEffect(() => setVal(String(base || "")), [base]);
  const resets = new Date(t.resets_at).toLocaleString("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" });
  return (
    <div style={{ marginBottom: 14, padding: 10, background: "var(--row)", borderRadius: 8, fontSize: 11 }}>
      {t.cap_usd > 0 ? (
        <Meter label={`Today (daily cap, ${t.timezone})`} used={t.spent_usd} limit={t.cap_usd} fmt={usd} />
      ) : (
        <div style={{ color: "var(--yellow)" }}>No daily cap — today so far {usd(t.spent_usd)}.</div>
      )}
      {t.reached && (
        <div style={{ color: "var(--red)", marginTop: 4 }}>
          ⚠ Daily cap reached — every AI, voice-over and research call on the site is paused until {resets}.
          Paused work resumes first, from where it stopped.
        </div>
      )}
      {t.paused_jobs.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <b>Paused by the cap ({t.paused_jobs.length}) — resume first, oldest first:</b>
          {t.paused_jobs.map((j) => (
            <div key={j.id} style={{ color: "var(--muted)" }}>
              ⏸ {j.label}{j.step ? ` — at step “${j.step}”` : ""}{j.since ? ` · since ${new Date(j.since).toLocaleString()}` : ""}
            </div>
          ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
        <span style={{ color: "var(--muted)" }}>Daily cap $</span>
        <input style={{ width: 70, fontSize: 11 }} inputMode="decimal" value={val} onChange={(e) => setVal(e.target.value)} placeholder="0 = off" />
        <button style={{ fontSize: 11, padding: "3px 10px" }} disabled={saving || Number(val || 0) === base}
                onClick={() => {
                  const v = val.trim() === "" ? 0 : Number(val);
                  if (!Number.isFinite(v) || v < 0) return notify("Daily cap must be a number, 0 or more (0 = off).");
                  onSave(v);
                }}>Save</button>
        <button style={{ fontSize: 11, padding: "3px 10px" }} disabled={saving || !t.cap_usd}
                title="Raise today's cap by $2; back to normal at midnight"
                onClick={async () => {
                  try { await api.costsTodayExtra(2); onReload(); }
                  catch (e: any) { notify(`Could not raise today's cap: ${e.message || e}`); }
                }}>+$2 today only</button>
        <span style={{ color: "var(--muted)" }}>
          {t.extra_today_usd ? `Today +$${t.extra_today_usd.toFixed(2)} extra. ` : ""}Resets {resets}. Counts pay-as-you-go spend only (not subscriptions).
        </span>
      </div>
    </div>
  );
}

function BreakdownCosts({ rows }: { rows: { id: number; title: string; auto: boolean; cost: number }[] }) {
  const max = Math.max(...rows.map((r) => r.cost), 0.0001);
  const avg = rows.filter((r) => r.cost > 0).reduce((a, r, _, arr) => a + r.cost / arr.length, 0);
  return (
    <div style={{ marginBottom: 14, fontSize: 11 }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>Cost per breakdown this month <span style={{ color: "var(--muted)", fontWeight: 400 }}>(avg {usd(avg)})</span></div>
      {rows.map((r) => (
        <div key={r.id} style={{ display: "grid", gridTemplateColumns: "minmax(90px, 160px) 1fr 60px", gap: 6, alignItems: "center", marginBottom: 3 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.title}>{r.auto ? "🤖 " : ""}{r.title}</span>
          <div style={{ background: "var(--row)", borderRadius: 3, height: 10 }}>
            <div style={{ width: `${(r.cost / max) * 100}%`, background: "var(--accent)", height: 10, borderRadius: 3 }} />
          </div>
          <span style={{ textAlign: "right" }}>{usd(r.cost)}</span>
        </div>
      ))}
    </div>
  );
}
