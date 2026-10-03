"use client";

import React, { useEffect, useState } from "react";

// ■ Stop with an in-page confirm: first tap arms it ("Tap again to stop"),
// a second tap within 4 s stops. Never uses window.confirm(): browsers can
// silently block those dialogs (Chrome's "prevent additional dialogs", some
// in-app browsers), which made Stop do nothing at all (2026-10-02).
export function StopButton({
  onStop,
  what,
  style,
  immediate = false,
}: {
  onStop: () => Promise<unknown> | unknown;
  what?: string; // shown on hover, e.g. "Finished steps are kept"
  style?: React.CSSProperties;
  immediate?: boolean;
}) {
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 4000);
    return () => clearTimeout(t);
  }, [armed]);

  async function click() {
    setErr("");
    if (!immediate && !armed) {
      setArmed(true);
      return;
    }
    setArmed(false);
    setBusy(true);
    try {
      await onStop();
    } catch (e: any) {
      setErr(`Couldn't stop: ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      <button
        className="danger"
        disabled={busy}
        title={what}
        onClick={click}
        style={{ fontWeight: 700, ...style, ...(armed ? { background: "#dc2626", color: "#fff" } : {}) }}
      >
        {busy ? "Stopping…" : armed ? "■ Tap again to stop" : "■ Stop"}
      </button>
      {err && <span style={{ fontSize: 11, color: "var(--red)" }}>{err}</span>}
    </span>
  );
}
