// @vitest-environment happy-dom
//
// #271: Settings > Text size, a group of four radio buttons after Theme and before Account.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt } from "../../test/test-env";

const KEY = "newsfeed-font-size";
let root: HTMLElement;

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.fontSize;
  vi.stubGlobal("location", locationAt("/settings"));
  vi.stubGlobal("__APP_VERSION__", "9.8.7-invented");
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  installRouter({ online: false });
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  localStorage.clear();
  delete document.documentElement.dataset.fontSize;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function showSettings(): Promise<void> {
  await loadStores();
  const { SettingsScreen } = await import("./SettingsScreen");
  render(h(SettingsScreen, null), root);
}

function group(): HTMLElement {
  const found = root.querySelector<HTMLElement>('[role="radiogroup"][aria-label="Text size"]');
  if (!found) throw new Error("no Text size radio group");
  return found;
}

function radio(name: RegExp): HTMLInputElement {
  const found = [...group().querySelectorAll<HTMLInputElement>('input[type="radio"]')].find((r) =>
    name.test(r.closest("label")?.textContent ?? r.getAttribute("aria-label") ?? ""),
  );
  if (!found) throw new Error(`no radio button named ${String(name)}`);
  return found;
}

function choose(r: HTMLInputElement): void {
  r.click();
  r.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("SettingsScreen text size", () => {
  it("has a Text size group of four radio buttons, after Theme and before Account", async () => {
    await showSettings();
    expect(group().querySelectorAll('input[type="radio"]')).toHaveLength(4);
    for (const name of [/small/i, /regular/i, /large/i, /xl/i]) expect(radio(name)).toBeDefined();
    const text = root.textContent ?? "";
    expect(text.indexOf("Text size")).toBeGreaterThan(-1);
    expect(text.indexOf("Theme")).toBeLessThan(text.indexOf("Text size"));
    expect(text.indexOf("Text size")).toBeLessThan(text.indexOf("Account"));
  });

  it("shows Regular checked, and no data-font-size, when nothing is stored", async () => {
    await showSettings();
    expect(radio(/regular/i).checked).toBe(true);
    expect(document.documentElement.hasAttribute("data-font-size")).toBe(false);
  });

  it("shows Large checked when Large is stored", async () => {
    localStorage.setItem(KEY, "large");
    await showSettings();
    expect(radio(/large/i).checked).toBe(true);
    expect(radio(/regular/i).checked).toBe(false);
  });

  it("shows Regular checked for a garbage stored value", async () => {
    localStorage.setItem(KEY, "huge");
    await showSettings();
    expect(radio(/regular/i).checked).toBe(true);
  });

  it("choosing Large sets data-font-size and stores the choice", async () => {
    await showSettings();
    choose(radio(/large/i));
    await vi.waitFor(() => expect(document.documentElement.dataset.fontSize).toBe("large"));
    expect(localStorage.getItem(KEY)).toBe("large");
    expect(radio(/large/i).checked).toBe(true);
  });

  it("choosing Regular after XL removes data-font-size and clears the stored key", async () => {
    localStorage.setItem(KEY, "xl");
    document.documentElement.dataset.fontSize = "xl";
    await showSettings();
    expect(radio(/xl/i).checked).toBe(true);

    choose(radio(/regular/i));

    await vi.waitFor(() =>
      expect(document.documentElement.hasAttribute("data-font-size")).toBe(false),
    );
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(radio(/regular/i).checked).toBe(true);
  });

  it("choosing a size does not touch the theme setting", async () => {
    localStorage.setItem("newsfeed-theme", "dark");
    await showSettings();
    choose(radio(/small/i));
    await vi.waitFor(() => expect(document.documentElement.dataset.fontSize).toBe("small"));
    expect(localStorage.getItem("newsfeed-theme")).toBe("dark");
  });
});
