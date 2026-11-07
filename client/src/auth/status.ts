/**
 * Session status, owned in this one module (docs/phases/phase-3.md, "Reconnect").
 * Screens read it; only `apiFetch` and the re-check report to it.
 */
export type SessionStatus = "ok" | "reconnect" | "not-allowed";

/** Live binding: importers always see the current value. */
export let sessionStatus: SessionStatus = "ok";

type Listener = (status: SessionStatus) => void;
const listeners = new Set<Listener>();

function set(next: SessionStatus): void {
  if (next === sessionStatus) return;
  sessionStatus = next;
  for (const fn of [...listeners]) fn(next);
}

/** Calls `fn` on every change; returns a function that stops the calls. */
export function onStatusChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** A 401 offline is just being offline, and Reconnect would open a page that can't load. */
export function reportAuthRequired(): void {
  if (navigator.onLine) set("reconnect");
}

export function reportNotAllowed(): void {
  set("not-allowed");
}

export function reportOk(): void {
  set("ok");
}
