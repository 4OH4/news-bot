const KEY = "newsfeed-font-size";

export type FontSize = "small" | "regular" | "large" | "xl";

/** The sizes in display order; also the list of values a stored string may take. */
export const FONT_SIZES: readonly FontSize[] = ["small", "regular", "large", "xl"];

function isFontSize(value: unknown): value is FontSize {
  return typeof value === "string" && (FONT_SIZES as readonly string[]).includes(value);
}

/** The stored text size, or "regular" when nothing valid is stored. */
export function fontSize(): FontSize {
  try {
    const value = localStorage.getItem(KEY);
    return isFontSize(value) ? value : "regular";
  } catch {
    return "regular";
  }
}

/** Sets `data-font-size` on the root element ("regular" removes it) and remembers the choice. */
export function setFontSize(size: FontSize): void {
  if (size === "regular") {
    delete document.documentElement.dataset.fontSize;
  } else {
    document.documentElement.dataset.fontSize = size;
  }
  try {
    if (size === "regular") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, size);
  } catch {
    // Storage can be blocked; the choice then lasts until reload.
  }
}

/** Applies the stored text size at startup. */
export function applyStoredFontSize(): void {
  const size = fontSize();
  if (size === "regular") delete document.documentElement.dataset.fontSize;
  else document.documentElement.dataset.fontSize = size;
}
