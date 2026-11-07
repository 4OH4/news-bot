// @vitest-environment happy-dom
//
// Sign-out clearing as the app wires it (#255). Each test calls `startFeedback()`, which installs
// the guard and registers `clearOnSignOut`, then signs out through `signOut()` and answers the
// dialog through its buttons. Nothing here registers a hook by hand, so a deletion that stops
// working (or deletes the wrong events) fails these tests.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  answerBy,
  fakeLocation,
  fillStoryState,
  installFakeApi,
  loadApp,
  settle,
  storyStateCounts,
} from "./test-env";

const A = "e0e0e0e0e0e0e0e1";
const B = "e0e0e0e0e0e0e0e2";
const C = "e0e0e0e0e0e0e0e3";

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

/** Fresh modules, an offline server, and the app's own start-up wiring. */
async function startApp() {
  const f = await loadApp();
  const api = installFakeApi("offline");
  f.startFeedback();
  await settle();
  return { f, api };
}

const EMPTY = { seen: 0, dismissed: 0, muted: 0, liked: 0 };
const FULL = { seen: 1, dismissed: 1, muted: 1, liked: 1 };

describe("sign-out through startFeedback()", () => {
  it("discarding a rejected event deletes it and the per-story stores", async () => {
    const { f, api } = await startApp();
    await f.enqueueFeedback(A, "like", 1);
    await settle();
    api.answer = () => 422;
    await f.flushQueue();
    api.answer = () => "offline";
    await fillStoryState(f);
    expect(await f.listRejected()).toHaveLength(1);

    const done = f.signOut();
    await click(DISCARD);
    await done;

    expect(await f.listRejected()).toEqual([]);
    expect(await storyStateCounts(f)).toEqual(EMPTY);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });

  it("deletes the events the dialog last showed, and keeps one enqueued after that", async () => {
    const { f, api } = await startApp();
    await f.enqueueFeedback(A, "open");
    await f.enqueueFeedback(B, "open");
    await settle();
    await fillStoryState(f);

    api.answer = answerBy({ [A]: 422 }, 204);
    const done = f.signOut();
    await click(SEND);
    await vi.waitFor(async () => expect(await f.queuedCount()).toBe(0));
    await vi.waitFor(() => expect(buttons(SEND)).toHaveLength(0));
    expect(buttons(DISCARD).length).toBeGreaterThan(0);
    expect(buttons(CANCEL).length).toBeGreaterThan(0);
    expect(api.posts.map((p) => p.id)).toHaveLength(2);
    expect(await f.listRejected()).toHaveLength(1);

    // C is written while the dialog is open, so the dialog never showed it.
    api.answer = () => "offline";
    const late = await f.enqueueFeedback(C, "open");
    await settle();
    await click(DISCARD);
    await done;

    expect(await f.listRejected()).toEqual([]);
    const db = await f.openFeedbackDb();
    const queued = (await db.getAll("queue")).map((row: { id: string }) => row.id);
    expect(queued).toEqual([late.id]);
    expect(await storyStateCounts(f)).toEqual(EMPTY);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });

  it("a send with no network deletes nothing, and closing the dialog leaves the queue unchanged", async () => {
    const { f } = await startApp();
    await f.enqueueFeedback(A, "open");
    await settle();
    await fillStoryState(f);

    const done = f.signOut();
    await click(SEND);
    await vi.waitFor(() => expect(document.body.textContent).toMatch(/n[o’']t (been )?sent/i));
    expect(await f.queuedCount()).toBe(1);
    expect(await storyStateCounts(f)).toEqual(FULL);
    expect(loc.replace).not.toHaveBeenCalled();

    // The dialog has already answered "stay signed in"; its only button is now OK.
    await click(/^ok\b/i);
    await done;
    expect(await f.queuedCount()).toBe(1);
    expect(await f.listRejected()).toEqual([]);
    expect(await storyStateCounts(f)).toEqual(FULL);
    expect(loc.replace).not.toHaveBeenCalled();
  });
});
