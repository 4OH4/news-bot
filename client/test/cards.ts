/**
 * Helpers for the feed-card, hidden-story and Settings tests (#217). They find controls by role
 * and accessible name, so they don't depend on how the card's menu is built.
 */
import { buttonsNamed } from "./test-env";

/** The accessible name of an element: `aria-label` if set, else its text. */
export function nameOf(el: Element): string {
  return (el.getAttribute("aria-label") ?? el.textContent ?? "").trim();
}

/**
 * True when the card is marked as seen: a class token or `data-` attribute on the card, or on
 * an element inside it, that names "seen" (for instance `card-seen` or `data-seen="true"`).
 */
export function looksSeen(card: Element): boolean {
  for (const el of [card, ...card.querySelectorAll("*")]) {
    if ([...el.classList].some((c) => /(^|[-_])seen($|[-_])/i.test(c))) return true;
    const attr = el.getAttribute("data-seen");
    if (attr !== null && attr !== "false") return true;
  }
  return false;
}

/** The card's menu toggle: a `<summary>` or a button that declares a popup or expanded state. */
export function menuToggle(scope: ParentNode): HTMLElement | null {
  return scope.querySelector<HTMLElement>(
    "summary, button[aria-haspopup], button[aria-expanded]",
  );
}

/** Opens the card's menu if the action isn't already showing, then clicks the action. */
export async function chooseAction(scope: ParentNode, name: RegExp): Promise<void> {
  let [action] = buttonsNamed(scope, name);
  if (!action) {
    menuToggle(scope)?.click();
    await new Promise((r) => setTimeout(r, 0));
    [action] = buttonsNamed(scope, name);
  }
  if (!action) throw new Error(`no action named ${name} in the card`);
  action.click();
}

/** The Unmute button on the Settings row for `domain`. */
export function unmuteButtonFor(scope: ParentNode, domain: string): HTMLButtonElement | undefined {
  return buttonsNamed(scope, /unmute/i).find((b) => {
    if (nameOf(b).includes(domain)) return true;
    // Walk up to the smallest ancestor holding only this Unmute button.
    let el: Element | null = b.parentElement;
    while (el && buttonsNamed(el, /unmute/i).length === 1) {
      if ((el.textContent ?? "").includes(domain)) return true;
      el = el.parentElement;
    }
    return false;
  });
}
