"use client";

import React, { useEffect, useRef, useState } from "react";
import { answerConfirm, dismissToast, snapshot, subscribe } from "../lib/dialogs";

/** Renders askConfirm() boxes and notify() messages (see lib/dialogs.ts). */
export function DialogHost() {
  const [state, setState] = useState(snapshot());
  const okRef = useRef<HTMLButtonElement>(null);
  useEffect(() => subscribe(() => setState(snapshot())), []);
  useEffect(() => {
    if (state.confirm) okRef.current?.focus();
  }, [state.confirm?.id]);
  useEffect(() => {
    if (!state.confirm) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") answerConfirm(false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [state.confirm?.id]);

  return (
    <>
      {state.confirm && (
        <div role="dialog" aria-modal="true" onClick={() => answerConfirm(false)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 1000,
                   display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
          <div onClick={(e) => e.stopPropagation()}
            style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 10, padding: 18,
                     maxWidth: 440, width: "100%", boxShadow: "0 20px 50px rgba(0,0,0,0.5)" }}>
            <div style={{ fontSize: 14, whiteSpace: "pre-wrap", lineHeight: 1.5, marginBottom: 16 }}>{state.confirm.message}</div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={() => answerConfirm(false)}>Cancel</button>
              <button ref={okRef} className="primary" onClick={() => answerConfirm(true)}>OK</button>
            </div>
          </div>
        </div>
      )}
      {state.toasts.length > 0 && (
        <div style={{ position: "fixed", left: 12, right: 12, bottom: "calc(12px + env(safe-area-inset-bottom, 0px))",
                      zIndex: 1001, display: "flex", flexDirection: "column", gap: 6, alignItems: "center", pointerEvents: "none" }}>
          {state.toasts.map((t) => (
            <div key={t.id} onClick={() => dismissToast(t.id)}
              style={{ pointerEvents: "auto", maxWidth: 560, width: "100%", padding: "10px 14px", borderRadius: 8, fontSize: 13,
                       background: t.tone === "error" ? "#450a0a" : "#0f2a1f", color: t.tone === "error" ? "#fecaca" : "#bbf7d0",
                       border: `1px solid ${t.tone === "error" ? "#7f1d1d" : "#14532d"}`, boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}>
              {t.message}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
