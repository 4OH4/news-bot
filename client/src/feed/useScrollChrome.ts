import type { RefObject } from "preact";
import { useEffect, useState } from "preact/hooks";
import { INITIAL_CHROME, nextChrome, type ChromeState } from "./scrollChrome";

/**
 * Tracks the page scroll (passive listener, one update per frame) and returns the header's offset
 * and whether the Back to top button shows (#307). `headerRef` is the moving header. The header
 * moves only as far as the page does; nothing finishes its move when scrolling stops (#411).
 */
export function useScrollChrome(headerRef: RefObject<HTMLElement>): ChromeState {
  const [state, setState] = useState<ChromeState>(INITIAL_CHROME);

  useEffect(() => {
    let current = INITIAL_CHROME;
    let frame = 0;
    const height = () => headerRef.current?.offsetHeight ?? 0;
    const gap = () => parseFloat(getComputedStyle(document.body).paddingTop) || 0;
    const update = () => {
      frame = 0;
      current = nextChrome(current, window.scrollY, window.innerHeight, height(), gap());
      setState(current);
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
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
