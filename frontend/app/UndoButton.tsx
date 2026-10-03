"use client";

import React, { useEffect, useState } from "react";
import { api, UndoStep } from "../lib/api";
import { notify } from "../lib/dialogs";

// Names the step it will undo ("Undo: Mass edit on 12 videos"), the way
// manhwa's storyboard undo does. `version` changes whenever the parent made a
// change, so the label refreshes.
export function UndoButton({
  scope,
  version,
  onUndone,
  compact,
}: {
  scope: string;
  version?: unknown;
  onUndone?: (label: string) => void;
  compact?: boolean;
}) {
  const [top, setTop] = useState<UndoStep | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setTop((await api.undoStack(scope)).stack[0] || null);
    } catch {
      setTop(null);
    }
  }

  useEffect(() => {
    load();
  }, [scope, version]);

  async function undo() {
    if (!top) return;
    setBusy(true);
    try {
      const r = await api.undo(scope);
      setTop(r.stack[0] || null);
      onUndone?.(r.undone);
    } catch (e: any) {
      notify(`Undo failed: ${e.message || e}`);
      load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      onClick={undo}
      disabled={!top || busy}
      title={top ? `Undo: ${top.label}` : "Nothing to undo"}
      style={{
        fontSize: 11,
        padding: "5px 10px",
        maxWidth: compact ? 160 : 280,
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        opacity: top ? 1 : 0.5,
      }}
    >
      ↶ {busy ? "Undoing…" : top ? `Undo: ${top.label}` : "Undo"}
    </button>
  );
}
