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
  loadApp,
  settle,
  storyStateCounts,
  type FakeApi,
} from "./test-env";
import type { FeedbackPayload } from "./event";

const A = "e0e0e0e0e0e0e0e1";
const B = "e0e0e0e0e0e0e0e2";
const C = "e0e0e0e0e0e0e0e3";

type F = Awaited<ReturnType<typeof loadApp>>;

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
  const f = await loadApp();
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

/**
 * An event whose `enqueueFeedback` resolved was promised to be stored, so after the sign-out it
 * must still be in `queue` or `rejected`, or have reached the server. An enqueue that was refused
 * (rejected) or held back until the page leaves (still pending) promised nothing, so either is
 * allowed: the fix may make the check and the clear one step, or block enqueues while signing out.
 */
async function expectNotLost(f: F, api: FakeApi, late: Promise<unknown>): Promise<void> {
  const result = await Promise.race([late, settle(300).then(() => "pending" as const)]);
  if (result === "refused" || result === "pending") return;
  const { id } = result as FeedbackPayload;
  const db = await f.openFeedbackDb();
  const queued = (await db.getAll("queue")).map((row: { id: string }) => row.id);
  const rejected = (await f.listRejected()).map((row: { id: string }) => row.id);
  const posted = api.posts.map((p) => p.id);
  expect([...queued, ...rejected, ...posted]).toContain(id);
}

describe("sign-out guard and an event enqueued while signing out", () => {
  /**
   * Registers a hook that enqueues an event, then runs the real `startFeedback` wiring. The hook
   * runs after the guard has said yes and before the clearing hook: the gap between the guard's
   * check and `clearFeedbackData`.
   */
  async function setUpWithLateEvent(): Promise<{ f: F; api: FakeApi; late: () => Promise<unknown> }> {
    const f = await loadApp();
    const api = installFakeApi("offline");
    let late: Promise<unknown> = Promise.resolve("not enqueued");
    f.onSignOut(async () => {
      late = f.enqueueFeedback(C, "like", 1).catch(() => "refused" as const);
      // Let the enqueue finish if it can, without waiting on a fix that holds it back.
      await Promise.race([late, settle(100)]);
    });
    f.startFeedback();
    await settle();
    return { f, api, late: () => late };
  }

  it("with an empty queue, does not clear an event enqueued after the guard's check", async () => {
    const { f, api, late } = await setUpWithLateEvent();
    await f.signOut();
    await expectNotLost(f, api, late());
  });

  it("after send emptied the queue, does not clear an event enqueued after the guard's check", async () => {
    const { f, api, late } = await setUpWithLateEvent();
    await withQueued(f, A);
    // A is accepted; the late event can't be sent, so only the queue can keep it.
    api.answer = answerBy({ [A]: 204 }, "offline");
    const done = f.signOut();
    await click(SEND);
    await done;
    expect(api.posts.map((p) => p.body.article_id)).toContain(A);
    await expectNotLost(f, api, late());
  });

  it("does not clear an event enqueued just after signOut was called", async () => {
    const f = await loadApp();
    const api = installFakeApi("offline");
    f.startFeedback();
    await settle();
    const done = f.signOut();
    const late = f.enqueueFeedback(C, "open").catch(() => "refused" as const);
    await done;
    await expectNotLost(f, api, late);
  });
});

describe("sign-out guard when the send never answers", () => {
  it("once the send's time limit runs out, stays signed in, keeps everything and says they weren't sent", async () => {
    const limits: AbortController[] = [];
    vi.spyOn(AbortSignal, "timeout").mockImplementation(() => {
      const controller = new AbortController();
      limits.push(controller);
      return controller.signal;
    });
    const { f, api, hook } = await setUp();
    await withQueued(f, A, B);
    api.answer = () => "hang";
    const before = api.attempts;
    const done = f.signOut();
    await click(SEND);
    await vi.waitFor(() => expect(api.attempts).toBeGreaterThan(before));
    expect(limits.length).toBeGreaterThan(0);
    for (const c of limits) c.abort(new DOMException("The operation timed out.", "TimeoutError"));

    await done;
    await vi.waitFor(() => expect(document.body.textContent).toMatch(/n[o’']t (been )?sent/i));
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
    expect(await f.queuedCount()).toBe(2);
    expect(await storyStateCounts(f)).toEqual(FULL);
  });
});
