// Where you are, kept in the address (#tab/sub/id), so a refresh lands on the same
// page (owner, 2026-10-13: "when I refresh the page it takes me to the starting page").
export function readHash(): string[] {
  if (typeof window === "undefined") return [];
  return decodeURIComponent(window.location.hash.replace(/^#/, "")).split("/").filter(Boolean);
}

/** Replace the address hash without adding a history step. */
export function writeHash(parts: (string | number | null | undefined)[]): void {
  if (typeof window === "undefined") return;
  const h = "#" + parts.filter((p) => p !== null && p !== undefined && p !== "").map(String).join("/");
  if (window.location.hash !== h) window.history.replaceState(null, "", h);
}
