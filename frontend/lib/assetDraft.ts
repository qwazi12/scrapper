export type AssetCueDraft = { start: string; end: string; text: string };
export const emptyAssetForm = { kind: "video", license: "owner_created", attestation: "", description: "", tags: "", people: "", cues: "[]", fileHint: "" };

export function assetCueDraft(raw: string): AssetCueDraft[] | null {
  try {
    if (raw.length > 500_000) return null;
    const rows: unknown = JSON.parse(raw);
    if (!Array.isArray(rows) || rows.length > 500 || !rows.every(row => row && typeof row === "object" &&
      Object.keys(row).length === 3 && ["start", "end", "text"].every(key => typeof row[key] === "string") && row.text.length <= 500)) return null;
    return rows as AssetCueDraft[];
  } catch { return null; }
}
