import { apiFetch } from "../api/client";
import { sessionStatus } from "./status";

/**
 * The only module that knows how to reconnect. `/reconnect` must stay in the service
 * worker's network-only list (sw-routes.ts). Never use IAP's session-refresh mode:
 * in spike #196 it only reached IAP's keep-alive page.
 */
export const RECONNECT_PATH = "/reconnect";

/** Plain navigation: IAP sends the page through Google and back. */
export function reconnect(): void {
  location.href = RECONNECT_PATH;
}

let rechecking = false;

/**
 * Safety net for a Back after a failed sign-in: the old page returns with its session
 * already restored. Only runs while the banner shows and the browser is online; never navigates.
 */
export async function recheckSession(): Promise<void> {
  if (sessionStatus !== "reconnect" || !navigator.onLine || rechecking) return;
  rechecking = true;
  try {
    await apiFetch("/api/feed", { cache: "no-store" }); // success reports ok
  } catch {
    // Still 401, or offline: the banner stays as it is.
  } finally {
    rechecking = false;
  }
}

const LISTENERS = Symbol.for("newsfeed.reconnect.listeners");
type Installed = { pageshow: () => void; visible: () => void };

/** Registers the listeners once per page; a re-evaluated module (hot reload) replaces the old pair. */
function install(): void {
  if (typeof window === "undefined" || typeof document === "undefined") return;
  const holder = globalThis as unknown as Record<symbol, Installed | undefined>;
  const old = holder[LISTENERS];
  if (old) {
    window.removeEventListener("pageshow", old.pageshow);
    document.removeEventListener("visibilitychange", old.visible);
  }
  const next: Installed = {
    pageshow: () => void recheckSession(),
    visible: () => {
      if (document.visibilityState === "visible") void recheckSession();
    },
  };
  holder[LISTENERS] = next;
  window.addEventListener("pageshow", next.pageshow);
  document.addEventListener("visibilitychange", next.visible);
}

install();
