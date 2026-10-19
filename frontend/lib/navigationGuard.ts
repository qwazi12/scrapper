"use client";

import { askConfirm } from "./dialogs";

const blockers = new Set<() => boolean>();
let acceptedHash: string | undefined;
let decision: Promise<boolean> | null = null;

export function registerNavigationBlocker(blocked: () => boolean) {
  blockers.add(blocked);
  return () => { blockers.delete(blocked); };
}

/** Internal tabs can discard the same editors without changing the URL hash. */
export async function allowLocalNavigation(): Promise<boolean> {
  if (![...blockers].some(blocked => blocked())) return true;
  if (!decision) decision = askConfirm("Browser draft storage is unavailable. Leaving this editor will lose unsaved changes. Leave anyway?")
    .finally(() => { decision = null; });
  return await decision;
}

/** Hash events and navigation controls share one decision and one saved route. */
export async function allowNavigation(target: string): Promise<boolean> {
  if (target === acceptedHash) return true;
  if (![...blockers].some(blocked => blocked())) { acceptedHash = target; return true; }
  const allowed = await allowLocalNavigation();
  if (allowed) acceptedHash = target;
  else if (acceptedHash !== undefined && window.location.hash !== acceptedHash) window.history.replaceState(null, "", acceptedHash || "#");
  return allowed;
}
