import type { RefObject } from "preact";
import { useEffect, useState } from "preact/hooks";
import { INITIAL_CHROME, nextChrome, snapChrome, type ChromeState } from "./scrollChrome";

/**
 * Tracks the page scroll (passive listener, one update per frame) and returns the header's offset
 * and whether the Back to top button shows (#307). `headerRef` is the moving header.
 */
export function useScrollChrome(
  headerRef: RefObject<HTMLElement>,
): ChromeState & { snapping: boolean } {
  const [state, setState] = useState<ChromeState & { snapping: boolean }>({
    ...INITIAL_CHROME,
    snapping: false,
  });

  useEffect(() => {
    let current = INITIAL_CHROME;
    let frame = 0;
    let idle = 0;
    const height = () => headerRef.current?.offsetHeight ?? 0;
    const gap = () => parseFloat(getComputedStyle(document.body).paddingTop) || 0;
    const reduce = () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const update = () => {
      frame = 0;
      current = nextChrome(current, window.scrollY, window.innerHeight, height(), gap());
      setState({ ...current, snapping: false });
    };
    // Scrolling has stopped: finish a part-shown header's slide, animated (not under reduced motion).
    const settle = () => {
      window.clearTimeout(idle);
      if (frame) {
        cancelAnimationFrame(frame);
        update();
      }
      const next = snapChrome(current, height(), gap());
      if (next === current) return;
      current = next;
      setState({ ...current, snapping: !reduce() });
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
      // `scrollend` is not everywhere, so a short idle timeout backs it up.
      window.clearTimeout(idle);
      idle = window.setTimeout(settle, 120);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("scrollend", settle);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("scrollend", settle);
      window.clearTimeout(idle);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [headerRef]);

  return state;
}

/** Scrolls to the top, without animation when the reader prefers reduced motion. */
export function scrollToTop(): void {
  const reduce =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
}
