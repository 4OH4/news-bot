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
import { outboxEntries } from "./test-env";

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

// Leaving the reader is a full page load: no unmount runs and the queue write may never finish.
// Since #254 the reading goes straight to enqueueFeedback, whose outbox entry is the copy that
// survives; the old `newsfeed-pending-reads` hand-off and `movePendingReads` are gone.
describe("a reading that outlives its page", () => {
  const PENDING = "newsfeed-pending-reads";

  beforeEach(() => {
    vi.resetModules();
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
    vi.useFakeTimers();
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

  it("calls enqueueFeedback inside the pagehide and writes no newsfeed-pending-reads copy", async () => {
    vi.useFakeTimers();
    // This page's queue write never finishes, as when the page unloads mid-write.
    const stuck = vi.fn(() => new Promise<never>(() => {}));
    vi.doMock("./queue", () => ({ enqueueFeedback: stuck }));
    const reading = await import("./reading");
    reading.startReading(ITEM);
    await vi.advanceTimersByTimeAsync(8_000);

    window.dispatchEvent(new Event("pagehide"));

    expect(stuck.mock.calls).toEqual([[ITEM.id, "read", 8]]);
    expect(localStorage.getItem(PENDING)).toBeNull();
    expect(reading).not.toHaveProperty("movePendingReads");
  });

  it("a page restored from the back/forward cache sends no second read for the opening", async () => {
    vi.useFakeTimers();
    const enqueue = vi.fn(async () => ({}));
    vi.doMock("./queue", () => ({ enqueueFeedback: enqueue }));
    const { startReading } = await import("./reading");
    startReading(ITEM);
    await vi.advanceTimersByTimeAsync(7_000);
    setVisibility("hidden");
    window.dispatchEvent(new Event("pagehide"));
    // Restored: shown again and read for a while, then left once more.
    window.dispatchEvent(new Event("pageshow"));
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(20_000);
    window.dispatchEvent(new Event("pagehide"));
    await vi.advanceTimersByTimeAsync(0);
    expect(enqueue.mock.calls).toEqual([[ITEM.id, "read", 7]]);
  });

  it("with the real queue, the read event is in the outbox as the pagehide returns", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-07T10:00:00Z"));
    installRouter({ online: false });
    await loadStores();
    const { startReading } = await import("./reading");
    startReading(ITEM);
    vi.setSystemTime(new Date("2026-11-07T10:00:07Z"));

    window.dispatchEvent(new Event("pagehide"));

    expect(outboxEntries()).toEqual([
      expect.objectContaining({ article_id: ITEM.id, event: "read", value: 7 }),
    ]);
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
