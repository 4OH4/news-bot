// @vitest-environment happy-dom
//
// #338: the preferences hook on its own, against the fake API: debounced saves, the 409
// re-apply, putting rows back on failure, and the stale mark a save leaves for the lists.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installPrefs, makeMenu, type FakePrefs } from "../../test/preferences-env";
import { loadStores, locationAt, makeFeed, makeItem, settle } from "../../test/test-env";
import { OFFLINE_MESSAGE, sourceKey, topicKey, usePreferences, type UsePreferences } from "./usePreferences";

let root: HTMLElement;
let fake: FakePrefs;
let hook: UsePreferences;

function Probe() {
  hook = usePreferences();
  return null;
}

async function mount(menu = makeMenu(), online = true): Promise<void> {
  fake = installPrefs(menu, { online });
  render(h(Probe, null), root);
  await settle();
}

beforeEach(() => {
  vi.stubGlobal("__APP_VERSION__", "test");
  vi.stubGlobal("location", locationAt("/settings/preferences"));
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
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("usePreferences", () => {
  it("loads the menu and reads each row's state from it", async () => {
    await mount(makeMenu({ states: { wired: "fewer", sport: "more" } }));
    expect(hook.menu?.sections.map((s) => s.id)).toEqual(["news", "tech", "science", "more", "long-reads"]);
    expect(hook.get(sourceKey("wired"))).toBe("fewer");
    expect(hook.get(topicKey("sport"))).toBe("more");
    expect(hook.get(sourceKey("bbc-news"))).toBe("show");
    expect(hook.message).toBe("");
  });

  it("shows a change at once and sends rapid changes as one debounced PUT", async () => {
    await mount(makeMenu({ version: 4 }));
    hook.set(sourceKey("bbc-news"), "hide");
    expect(hook.get(sourceKey("bbc-news"))).toBe("hide");
    hook.set(sourceKey("wired"), "fewer");
    hook.set(topicKey("sport"), "more");
    await settle(200);
    expect(fake.puts).toHaveLength(0);
    await settle(500);
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body).toEqual({
      version: 4,
      sources: { "bbc-news": "hide", wired: "fewer" },
      topics: { sport: "more" },
      setup_done: true,
    });
  });

  it("uses the version from each save for the next one", async () => {
    await mount(makeMenu({ version: 4 }));
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(700);
    hook.set(sourceKey("bbc-news"), "show");
    await settle(700);
    expect(fake.puts.map((p) => p.body.version)).toEqual([4, 5]);
    expect(fake.puts[1]?.body.sources).toEqual({});
  });

  it("on a 409 lays the owner's change over the newer state and saves once more", async () => {
    await mount();
    fake.putAnswers = [
      { status: 409, body: makeMenu({ version: 6, states: { wired: "fewer", "bbc-news": "more" } }) },
      { status: 200, body: makeMenu({ version: 7 }) },
    ];
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(800);
    expect(fake.puts).toHaveLength(2);
    expect(fake.puts[1]?.body).toMatchObject({
      version: 6,
      sources: { "bbc-news": "hide", wired: "fewer" },
    });
    expect(hook.get(sourceKey("wired"))).toBe("fewer");
    expect(hook.get(sourceKey("bbc-news"))).toBe("hide");
    expect(hook.message).toBe("");
  });

  it("after two 409s in a row puts the row back and stops", async () => {
    await mount();
    fake.putAnswers = [{ status: 409, body: makeMenu({ version: 6 }) }];
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(800);
    expect(fake.puts).toHaveLength(2);
    expect(hook.get(sourceKey("bbc-news"))).toBe("show");
    expect(hook.message).not.toBe("");
  });

  it("on a 500 puts the row back and says so, keeping rows saved before", async () => {
    await mount();
    hook.set(sourceKey("wired"), "fewer");
    await settle(700);
    fake.putAnswers = [{ status: 200 }, { status: 500 }];
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(700);
    expect(hook.get(sourceKey("bbc-news"))).toBe("show");
    expect(hook.get(sourceKey("wired"))).toBe("fewer");
    expect(hook.message).toMatch(/could not save/i);
  });

  it("offline on load has no menu and says changes need a connection", async () => {
    await mount(makeMenu(), false);
    expect(hook.menu).toBeNull();
    expect(hook.message).toBe(OFFLINE_MESSAGE);
    expect(fake.puts).toHaveLength(0);
  });

  /** The `keepalive` flag of each PUT to /api/preferences that reached fetch. */
  function putKeepalive(): (boolean | undefined)[] {
    return vi
      .mocked(globalThis.fetch)
      .mock.calls.filter(([, init]) => init?.method === "PUT")
      .map(([, init]) => init?.keepalive);
  }

  it("on pagehide within the debounce sends the change at once, with keepalive", async () => {
    await mount(makeMenu({ version: 4 }));
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(100);
    window.dispatchEvent(new Event("pagehide"));
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body).toMatchObject({ version: 4, sources: { "bbc-news": "hide" } });
    expect(putKeepalive()).toEqual([true]);
    await settle(700);
    expect(fake.puts).toHaveLength(1);
  });

  it("on unmount within the debounce sends the change at once", async () => {
    await mount();
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(100);
    render(null, root);
    await settle(700);
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body.sources).toEqual({ "bbc-news": "hide" });
  });

  it("on pagehide with nothing pending sends nothing", async () => {
    await mount();
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(700);
    window.dispatchEvent(new Event("pagehide"));
    await settle();
    expect(fake.puts).toHaveLength(1);
    // Every save sets keepalive now (#395), not only the one sent as the page hides.
    expect(putKeepalive()).toEqual([true]);
  });

  it("a failed save keeps a row the owner changed again while it was in flight", async () => {
    await mount();
    fake.putAnswers = [{ status: 500 }, { status: 200 }];
    hook.set(sourceKey("bbc-news"), "hide");
    const first = hook.finishSetup();
    hook.set(sourceKey("bbc-news"), "more");
    await first;
    expect(hook.get(sourceKey("bbc-news"))).toBe("more");
    await settle(700);
    expect(fake.puts.at(-1)?.body.sources).toEqual({ "bbc-news": "more" });
  });

  it("after a second 409 puts the row back to the newer state, not the one before", async () => {
    await mount();
    fake.putAnswers = [{ status: 409, body: makeMenu({ version: 6, states: { "bbc-news": "more" } }) }];
    hook.set(sourceKey("bbc-news"), "hide");
    await settle(800);
    expect(fake.puts).toHaveLength(2);
    expect(hook.get(sourceKey("bbc-news"))).toBe("more");
  });

  it("finishSetup keeps setup_done true across a 409 that says false", async () => {
    await mount(makeMenu({ setup_done: false }));
    fake.putAnswers = [
      { status: 409, body: makeMenu({ version: 6, setup_done: false }) },
      { status: 200, body: makeMenu({ version: 7 }) },
    ];
    await hook.finishSetup();
    expect(fake.puts.map((p) => p.body.setup_done)).toEqual([true, true]);
  });

  it("finishSetup saves at once with setup_done true", async () => {
    await mount(makeMenu({ setup_done: false }));
    await hook.finishSetup();
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body.setup_done).toBe(true);
  });
});

describe("the stale mark", () => {
  /** Opens News with a stored list and returns the If-None-Match its refresh sent. */
  async function openNews(saveFirst: boolean): Promise<string | null> {
    fake = installPrefs(makeMenu(), { feed: makeFeed([makeItem("a1b2c3d4e5f60718")]) });
    await loadStores();
    const stores = await import("../store/db");
    await stores.saveFeed(makeFeed([makeItem("a1b2c3d4e5f60718")]), '"etag-1"', "news");
    if (saveFirst) {
      render(h(Probe, null), root);
      await settle();
      hook.set(sourceKey("bbc-news"), "hide");
      await settle(700);
      expect(fake.puts).toHaveLength(1);
      render(null, root);
    }
    vi.stubGlobal("location", locationAt("/news"));
    const { App } = await import("../App");
    render(h(App, null), root);
    await settle();
    return fake.feedConditions.at(-1) ?? null;
  }

  it("without a save, a stored list is revalidated with its etag", async () => {
    expect(await openNews(false)).toBe('"etag-1"');
  });

  it("after a save, a list stored before it is fetched in full", async () => {
    expect(await openNews(true)).toBeNull();
  });
});
