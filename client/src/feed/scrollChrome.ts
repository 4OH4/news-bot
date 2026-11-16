/** Pure scroll-direction logic for the feed screens' moving header and Back to top button (#307). */

export interface ChromeState {
  /** Last scrollY seen. */
  y: number;
  /** How far the header has moved up, in px, between 0 and its own height. */
  offset: number;
  /** Whether the Back to top button shows. */
  showButton: boolean;
}

export const INITIAL_CHROME: ChromeState = { y: 0, offset: 0, showButton: false };

/**
 * The next state after a scroll to `y`. The header follows the scroll delta (so it moves with the
 * page, not on a timer) and is fully shown at the top. The button shows once more than two screens
 * down and the last move was upward (`delta` above is the raw scroll change); it hides on scrolling down or within one screen of the top.
 */
export function nextChrome(
  prev: ChromeState,
  y: number,
  viewport: number,
  headerHeight: number,
  gap = 0,
): ChromeState {
  const delta = y - prev.y;
  // The header rests `gap` px below the page top (the body padding) and only sticks once that
  // much has scrolled by, so the first `gap` px of scroll move it natively and don't count.
  const effective = Math.max(0, y - gap) - Math.max(0, prev.y - gap);
  const offset = y <= gap ? 0 : Math.min(headerHeight, Math.max(0, prev.offset + effective));
  let showButton = prev.showButton;
  if (delta > 0 || y <= viewport) showButton = false;
  else if (delta < 0 && y > 2 * viewport) showButton = true;
  return { y, offset, showButton };
}
