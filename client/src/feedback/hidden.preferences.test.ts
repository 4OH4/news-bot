// @vitest-environment happy-dom
//
// #339: "Mute source" sets the source to Hide on the server (`PUT /api/preferences`) and Undo
// sets it back; offline, the change waits in a small localStorage outbox. Invented data only.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installPrefs, makeMenu } from "../../test/preferences-env";
import { loadStores, makeItem, queuedEvents, settle } from "../../test/test-env";
import { installStorage } from "./test-env";

const BBC = makeItem("b1b1b1b1b1b1b1b1", { source_id: "bbc-news", domain: "bbc.example.test" });
const WIRED = makeItem("c1c1c1c1c1c1c1c1", { source_id: "wired", domain: "wired.example.test" });

beforeEach(() => {
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  installStorage();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function load(over: Parameters<typeof makeMenu>[0] = {}) {
  const fake = installPrefs(makeMenu(over), { online: true });
  const stores = await loadStores();
  const hidden = await import("./hidden");
  return { fake, stores, hidden };
}

describe("muteSource and unmuteSource set the source preference", () => {
  it("mute queues mute_source and PUTs the source as hide; undo PUTs it back to show", async () => {
    const { fake, stores, hidden } = await load({ version: 4, states: { wired: "fewer" } });
    // 401 is a retryable answer, so the queue keeps its events for the assertions below; with the
    // default 204 the flush would send and clear them before we look.
    fake.router.feedback = 401;

    await hidden.muteSource(BBC);
    await settle();

    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]!.body).toMatchObject({
      version: 4,
      sources: { "bbc-news": "hide", wired: "fewer" },
    });
    expect((await queuedEvents(stores)).map((e) => [e.article_id, e.event, e.value])).toEqual([
      [BBC.id, "mute_source", 1],
    ]);

    await hidden.unmuteSource(BBC);
    await settle();

    expect(fake.puts).toHaveLength(2);
    expect(fake.puts[1]!.body.sources).toEqual({ wired: "fewer" });
    expect((await queuedEvents(stores)).at(-1)).toMatchObject({ event: "mute_source", value: 0 });
  });

  it("offline: the source stays hidden on the phone, and the change waits in pending-source-states, written inside the tap", async () => {
    const { fake, hidden } = await load();
    fake.router.online = false;

    const done = hidden.muteSource(BBC);
    expect(JSON.parse(localStorage.getItem("pending-source-states") ?? "null")).toBeTruthy();
    await done;

    expect(await hidden.isHidden(BBC)).toBe(true);
    expect(await hidden.isHidden(WIRED)).toBe(false);
    expect(fake.puts).toEqual([]);
  });

  it("back online, the pending change goes out with the next successful save, and the phone stops filtering", async () => {
    const { fake, hidden } = await load({ version: 2 });
    fake.router.online = false;
    await hidden.muteSource(BBC);
    fake.router.online = true;

    await hidden.muteSource(WIRED);
    await settle();

    expect(fake.puts.at(-1)!.body.sources).toEqual({ "bbc-news": "hide", wired: "hide" });
    expect(await hidden.isHidden(BBC)).toBe(false);
  });
});
