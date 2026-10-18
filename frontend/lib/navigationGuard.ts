"use client";

import { askConfirm } from "./dialogs";

const blockers = new Set<() => boolean>();
let acceptedHash: string | undefined;
let decision: Promise<boolean> | null = null;

export function registerNavigationBlocker(blocked: () => boolean) {
  blockers.add(blocked);
  return () => { blockers.delete(blocked); };
}

/** Hash events and navigation controls share one decision and one saved route. */
export async function allowNavigation(target: string): Promise<boolean> {
  if (target === acceptedHash) return true;
  if (![...blockers].some(blocked => blocked())) { acceptedHash = target; return true; }
  if (!decision) decision = askConfirm("Browser draft storage is unavailable. Leaving this editor will lose unsaved changes. Leave anyway?")
    .finally(() => { decision = null; });
  const allowed = await decision;
  if (allowed) acceptedHash = target;
  else if (acceptedHash !== undefined && window.location.hash !== acceptedHash) window.history.replaceState(null, "", acceptedHash || "#");
  return allowed;
}
