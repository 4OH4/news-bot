// @vitest-environment happy-dom
//
// Choices #216 left open: a short reading that ends with a hide doesn't use up the opening's one
// `read` event, and a like stores the reader's text, kept, when nothing was stored yet.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  articleRecord,
  bodyFor,
  installRouter,
  loadStores,
  makeItem,
  resetVisibility,
  setVisibility,
} from "../../test/test-env";

const ITEM = makeItem("0f1e2d3c4b5a6978");

afterEach(() => {
  vi.doUnmock("./queue");
  vi.useRealTimers();
  resetVisibility();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("startReading after a short reading", () => {
  let enqueue: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    setVisibility("visible", false);
    enqueue = vi.fn(async () => ({}));
    vi.doMock("./queue", () => ({ enqueueFeedback: enqueue }));
  });

  it("3 seconds, hidden, then 10 more seconds sends one event for the second reading", async () => {
    const { startReading } = await import("./reading");
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(3_000);
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(5_000);
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(10_000);
    stop();
    stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(enqueue.mock.calls).toEqual([[ITEM.id, "read", 10]]);
  });
});

// Leaving the reader is a full page load: no unmount runs and the queue write may never finish,
// so the reading is handed to the next page through localStorage.
describe("a reading that outlives its page", () => {
  const PENDING = "newsfeed-pending-reads";

  beforeEach(() => {
    vi.resetModules();
    vi.useFakeTimers();
    setVisibility("visible", false);
    localStorage.clear();
    for (const m of ["error", "warn", "log", "info"] as const) {
      vi.spyOn(console, m).mockImplementation(() => {});
    }
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("pagehide alone ends the reading and sends it", async () => {
    const enqueue = vi.fn(async () => ({}));
    vi.doMock("./queue", () => ({ enqueueFeedback: enqueue }));
    const { startReading } = await import("./reading");
    startReading(ITEM);
    await vi.advanceTimersByTimeAsync(9_000);
    window.dispatchEvent(new Event("pagehide"));
    await vi.advanceTimersByTimeAsync(0);
    expect(enqueue.mock.calls).toEqual([[ITEM.id, "read", 9]]);
    expect(localStorage.getItem(PENDING)).toBeNull();
  });

  it("a queue write the unload cut off is queued by the next page, once", async () => {
    // This page's queue write never finishes, as when the page unloads mid-write.
    const stuck = vi.fn(() => new Promise<never>(() => {}));
    vi.doMock("./queue", () => ({ enqueueFeedback: stuck }));
    const first = await import("./reading");
    first.startReading(ITEM);
    await vi.advanceTimersByTimeAsync(8_000);
    window.dispatchEvent(new Event("pagehide"));
    expect(localStorage.getItem(PENDING)).not.toBeNull();

    // The next page.
    vi.resetModules();
    const enqueue = vi.fn(async () => ({}));
    vi.doMock("./queue", () => ({ enqueueFeedback: enqueue }));
    const next = await import("./reading");
    await next.movePendingReads();
    expect(enqueue.mock.calls).toEqual([[ITEM.id, "read", 8]]);
    expect(localStorage.getItem(PENDING)).toBeNull();
    await next.movePendingReads();
    expect(enqueue).toHaveBeenCalledTimes(1);
  });

  it("a pending reading the queue refuses stays for the next start", async () => {
    localStorage.setItem(PENDING, JSON.stringify({ o1: { article_id: ITEM.id, value: 12 } }));
    const failing = vi.fn(async () => {
      throw new Error("invented storage failure");
    });
    vi.doMock("./queue", () => ({ enqueueFeedback: failing }));
    const { movePendingReads } = await import("./reading");
    await movePendingReads();
    expect(failing).toHaveBeenCalledTimes(1);
    expect(JSON.parse(localStorage.getItem(PENDING)!)).toEqual({
      o1: { article_id: ITEM.id, value: 12 },
    });
  });

  it("drops a malformed pending entry without queuing it", async () => {
    localStorage.setItem(PENDING, JSON.stringify({ o1: { article_id: 7, value: "x" } }));
    const enqueue = vi.fn(async () => ({}));
    vi.doMock("./queue", () => ({ enqueueFeedback: enqueue }));
    const { movePendingReads } = await import("./reading");
    await movePendingReads();
    expect(enqueue).not.toHaveBeenCalled();
    expect(localStorage.getItem(PENDING)).toBeNull();
  });
});

describe("setLiked with the reader's text", () => {
  beforeEach(() => {
    for (const m of ["error", "warn", "log", "info"] as const) {
      vi.spyOn(console, m).mockImplementation(() => {});
    }
  });

  it("stores the given text as kept when none is stored", async () => {
    installRouter({ online: false });
    const s = await loadStores();
    const liked = await import("./liked");
    await liked.setLiked(ITEM, true, bodyFor(ITEM.id, "shown"));
    expect(await s.getArticleHtml(ITEM.id)).toBe(bodyFor(ITEM.id, "shown"));
    expect((await articleRecord(ITEM.id))?.keep).toBe(true);
  });

  it("keeps the stored text rather than replacing it", async () => {
    installRouter({ online: false });
    const s = await loadStores();
    await s.putArticleHtml(ITEM.id, bodyFor(ITEM.id, "stored"));
    const liked = await import("./liked");
    await liked.setLiked(ITEM, true, bodyFor(ITEM.id, "shown"));
    expect(await s.getArticleHtml(ITEM.id)).toBe(bodyFor(ITEM.id, "stored"));
    expect((await articleRecord(ITEM.id))?.keep).toBe(true);
  });
});
