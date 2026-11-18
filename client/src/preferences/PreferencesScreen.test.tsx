// @vitest-environment happy-dom
//
// #338: the preferences screen at /settings/preferences, wired to the fake API.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installPrefs, makeMenu, type FakePrefs } from "../../test/preferences-env";
import { loadStores, locationAt, makeFeed, makeItem, settle } from "../../test/test-env";

let root: HTMLElement;
let fake: FakePrefs;

beforeEach(() => {
  vi.stubGlobal("__APP_VERSION__", "test");
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Opens the app at the preferences route against the fake API. */
async function open(
  menu = makeMenu(),
  opts: { online?: boolean; search?: string } = {},
): Promise<void> {
  vi.stubGlobal("location", { ...locationAt("/settings/preferences"), search: opts.search ?? "" });
  fake = installPrefs(menu, { online: opts.online ?? true, feed: makeFeed([makeItem("a1b2c3d4e5f60718")]) });
  await loadStores();
  const { App } = await import("../App");
  render(h(App, null), root);
  await settle();
}

const section = (heading: string): HTMLElement => {
  const h2 = [...root.querySelectorAll("h2")].find((el) => el.textContent?.trim() === heading);
  if (!h2?.closest("section")) throw new Error(`no section ${heading}`);
  return h2.closest("section")!;
};
const rows = (el: ParentNode) => el.querySelectorAll('[role="radiogroup"]').length;
const control = (name: string): HTMLElement =>
  root.querySelector<HTMLElement>(`[role="radiogroup"][aria-label="${name}"]`)!;
const pick = (name: string, word: string): void =>
  [...control(name).querySelectorAll<HTMLElement>('[role="radio"]')]
    .find((r) => r.getAttribute("aria-label") === word)!
    .click();
const checked = (name: string): string | null | undefined =>
  control(name).querySelector('[role="radio"][aria-checked="true"]')?.getAttribute("aria-label");
const button = (text: RegExp): HTMLButtonElement | undefined =>
  [...root.querySelectorAll("button")].find((b) => text.test(b.textContent ?? ""));

describe("the preferences screen", () => {
  it("shows the API's sections in order, then Topics, with the note and no Regions", async () => {
    await open();
    const headings = [...root.querySelectorAll("h2")].map((el) => el.textContent?.trim());
    expect(headings).toEqual(["News sources", "Tech", "Science", "More sources", "Long Reads", "Topics"]);
    expect(root.textContent).toContain(
      "Hide works on every tab. Fewer and More change the order in Feed and Longer; News stays newest first.",
    );
    expect(root.textContent).not.toContain("Regions");
  });

  it("shows starters first and 'Show n more' opens the rest of that section", async () => {
    await open();
    const news = section("News sources");
    expect(rows(news)).toBe(3);
    const more = button(/Show 4 more/)!;
    expect(more.getAttribute("aria-expanded")).toBe("false");
    more.click();
    await settle(10);
    expect(rows(news)).toBe(7);
    expect(news.textContent).toContain("Show fewer");
  });

  it("has no button for an all-starter section and keeps one for a section with no starters", async () => {
    await open();
    expect(section("Tech").querySelector("button:not([role])")).toBeNull();
    expect(rows(section("More sources"))).toBe(0);
    expect(section("More sources").textContent).toContain("Show 2 more");
  });

  it("saves a change once, with the current version and every non-Show state", async () => {
    await open(makeMenu({ version: 4, states: { wired: "fewer" } }));
    pick("BBC News", "Hide");
    expect(checked("BBC News")).toBe("Hide");
    await settle(700);
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body).toMatchObject({
      version: 4,
      sources: { "bbc-news": "hide", wired: "fewer" },
      topics: {},
    });
    expect(fake.puts[0]?.headers.get("X-Requested-With")).toBe("XMLHttpRequest");
  });

  it("sends three taps within 500 ms as one PUT", async () => {
    await open();
    pick("BBC News", "Hide");
    pick("Wired", "Fewer");
    pick("Sport", "More");
    await settle(700);
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body).toMatchObject({
      sources: { "bbc-news": "hide", wired: "fewer" },
      topics: { sport: "more" },
    });
  });

  it("on a 409 re-applies the owner's change to the newer state and saves once more", async () => {
    await open();
    fake.putAnswers = [
      { status: 409, body: makeMenu({ version: 6, states: { wired: "fewer" } }) },
      { status: 200, body: makeMenu({ version: 7 }) },
    ];
    pick("BBC News", "Hide");
    await settle(1400);
    expect(fake.puts).toHaveLength(2);
    expect(fake.puts[1]?.body).toMatchObject({
      version: 6,
      sources: { "bbc-news": "hide", wired: "fewer" },
    });
    expect(checked("Wired")).toBe("Fewer");
    expect(checked("BBC News")).toBe("Hide");
  });

  it("on a 500 returns the control to its previous state and shows a message", async () => {
    await open();
    fake.putAnswers = [{ status: 500 }];
    pick("BBC News", "Hide");
    await settle(700);
    expect(checked("BBC News")).toBe("Show");
    expect(root.querySelector('[role="alert"],[role="status"]')?.textContent?.trim()).toBeTruthy();
  });

  it("offline says changes need a connection and disables the controls", async () => {
    await open(makeMenu(), { online: false });
    expect(root.textContent).toContain("You're offline; changes need a connection");
    const radios = [...root.querySelectorAll<HTMLButtonElement>('[role="radio"]')];
    if (radios.length > 0) expect(radios.every((r) => r.disabled)).toBe(true);
    expect(fake.puts).toHaveLength(0);
  });

  it("after a save the next feed load is a full fetch, not a conditional one", async () => {
    await open();
    const stores = await import("../store/db");
    await stores.saveFeed(makeFeed([makeItem("a1b2c3d4e5f60718")]), '"etag-1"', "news");
    pick("BBC News", "Hide");
    await settle(700);
    render(null, root);
    vi.stubGlobal("location", locationAt("/news"));
    const { App } = await import("../App");
    render(h(App, null), root);
    await settle();
    expect(fake.feedConditions.at(-1) ?? null).toBeNull();
  });
});

describe("first-run setup", () => {
  const target = () =>
    [
      ...(location.replace as ReturnType<typeof vi.fn>).mock.calls,
      ...(location.assign as ReturnType<typeof vi.fn>).mock.calls,
    ].map((c) => String(c[0]));

  async function start(menu: ReturnType<typeof makeMenu> | null, online = true): Promise<void> {
    vi.stubGlobal("location", locationAt("/"));
    fake = installPrefs(menu, { online, feed: makeFeed([makeItem("a1b2c3d4e5f60718")]) });
    await loadStores();
    const { App } = await import("../App");
    render(h(App, null), root);
    await settle();
  }

  it("opens the setup screen once when setup_done is false", async () => {
    await start(makeMenu({ setup_done: false }));
    expect(target()).toContain("/settings/preferences?setup=1");
  });

  it.each([
    ["setup is done", makeMenu({ setup_done: true }), true],
    ["the request fails", null, true],
    ["offline", makeMenu({ setup_done: false }), false],
  ])("does not redirect when %s", async (_name, menu, online) => {
    await start(menu, online);
    expect(target()).toEqual([]);
  });

  it("the setup screen has a Welcome header and Start reading PUTs setup_done then opens the Feed", async () => {
    await open(makeMenu({ setup_done: false }), { search: "?setup=1" });
    expect(root.textContent).toContain("Welcome");
    expect(root.textContent).toContain("Choose what you see. You can change this any time in Settings.");
    button(/Start reading/)!.click();
    await settle(700);
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body).toMatchObject({ setup_done: true });
    expect((location.replace as ReturnType<typeof vi.fn>).mock.calls.length +
      (location.assign as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(0);
  });
});
