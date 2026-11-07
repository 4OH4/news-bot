/**
 * Start-up wiring for feedback (#184): the sign-out guard, clearing on a chosen sign-out, and
 * flushes on start, when the network returns and when the page becomes visible. A 401 or 403
 * never runs the sign-out hooks (#180), so the queue simply waits and flushes after reconnecting.
 */
import { onSignOut } from "../auth/signout";
import { clearFeedbackData } from "./db";
import { flushQueue, releaseEnqueues } from "./queue";
import { installSignOutGuard } from "./signout-guard";

let started = false;

/**
 * The sign-out hook: clears what the guard counted, then lets through any event enqueued while
 * signing out, so it is kept for the next start rather than deleted unsent.
 */
async function clearOnSignOut(): Promise<void> {
  try {
    await clearFeedbackData();
  } finally {
    releaseEnqueues();
  }
}

function flush(): void {
  void flushQueue().catch((err: unknown) => console.error("feedback flush failed", err));
}

/** Call once at start-up. */
export function startFeedback(): void {
  if (started) return;
  started = true;
  installSignOutGuard();
  onSignOut(clearOnSignOut);
  window.addEventListener("online", flush);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") flush();
  });
  flush();
}
