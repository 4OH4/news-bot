// @vitest-environment happy-dom
//
// #339: "Mute source" sets the source to Hide on the server (`PUT /api/preferences`) and Undo
// sets it back; offline, the change waits in a small localStorage outbox. Invented data only.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installPrefs, makeMenu } from "../../test/preferences-env";
import { loadStores, makeItem, queuedEvents, settle } from "../../test/test-env";
import { installStorage } from "./test-env";

const BBC = makeItem("b1b1b1b1b1b1b1b1", { source_id: "bbc-news", domain: "bbc.example.test" });
const BBC_TWO = makeItem("b2b2b2b2b2b2b2b2", { source_id: "bbc-news", domain: "bbc.example.test" });
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

const outbox = (): Record<string, string> =>
  JSON.parse(localStorage.getItem("pending-source-states") ?? "{}");

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
    expect(outbox()).toEqual({});
    // Hidden for the rest of this page; a new page leaves the filtering to the server.
    expect(await hidden.isHidden(BBC)).toBe(true);
    expect(await (await load({ version: 3 })).hidden.isHidden(BBC)).toBe(false);
  });
});

describe("online mute hides the whole source for the page (#406)", () => {
  it("another story from the muted source is hidden after the PUT, until Undo or a new page", async () => {
    const { fake, hidden } = await load();
    await hidden.muteSource(BBC);
    await settle();
    expect(fake.puts).toHaveLength(1);
    expect(await hidden.isHidden(BBC_TWO)).toBe(true);
    expect(await hidden.isHidden(WIRED)).toBe(false);

    await hidden.unmuteSource(BBC);
    await settle();
    expect(await hidden.isHidden(BBC_TWO)).toBe(false);

    await hidden.muteSource(BBC);
    await settle();
    expect(await (await load()).hidden.isHidden(BBC_TWO)).toBe(false);
  });
});

describe("the outbox can't be blocked (#406)", () => {
  it("a source the menu doesn't offer is left out of the PUT and leaves the outbox", async () => {
    localStorage.setItem("pending-source-states", JSON.stringify({ "invented-gone": "hide" }));
    const { fake, hidden } = await load();

    await hidden.muteSource(BBC);
    await settle();

    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]!.body.sources).toEqual({ "bbc-news": "hide" });
    expect(outbox()).toEqual({});
  });

  it("a 422 clears the entries that save sent, and a later mute is saved normally", async () => {
    const { fake, hidden } = await load();
    fake.putAnswers = [{ status: 422 }, { status: 200, body: makeMenu({ version: 3 }) }];

    await hidden.muteSource(BBC);
    await settle();
    expect(outbox()).toEqual({});

    await hidden.muteSource(WIRED);
    await settle();
    expect(fake.puts.at(-1)!.body.sources).toEqual({ wired: "hide" });
    expect(outbox()).toEqual({});
  });

  it.each([
    ["the server answers 503", 503],
    ["the server is unreachable", "offline"],
  ])("when %s, the change stays in the outbox", async (_why, failure) => {
    const { fake, hidden } = await load();
    if (failure === "offline") fake.router.online = false;
    else fake.putAnswers = [{ status: failure as number }];

    await hidden.muteSource(BBC);
    await settle();

    expect(outbox()).toEqual({ "bbc-news": "hide" });
  });

  it("migrateMutedSources has sent the outbox by the time it resolves", async () => {
    localStorage.setItem("muted-migrated", "2026-01-01T00:00:00.000Z");
    localStorage.setItem("pending-source-states", JSON.stringify({ "bbc-news": "hide" }));
    const { fake, hidden } = await load();

    await hidden.migrateMutedSources();

    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]!.body.sources).toEqual({ "bbc-news": "hide" });
    expect(outbox()).toEqual({});
  });

  it("an Undo tapped while the mute's PUT is held stays in the outbox, then reaches the server", async () => {
    const { fake, hidden } = await load();
    let release!: () => void;
    fake.putGate = new Promise<void>((r) => (release = r));

    const muted = hidden.muteSource(BBC);
    await settle();
    expect(fake.puts).toHaveLength(1);
    const undone = hidden.unmuteSource(BBC);
    expect(outbox()).toEqual({ "bbc-news": "show" });

    // Hold the Undo's own PUT too, so the state between the two saves can be read.
    let releaseUndo!: () => void;
    fake.putGate = new Promise<void>((r) => (releaseUndo = r));
    release();
    await settle();
    expect(fake.puts).toHaveLength(2);
    expect(outbox()).toEqual({ "bbc-news": "show" });

    releaseUndo();
    await Promise.all([muted, undone]);
    await settle();
    expect(fake.puts.at(-1)!.body.sources).not.toHaveProperty("bbc-news");
    expect(outbox()).toEqual({});
  });
});
