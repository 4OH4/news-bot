// @vitest-environment happy-dom
//
// #258: the theme can go back to following the system after a choice was made.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyStoredTheme, currentTheme, setThemeChoice, themeChoice, toggleTheme } from "./theme";

function systemPrefersDark(dark: boolean): void {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({ matches: dark && query.includes("dark"), media: query })),
  );
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  systemPrefersDark(true);
});

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
});

describe("themeChoice and setThemeChoice", () => {
  it("is system when nothing is stored", () => {
    expect(themeChoice()).toBe("system");
  });

  it("remembers Light and Dark, and sets data-theme", () => {
    setThemeChoice("light");
    expect(themeChoice()).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    setThemeChoice("dark");
    expect(themeChoice()).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("reports the reader button's choice", () => {
    systemPrefersDark(false);
    toggleTheme();
    expect(themeChoice()).toBe("dark");
  });

  it("system removes data-theme and the stored choice, and the app then follows the phone", () => {
    systemPrefersDark(false);
    setThemeChoice("dark");
    setThemeChoice("system");

    expect(document.documentElement.dataset.theme).toBeUndefined();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(themeChoice()).toBe("system");
    expect(localStorage.length).toBe(0);

    // After a reload nothing is applied, and the current theme is the system's.
    applyStoredTheme();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(currentTheme()).toBe("light");
    systemPrefersDark(true);
    expect(currentTheme()).toBe("dark");
  });
});
