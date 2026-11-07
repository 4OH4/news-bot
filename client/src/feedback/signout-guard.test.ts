// @vitest-environment happy-dom
//
// The guard opens a dialog, so these tests need a DOM. Every sign-out goes through `signOut`
// (#180), which awaits the guard; the dialog's buttons are found by their text.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  answerBy,
  fakeLocation,
  fillStoryState,
  installFakeApi,
  loadFeedback,
  settle,
  storyStateCounts,
  type FakeApi,
} from "./test-env";

const A = "e0e0e0e0e0e0e0e1";
const B = "e0e0e0e0e0e0e0e2";
const C = "e0e0e0e0e0e0e0e3";

type F = Awaited<ReturnType<typeof loadFeedback>>;

let loc: ReturnType<typeof fakeLocation>;

beforeEach(() => {
  loc = fakeLocation();
  vi.stubGlobal("location", loc);
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function buttons(name: RegExp): HTMLButtonElement[] {
  return [...document.querySelectorAll("button")].filter((b) =>
    name.test(`${b.textContent ?? ""} ${b.getAttribute("aria-label") ?? ""}`),
  );
}
const SEND = /send/i;
const DISCARD = /discard/i;
const CANCEL = /cancel/i;

async function click(name: RegExp): Promise<void> {
  await vi.waitFor(() => expect(buttons(name).length).toBeGreaterThan(0));
  buttons(name)[0]!.click();
}

/** Loads fresh modules, wires the guard and clearing as `startFeedback` does, plus a spy hook. */
async function setUp(): Promise<{ f: F; api: FakeApi; hook: ReturnType<typeof vi.fn> }> {
  const f = await loadFeedback();
  const api = installFakeApi("offline");
  f.installSignOutGuard();
  f.onSignOut(f.clearFeedbackData);
  const hook = vi.fn(async () => {});
  f.onSignOut(hook);
  return { f, api, hook };
}

/** Queues one event per article while offline, and fills the per-story stores. */
async function withQueued(f: F, ...articles: string[]): Promise<string[]> {
  const ids: string[] = [];
  for (const a of articles) ids.push((await f.enqueueFeedback(a, "open")).id);
  await settle();
  await fillStoryState(f);
  return ids;
}

const FULL = { seen: 1, dismissed: 1, muted: 1, liked: 1 };
const EMPTY = { seen: 0, dismissed: 0, muted: 0, liked: 0 };

describe("sign-out guard with nothing unsent", () => {
  it("lets signOut go ahead at once: no dialog, hooks run, the page is replaced with /", async () => {
    const { f, hook } = await setUp();
    await fillStoryState(f);
    await f.signOut();
    expect(buttons(SEND)).toHaveLength(0);
    expect(buttons(DISCARD)).toHaveLength(0);
    expect(hook).toHaveBeenCalledTimes(1);
    expect(await storyStateCounts(f)).toEqual(EMPTY);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });
});

describe("sign-out guard with queued events", () => {
  it("asks first, saying how many are unsent, with send, discard and cancel", async () => {
    const { f, hook } = await setUp();
    await withQueued(f, A, B);
    void f.signOut();
    await vi.waitFor(() => expect(buttons(CANCEL).length).toBeGreaterThan(0));
    expect(buttons(SEND).length).toBeGreaterThan(0);
    expect(buttons(DISCARD).length).toBeGreaterThan(0);
    expect(document.body.textContent).toMatch(/\b2\b/);
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
  });

  it("on discard, clears the queue and the per-story stores and signs out", async () => {
    const { f, api, hook } = await setUp();
    await withQueued(f, A, B);
    const done = f.signOut();
    await click(DISCARD);
    await done;
    expect(api.posts).toHaveLength(0);
    expect(await f.queuedCount()).toBe(0);
    expect(await storyStateCounts(f)).toEqual(EMPTY);
    expect(hook).toHaveBeenCalledTimes(1);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });

  it("on send with a working network, sends the events, then signs out and clears the stores", async () => {
    const { f, api, hook } = await setUp();
    const ids = await withQueued(f, A, B);
    api.answer = () => 204;
    const done = f.signOut();
    await click(SEND);
    await done;
    expect(api.posts.map((p) => p.id)).toEqual(ids);
    expect(await f.queuedCount()).toBe(0);
    expect(await storyStateCounts(f)).toEqual(EMPTY);
    expect(hook).toHaveBeenCalledTimes(1);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });

  it("on send with no network, stays signed in, keeps events and state, and says they weren't sent", async () => {
    const { f, hook } = await setUp();
    await withQueued(f, A, B);
    const done = f.signOut();
    await click(SEND);
    await done;
    await vi.waitFor(() => expect(document.body.textContent).toMatch(/n[o’']t (been )?sent/i));
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
    expect(await f.queuedCount()).toBe(2);
    expect(await storyStateCounts(f)).toEqual(FULL);
  });

  it("on cancel, runs no sign-out hook and clears nothing", async () => {
    const { f, api, hook } = await setUp();
    await withQueued(f, A, B);
    const done = f.signOut();
    await click(CANCEL);
    await done;
    expect(api.posts).toHaveLength(0);
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
    expect(await f.queuedCount()).toBe(2);
    expect(await storyStateCounts(f)).toEqual(FULL);
  });

  it("offers discard while the session needs reconnecting", async () => {
    const { f } = await setUp();
    await withQueued(f, A);
    f.reportAuthRequired();
    expect(f.status()).toBe("reconnect");
    void f.signOut();
    await vi.waitFor(() => expect(buttons(CANCEL).length).toBeGreaterThan(0));
    expect(buttons(DISCARD).length).toBeGreaterThan(0);
  });
});

describe("sign-out guard with rejected events", () => {
  /** One event the server rejected with 422, and nothing left in the queue. */
  async function withRejected(f: F, api: FakeApi): Promise<void> {
    await f.enqueueFeedback(C, "like", 1);
    await settle();
    api.answer = () => 422;
    await f.flushQueue();
    api.answer = () => "offline";
    expect(await f.queuedCount()).toBe(0);
    expect(await f.listRejected()).toHaveLength(1);
  }

  it("asks before signing out even when only rejected events are left", async () => {
    const { f, api, hook } = await setUp();
    await withRejected(f, api);
    void f.signOut();
    await vi.waitFor(() => expect(buttons(CANCEL).length).toBeGreaterThan(0));
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
  });

  it("says how many events are unsent and how many the server rejected", async () => {
    const { f, api } = await setUp();
    await withRejected(f, api);
    await withQueued(f, A, B, C);
    void f.signOut();
    await vi.waitFor(() => expect(buttons(CANCEL).length).toBeGreaterThan(0));
    expect(document.body.textContent).toMatch(/\b3\b/);
    expect(document.body.textContent).toMatch(/\b1\b/);
  });

  it("after a send leaves rejected events, stays open with only discard and cancel", async () => {
    const { f, api, hook } = await setUp();
    await withQueued(f, A, B);
    api.answer = answerBy({ [A]: 422 });
    const done = f.signOut();
    await click(SEND);
    await vi.waitFor(async () => expect(await f.queuedCount()).toBe(0));
    await vi.waitFor(() => expect(buttons(SEND)).toHaveLength(0));
    expect(buttons(DISCARD).length).toBeGreaterThan(0);
    expect(buttons(CANCEL).length).toBeGreaterThan(0);
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();

    await click(CANCEL);
    await done;
    expect(await f.listRejected()).toHaveLength(1);
    expect(await storyStateCounts(f)).toEqual(FULL);
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
  });

  it("deletes rejected events only on discard", async () => {
    const { f, api } = await setUp();
    await withRejected(f, api);
    const done = f.signOut();
    await click(DISCARD);
    await done;
    expect(await f.listRejected()).toEqual([]);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });
});

describe("sign-out guard while the account is not allowed (403)", () => {
  it("offers send and cancel but no discard; send gets 403 again and nothing is cleared", async () => {
    const { f, api, hook } = await setUp();
    const ids = await withQueued(f, A, B);
    api.answer = () => 403;
    f.reportNotAllowed();
    expect(f.status()).toBe("not-allowed");

    const done = f.signOut();
    await vi.waitFor(() => expect(buttons(CANCEL).length).toBeGreaterThan(0));
    expect(buttons(SEND).length).toBeGreaterThan(0);
    expect(buttons(DISCARD)).toHaveLength(0);

    await click(SEND);
    await done;
    expect(api.posts.map((p) => p.id)).toContain(ids[0]);
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
    expect(await f.queuedCount()).toBe(2);
    expect(await f.listRejected()).toEqual([]);
    expect(await storyStateCounts(f)).toEqual(FULL);
  });

  it("offers no discard for rejected events either", async () => {
    const { f, api } = await setUp();
    await f.enqueueFeedback(C, "like", 1);
    await settle();
    api.answer = () => 422;
    await f.flushQueue();
    api.answer = () => 403;
    f.reportNotAllowed();
    void f.signOut();
    await vi.waitFor(() => expect(buttons(CANCEL).length).toBeGreaterThan(0));
    expect(buttons(DISCARD)).toHaveLength(0);
  });
});
