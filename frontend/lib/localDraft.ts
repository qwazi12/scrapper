"use client";

import { useEffect, useRef, useState } from "react";
import { registerNavigationBlocker } from "./navigationGuard";

type Fields = Record<string, string>;
type Draft<T> = { basis: string; value: T; at: string; conflict?: boolean };

/** Preserve unsaved words across navigation without replacing a newer server edit. */
export function useLocalDraft<T extends Fields>(key: string, server: T) {
  const serialized = JSON.stringify(server);
  const [value, setValue] = useState(server);
  const [baseline, setBaseline] = useState(serialized);
  const [ready, setReady] = useState("");
  const [pending, setPending] = useState<Draft<T> | null>(null);
  const [conflict, setConflict] = useState(false);
  const [storageError, setStorageError] = useState(false);
  const current = useRef({ key: "", basis: serialized, value: server });
  const dirty = JSON.stringify(value) !== baseline;

  function change(next: T) { current.current.value = next; setValue(next); }

  useEffect(() => {
    const record = current.current;
    if (record.key !== key) {
      current.current = { key, basis: serialized, value: server };
      setBaseline(serialized); setValue(server); setConflict(false); setPending(null);
      try {
        const raw = localStorage.getItem(key);
        if (raw && raw.length <= 1_000_000) {
          const cached = JSON.parse(raw);
          if (cached && typeof cached.basis === "string" && cached.value &&
              Object.keys(server).every(k => typeof cached.value[k] === "string") &&
              JSON.stringify(cached.value) !== serialized) setPending(cached);
        }
        setStorageError(false);
      } catch { setStorageError(true); }
      setReady(key);
    } else if (serialized !== record.basis) {
      const edited = JSON.stringify(record.value) !== record.basis;
      record.basis = serialized; setBaseline(serialized);
      if (!edited || JSON.stringify(record.value) === serialized) {
        record.value = server; setValue(server); setConflict(false);
      } else setConflict(true);
    }
    // The serialized value is the dependency; fresh API objects alone cannot
    // reset the editor on every idle poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, serialized]);

  useEffect(() => {
    if (ready !== key) return;
    try {
      if (dirty) localStorage.setItem(key, JSON.stringify({ basis: baseline, value, conflict, at: new Date().toISOString() }));
      else if (!pending) localStorage.removeItem(key);
      setStorageError(false);
    } catch { setStorageError(true); }
  }, [key, ready, value, baseline, dirty, pending, conflict]);

  useEffect(() => registerNavigationBlocker(() => dirty && storageError), [dirty, storageError]);

  useEffect(() => {
    if (!dirty || !storageError) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty, storageError]);

  function saved(next: T) {
    const basis = JSON.stringify(next);
    current.current = { key, basis, value: next };
    setValue(next); setBaseline(basis); setPending(null); setConflict(false);
    try { localStorage.removeItem(key); setStorageError(false); } catch { setStorageError(true); }
  }

  function restore() {
    if (!pending) return;
    change(pending.value); setConflict(pending.conflict === true || pending.basis !== serialized); setPending(null);
  }

  return { value, change, dirty, pending, conflict, storageError, restore,
    discard: () => saved(server), saved, keepAfterReview: () => setConflict(false), server };
}
