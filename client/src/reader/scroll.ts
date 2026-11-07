const KEY = "newsfeed-list-scroll";

/** Remembers the list's scroll position when the page is left for an article. */
export function saveListScroll(): void {
  try {
    sessionStorage.setItem(KEY, String(window.scrollY));
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
