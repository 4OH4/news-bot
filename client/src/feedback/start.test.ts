import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeLocation, installFakeApi, loadFeedback, settle, STORY_KEY } from "./test-env";

const A = "d0d0d0d0d0d0d0d1";

/** Stand-ins for the page: `online` fires on window, `visibilitychange` on document. */
let win: EventTarget;
let doc: EventTarget & { visibilityState: DocumentVisibilityState };
let loc: ReturnType<typeof fakeLocation>;
const nav = { onLine: true };

beforeEach(() => {
  win = new EventTarget();
  doc = Object.assign(new EventTarget(), { visibilityState: "visible" as DocumentVisibilityState });
  loc = fakeLocation();
  nav.onLine = true;
  vi.stubGlobal("window", win);
  vi.stubGlobal("document", doc);
  vi.stubGlobal("addEventListener", win.addEventListener.bind(win));
  vi.stubGlobal("removeEventListener", win.removeEventListener.bind(win));
  vi.stubGlobal("navigator", nav);
  vi.stubGlobal("location", loc);
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function becomeVisible(): void {
  doc.visibilityState = "hidden";
  doc.dispatchEvent(new Event("visibilitychange"));
  doc.visibilityState = "visible";
  doc.dispatchEvent(new Event("visibilitychange"));
}

describe("startFeedback", () => {
  it("flushes the queue on start", async () => {
    const f = await loadFeedback();
    const api = installFakeApi("offline");
    const event = await f.enqueueFeedback(A, "open");
    await settle();
    api.answer = () => 204;
    f.startFeedback();
    await vi.waitFor(() => expect(api.posts.map((p) => p.id)).toEqual([event.id]));
    await vi.waitFor(async () => expect(await f.queuedCount()).toBe(0));
  });

  it("with no network, keeps an enqueued event, and sends it once the online event fires", async () => {
    const f = await loadFeedback();
    const api = installFakeApi("offline");
    f.startFeedback();
    nav.onLine = false;
    const event = await f.enqueueFeedback(A, "dismiss");
    expect(await f.queuedCount()).toBe(1);
    await settle();
    expect(await f.queuedCount()).toBe(1);

    nav.onLine = true;
    api.answer = () => 204;
    win.dispatchEvent(new Event("online"));
    await vi.waitFor(() => expect(api.posts.map((p) => p.id)).toEqual([event.id]));
    await vi.waitFor(async () => expect(await f.queuedCount()).toBe(0));
  });

  it("flushes when the page becomes visible", async () => {
    const f = await loadFeedback();
    const api = installFakeApi("offline");
    f.startFeedback();
    const event = await f.enqueueFeedback(A, "open");
    await settle();
    api.answer = () => 204;
    becomeVisible();
    await vi.waitFor(() => expect(api.posts.map((p) => p.id)).toEqual([event.id]));
  });

  it("on a lapsed session keeps the event and shows Reconnect, then sends it after reconnecting", async () => {
    const f = await loadFeedback();
    const api = installFakeApi("offline");
    f.startFeedback();
    await f.markSeen(STORY_KEY);
    const event = await f.enqueueFeedback(A, "open");
    await settle();

    // The network returns, but the IAP session has lapsed.
    api.answer = () => 401;
    win.dispatchEvent(new Event("online"));
    await vi.waitFor(() => expect(api.posts).toHaveLength(1));
    await settle();
    expect(await f.queuedCount()).toBe(1);
    expect(f.status()).toBe("reconnect");
    // A 401 is not a sign-out the user chose: nothing local is cleared.
    expect(await f.isSeen(STORY_KEY)).toBe(true);
    expect(loc.replace).not.toHaveBeenCalled();

    // The owner reconnects and comes back to the page.
    api.answer = () => 204;
    becomeVisible();
    await vi.waitFor(() => expect(api.posts.at(-1)?.id).toBe(event.id));
    await vi.waitFor(async () => expect(await f.queuedCount()).toBe(0));
  });

  it("registers clearing on sign-out, so an empty queue signs out and clears per-story state", async () => {
    const f = await loadFeedback();
    installFakeApi(204);
    f.startFeedback();
    await settle();
    await f.markSeen(STORY_KEY);
    await f.signOut();
    expect(await f.isSeen(STORY_KEY)).toBe(false);
    expect(loc.replace).toHaveBeenCalledTimes(1);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });
});
