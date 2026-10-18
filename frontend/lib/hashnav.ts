// Where you are, kept in the address (#tab/sub/id), so a refresh lands on the same
// page (owner, 2026-10-13: "when I refresh the page it takes me to the starting page").
import { useCallback, useEffect, useState } from "react";
import { allowNavigation } from "./navigationGuard";
export function readHash(): string[] {
  if (typeof window === "undefined") return [];
  try { return decodeURIComponent(window.location.hash.replace(/^#/, "")).split("/").filter(Boolean); }
  catch { return []; }
}

/** Replace the address hash without adding a history step. */
export function writeHash(parts: (string | number | null | undefined)[]): void {
  if (typeof window === "undefined") return;
  const h = "#" + parts.filter((p) => p !== null && p !== undefined && p !== "").map(String).join("/");
  if (window.location.hash !== h) window.history.replaceState(null, "", h);
}

/** Project navigation is a history step; external fragment/back/forward updates
 * the mounted studio too. No state-to-URL effect can overwrite a history event. */
export function useProjectHash(tab: "longform" | "countdowns"): [number | null, (id: number | null) => void] {
  const [id, setId] = useState<number | null>(null);
  useEffect(() => {
    const sync = async () => {
      if (!await allowNavigation(window.location.hash)) return;
      const [current, value] = readHash();
      if (current === tab) setId(/^\d+$/.test(value || "") && Number(value) > 0 ? Number(value) : null);
    };
    sync(); window.addEventListener("hashchange", sync); window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, [tab]);
  const navigate = useCallback(async (next: number | null) => {
    const hash = `#${tab}${next === null ? "" : `/${next}`}`;
    if (!await allowNavigation(hash)) return;
    setId(next);
    if (window.location.hash !== hash) {
      window.history.pushState(null, "", hash);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
  }, [tab]);
  return [id, navigate];
}
