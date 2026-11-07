const KEY = "newsfeed-list-scroll";
const STATE_KEY = "newsfeedListOrigin";

export type ListPath = "/" | "/liked";

function currentList(): ListPath {
  return location.pathname.replace(/\/+$/, "") === "/liked" ? "/liked" : "/";
}
const FROM_LIST = "newsfeed-from-list";

/** Remembers the list's scroll position when the page is left for an article. */
export function saveListScroll(): void {
  try {
    const list = currentList();
    sessionStorage.setItem(`${KEY}:${list}`, String(window.scrollY));
    sessionStorage.setItem(FROM_LIST, list);
  } catch {
    // Not essential.
  }
}

/** On a back navigation to the list, scrolls to the saved position once the items exist. */
export function restoreListScroll(): void {
  const nav = performance.getEntriesByType("navigation")[0] as
    | PerformanceNavigationTiming
    | undefined;
  if (nav?.type !== "back_forward") return;
  let target = 0;
  try {
    target = Number(sessionStorage.getItem(`${KEY}:${currentList()}`) ?? 0);
  } catch {
    return;
  }
  if (!(target > 0)) return;
  const deadline = performance.now() + 4000;
  const step = () => {
    window.scrollTo(0, target);
    if (Math.abs(window.scrollY - target) > 2 && performance.now() < deadline) {
      requestAnimationFrame(step);
    }
  };
  requestAnimationFrame(step);
}

/** The list this article was opened from, or null. The referrer cannot tell: the app sends
 * Referrer-Policy: no-referrer. The first call moves the session flag into this history entry's
 * state, so it is used once and a later direct open in the same tab does not inherit it. */
export function listOrigin(): ListPath | null {
  let origin: unknown = null;
  try {
    const state = history.state as Record<string, unknown> | null;
    origin = state?.[STATE_KEY] ?? null;
    if (origin === null) {
      origin = sessionStorage.getItem(FROM_LIST);
      if (origin === "/" || origin === "/liked") {
        history.replaceState({ ...state, [STATE_KEY]: origin }, "");
      }
    }
    sessionStorage.removeItem(FROM_LIST);
  } catch {
    return null;
  }
  if (history.length <= 1) return null;
  return origin === "/" || origin === "/liked" ? origin : null;
}
