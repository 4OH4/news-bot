const KEY = "newsfeed-theme";

export type Theme = "light" | "dark";

function readStored(): Theme | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

/** The theme in force: the stored choice, else the system setting. */
export function currentTheme(): Theme {
  const stored = readStored();
  if (stored) return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Applies the stored choice, if any, as `data-theme` on the root element. */
export function applyStoredTheme(): void {
  const stored = readStored();
  if (stored) document.documentElement.dataset.theme = stored;
}

/** Switches to the other theme and remembers the choice. Returns the new theme. */
export function toggleTheme(): Theme {
  const next: Theme = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // Storage can be blocked; the choice then lasts until reload.
  }
  return next;
}

export type ThemeChoice = Theme | "system";

/** The stored choice, or "system" when the app follows the phone's setting. */
export function themeChoice(): ThemeChoice {
  return readStored() ?? "system";
}

/** Sets the theme choice; "system" clears the stored key and the `data-theme` attribute. */
export function setThemeChoice(choice: ThemeChoice): void {
  if (choice === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = choice;
  }
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    // Storage can be blocked; the choice then lasts until reload.
  }
}
