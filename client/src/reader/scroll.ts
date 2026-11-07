const KEY = "newsfeed-list-scroll";
const FROM_LIST = "newsfeed-from-list";

/** Remembers the list's scroll position when the page is left for an article. */
export function saveListScroll(): void {
  try {
    sessionStorage.setItem(KEY, String(window.scrollY));
    sessionStorage.setItem(FROM_LIST, "1");
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
    target = Number(sessionStorage.getItem(KEY) ?? 0);
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

/** True when this tab opened the article from the list, so history.back() returns to it.
 * The referrer cannot tell: the app sends Referrer-Policy: no-referrer. */
export function openedFromList(): boolean {
  try {
    return sessionStorage.getItem(FROM_LIST) === "1" && history.length > 1;
  } catch {
    return false;
  }
}
