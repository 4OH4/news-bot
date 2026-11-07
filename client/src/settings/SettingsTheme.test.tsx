// @vitest-environment happy-dom
//
// #258: Settings > Theme, a group of three radio buttons above Account.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt } from "../../test/test-env";

let root: HTMLElement;

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
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
  delete document.documentElement.dataset.theme;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function showSettings(): Promise<void> {
  await loadStores();
  const { SettingsScreen } = await import("./SettingsScreen");
  render(h(SettingsScreen, null), root);
}

function radio(name: RegExp): HTMLInputElement {
  const found = [...root.querySelectorAll<HTMLInputElement>('input[type="radio"]')].find((r) => {
    const label =
      r.getAttribute("aria-label") ??
      r.closest("label")?.textContent ??
      (r.id ? root.querySelector(`label[for="${r.id}"]`)?.textContent : null) ??
      "";
    return name.test(label);
  });
  if (!found) throw new Error(`no radio button named ${String(name)}`);
  return found;
}

function choose(r: HTMLInputElement): void {
  r.click();
  r.dispatchEvent(new Event("change", { bubbles: true }));
}

describe("SettingsScreen theme", () => {
  it("has a Theme group of three radio buttons above Account", async () => {
    await showSettings();
    expect(root.querySelectorAll('input[type="radio"]')).toHaveLength(3);
    for (const name of [/follow system/i, /light/i, /dark/i]) expect(radio(name)).toBeDefined();
    const text = root.textContent ?? "";
    expect(text.indexOf("Theme")).toBeGreaterThan(-1);
    expect(text.indexOf("Theme")).toBeLessThan(text.indexOf("Account"));
  });

  it("shows Follow system checked when nothing is stored", async () => {
    await showSettings();
    expect(radio(/follow system/i).checked).toBe(true);
  });

  it("shows Light checked when Light is stored", async () => {
    localStorage.setItem("newsfeed-theme", "light");
    await showSettings();
    expect(radio(/light/i).checked).toBe(true);
    expect(radio(/follow system/i).checked).toBe(false);
  });

  it("choosing Dark sets data-theme and stores the choice", async () => {
    await showSettings();
    choose(radio(/dark/i));
    await vi.waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(localStorage.getItem("newsfeed-theme")).toBe("dark");
  });

  it("choosing Follow system removes data-theme and clears the stored choice", async () => {
    localStorage.setItem("newsfeed-theme", "dark");
    document.documentElement.dataset.theme = "dark";
    await showSettings();
    expect(radio(/dark/i).checked).toBe(true);

    choose(radio(/follow system/i));

    await vi.waitFor(() => expect(document.documentElement.hasAttribute("data-theme")).toBe(false));
    expect(localStorage.getItem("newsfeed-theme")).toBeNull();
    expect(radio(/follow system/i).checked).toBe(true);
  });
});
