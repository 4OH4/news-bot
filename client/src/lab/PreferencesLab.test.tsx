// @vitest-environment happy-dom
//
// #332: the hidden /lab/preferences prototype, on mock data. Nothing is stored and no API is called.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt, settle, type FakeRouter } from "../../test/test-env";

let root: HTMLElement;
let router: FakeRouter;

beforeEach(() => {
  vi.stubGlobal("__APP_VERSION__", "test");
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  localStorage.clear();
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function open(path: string, search = ""): Promise<void> {
  vi.stubGlobal("location", { ...locationAt(path), search });
  router = installRouter({ online: false });
  await loadStores();
  const { App } = await import("../App");
  render(h(App, null), root);
  await settle();
}

const NOTE =
  "Hide works on every tab. Fewer and More change the order in Feed and Longer; News stays newest first.";

function groups(): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('[role="radiogroup"]')];
}

/** The control whose label is exactly `name`. */
function group(name: string): HTMLElement {
  const g = groups().find((el) => el.getAttribute("aria-label") === name);
  if (!g) throw new Error(`no control labelled ${name}`);
  return g;
}

function pick(g: HTMLElement, word: string): void {
  const r = [...g.querySelectorAll<HTMLElement>('[role="radio"]')].find((el) =>
    new RegExp(word, "i").test(el.getAttribute("aria-label") ?? el.textContent ?? ""),
  );
  if (!r) throw new Error(`no ${word} radio`);
  r.click();
}

function selected(g: HTMLElement): string {
  const on = g.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]');
  return on?.getAttribute("aria-label") ?? on?.textContent ?? "";
}

function clickable(text: string): HTMLElement {
  const found = [...root.querySelectorAll<HTMLElement>("button,[role=button],a")].find((el) =>
    (el.textContent ?? "").includes(text),
  );
  if (!found) throw new Error(`nothing to click for ${text}`);
  return found;
}

describe("/lab/preferences", () => {
  it("opens on variant a with a switcher for a, b and c and no tab bar", async () => {
    await open("/lab/preferences");
    expect(root.querySelector('nav[aria-label="Sections"]')).toBeNull();
    for (const v of ["a", "b", "c"]) {
      const hit = [...root.querySelectorAll<HTMLElement>("a,button")].some((el) =>
        new RegExp(`^(variant )?${v}\\b`, "i").test((el.textContent || el.getAttribute("aria-label") || "").trim()),
      );
      expect(hit, `switcher entry ${v}`).toBe(true);
    }
    expect(group("Sport")).toBeDefined();
  });

  it.each(["a", "b", "c"])("variant %s shows the Hide / Fewer / More note", async (v) => {
    await open("/lab/preferences", `?v=${v}`);
    expect(root.textContent).toContain(NOTE);
  });

  it("variant a: Hide on Sport selects Hide and the summary says 1 hidden", async () => {
    await open("/lab/preferences");
    expect(selected(group("Sport"))).toMatch(/show/i);
    pick(group("Sport"), "hide");
    await settle();
    expect(selected(group("Sport"))).toMatch(/hide/i);
    expect(root.textContent).toContain("1 hidden");
  });

  it("variant a: Show 18 more reveals the other sources, grouped by kind, with working controls", async () => {
    await open("/lab/preferences");
    const before = groups().length;
    clickable("Show 18 more").click();
    await settle();
    expect(groups().length).toBeGreaterThanOrEqual(before + 18);
    for (const kind of ["Long reads", "Blogs"]) expect(root.textContent).toContain(kind);
    const fresh = groups()[groups().length - 1] as HTMLElement;
    pick(fresh, "more");
    await settle();
    expect(selected(fresh)).toMatch(/more/i);
  });

  it("variant b: tapping BBC News opens a sheet; choosing More closes it and the row shows +", async () => {
    await open("/lab/preferences", "?v=b");
    clickable("BBC News").click();
    await settle();
    const sheet = root.querySelector<HTMLElement>('[role="dialog"]');
    expect(sheet).not.toBeNull();
    pick(sheet?.querySelector<HTMLElement>('[role="radiogroup"]') as HTMLElement, "more");
    await settle();
    expect(root.querySelector('[role="dialog"]')).toBeNull();
    expect(clickable("BBC News").textContent).toContain("+");
  });

  it("variant c: UK set to Fewer leaves the sources beneath it on Show", async () => {
    await open("/lab/preferences", "?v=c");
    pick(group("UK"), "fewer");
    await settle();
    expect(selected(group("UK"))).toMatch(/fewer/i);
    const regions = ["UK", "Europe", "US", "World"];
    const sources = groups().filter((g) => !regions.includes(g.getAttribute("aria-label") ?? ""));
    expect(sources.length).toBeGreaterThan(0);
    for (const g of sources) {
      const label = g.getAttribute("aria-label") ?? "";
      if (label === "Everywhere") continue;
      // Topic and source controls are untouched by the region change.
      expect(selected(g), label).toMatch(/show/i);
    }
  });

  it("?setup=1 shows the welcome and the starter set, and Start reading confirms", async () => {
    await open("/lab/preferences", "?setup=1");
    expect(root.textContent).toContain("Choose what you see. You can change this any time in Settings.");
    expect(root.textContent).toContain("BBC News");
    expect(root.textContent).toContain("Sport");
    for (const g of groups()) expect(selected(g)).toMatch(/show/i);
    const before = root.textContent;
    clickable("Start reading").click();
    await settle();
    expect(root.textContent).not.toBe(before);
  });

  it("stores nothing and calls no API, and a reload restores the defaults", async () => {
    await open("/lab/preferences");
    pick(group("Sport"), "hide");
    await settle();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(router.calls).toEqual([]);
    render(null, root);
    await open("/lab/preferences");
    expect(selected(group("Sport"))).toMatch(/show/i);
    expect(root.textContent).not.toContain("1 hidden");
  });

  it.each(["/", "/news", "/longer", "/liked", "/settings"])("%s does not link to the lab", async (path) => {
    await open(path);
    expect(root.querySelector('a[href*="/lab"]')).toBeNull();
  });
});
