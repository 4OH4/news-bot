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
} from "../reader/test-env";

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
