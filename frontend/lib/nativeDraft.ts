import type { NativeBeat } from "./api";

const formats = new Set(["short", "reel", "carousel", "image", "community", "poll"]);
const blankBeats: NativeBeat[] = ["hook", "body", "payoff"].map(role => ({ role: role as NativeBeat["role"], text: "", source_refs: [1], asset_id: null }));
export const emptyNativeForm = { oid: "", researchFingerprint: "", title: "", beats: JSON.stringify(blankBeats), selected: JSON.stringify(["short", "carousel"]),
  shortCaption: "", carouselCaption: "", question: "", reel_caption: "", image_text: "", image_caption: "", community_text: "", poll_question: "", poll_options: "" };

/** Browser recovery is untrusted data. Preserve malformed words without rendering or submitting their structure. */
export function nativeBeats(raw: string): NativeBeat[] | null {
  try {
    if (raw.length > 100_000) return null;
    const rows: unknown = JSON.parse(raw);
    if (!Array.isArray(rows) || rows.length < 3 || rows.length > 8) return null;
    const allowed = new Set(["role", "text", "source_refs", "asset_id", "clip_start", "clip_end", "clip_end_policy", "background_audio_id", "audio_start", "audio_end", "audio_end_policy", "audio_gain_db"]);
    if (!rows.every((row, index) => row && typeof row === "object" && Object.keys(row).every(key => allowed.has(key)) &&
      row.role === (index === 0 ? "hook" : index === rows.length - 1 ? "payoff" : "body") && typeof row.text === "string" && row.text.length <= 140 &&
      Array.isArray(row.source_refs) && row.source_refs.length >= 1 && row.source_refs.length <= 10 && row.source_refs.every((ref: unknown) => Number.isSafeInteger(ref) && Number(ref) > 0) &&
      (row.asset_id == null || Number.isSafeInteger(row.asset_id) && row.asset_id > 0) &&
      (row.background_audio_id == null || Number.isSafeInteger(row.background_audio_id) && row.background_audio_id > 0) &&
      ["clip_start", "clip_end", "audio_start", "audio_end"].every(key => row[key] == null || typeof row[key] === "number" && Number.isFinite(row[key]) && row[key] >= 0 && row[key] <= 300) &&
      (row.clip_end_policy == null || ["hold", "loop"].includes(row.clip_end_policy)) &&
      (row.audio_end_policy == null || ["silence", "loop"].includes(row.audio_end_policy)) &&
      (row.audio_gain_db == null || typeof row.audio_gain_db === "number" && Number.isFinite(row.audio_gain_db) && row.audio_gain_db >= -36 && row.audio_gain_db <= -12))) return null;
    return rows as NativeBeat[];
  } catch { return null; }
}

export function nativeFormats(raw: string): string[] | null {
  try {
    if (raw.length > 1000) return null;
    const rows: unknown = JSON.parse(raw);
    return Array.isArray(rows) && rows.length <= 6 && new Set(rows).size === rows.length && rows.every(row => typeof row === "string" && formats.has(row)) ? rows : null;
  } catch { return null; }
}
