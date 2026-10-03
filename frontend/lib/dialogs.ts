// In-page replacements for window.confirm() and window.alert().
//
// Browsers can silently block native dialogs (Chrome's "prevent this page from
// creating additional dialogs", in-app browsers such as Chrome on iOS with
// some settings): confirm() then returns false at once and alert() shows
// nothing — so Stop, Delete, Post now… did nothing (2026-10-02/03). These are
// drawn by <DialogHost/> (mounted once in page.tsx), so they always appear.

type ConfirmReq = { id: number; message: string; resolve: (ok: boolean) => void };
type Toast = { id: number; message: string; tone: "info" | "error" };
type Listener = () => void;

let seq = 0;
const confirms: ConfirmReq[] = [];
const toasts: Toast[] = [];
const listeners = new Set<Listener>();
const emit = () => listeners.forEach((l) => l());

export function subscribe(l: Listener): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
export function snapshot() {
  return { confirm: confirms[0] || null, toasts: [...toasts] };
}

/** Ask the user; resolves true (OK) or false (Cancel). */
export function askConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    confirms.push({ id: ++seq, message, resolve });
    emit();
  });
}

export function answerConfirm(ok: boolean): void {
  const c = confirms.shift();
  if (c) c.resolve(ok);
  emit();
}

/** A message that stays on screen for a few seconds (errors longer). */
export function notify(message: unknown): void {
  const text = String(message ?? "");
  const tone: Toast["tone"] = /fail|error|could not|couldn't|✕/i.test(text) ? "error" : "info";
  const t = { id: ++seq, message: text, tone };
  toasts.push(t);
  emit();
  setTimeout(() => {
    const i = toasts.indexOf(t);
    if (i >= 0) toasts.splice(i, 1);
    emit();
  }, tone === "error" ? 9000 : 5000);
}

export function dismissToast(id: number): void {
  const i = toasts.findIndex((t) => t.id === id);
  if (i >= 0) toasts.splice(i, 1);
  emit();
}
