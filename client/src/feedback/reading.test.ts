// @vitest-environment happy-dom
//
// Reading time (#216): one `read` event per reader opening, with the whole seconds the article
// was visible, sent through the feedback queue, and none under 5 seconds.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeItem, resetVisibility, setVisibility } from "../../test/test-env";

const ITEM = makeItem("d4e5f6a7b8c9d0e1");

let enqueue: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.resetModules();
  vi.useFakeTimers();
  setVisibility("visible", false);
  enqueue = vi.fn(async (articleId: string, event: string, value?: number) => ({
    id: "0".repeat(32),
    article_id: articleId,
    event,
    value: value ?? null,
    created_at: new Date().toISOString(),
  }));
  // Every event must go through the queue's enqueueFeedback.
  vi.doMock("./queue", () => ({ enqueueFeedback: enqueue }));
});

afterEach(() => {
  vi.doUnmock("./queue");
  vi.useRealTimers();
  resetVisibility();
  vi.restoreAllMocks();
});

async function loadReading() {
  return import("./reading");
}

/** The `read` events enqueued so far, as [article_id, value]. */
function readEvents(): [unknown, unknown][] {
  return enqueue.mock.calls.filter((c) => c[1] === "read").map((c) => [c[0], c[2]]);
}

describe("startReading", () => {
  it("a reader open for 40 seconds with the app hidden for 20 of them sends one read event of about 20", async () => {
    const { startReading } = await loadReading();
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(20_000);
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(20_000);
    stop();
    await vi.advanceTimersByTimeAsync(0);

    const events = readEvents();
    expect(events).toHaveLength(1);
    const [articleId, value] = events[0]!;
    expect(articleId).toBe(ITEM.id);
    expect(Number.isInteger(value)).toBe(true);
    expect(value as number).toBeGreaterThanOrEqual(19);
    expect(value as number).toBeLessThanOrEqual(21);
  });

  it("a reader open for 2 seconds sends no read event when left", async () => {
    const { startReading } = await loadReading();
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(2_000);
    stop();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(readEvents()).toEqual([]);
  });

  it("hiding the app after 4 visible seconds sends nothing either: the 5-second floor applies to a hide too", async () => {
    const { startReading } = await loadReading();
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(4_000);
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(30_000);
    stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(readEvents()).toEqual([]);
  });

  it("sends the event when the app is hidden, without waiting for the reader to close", async () => {
    const { startReading } = await loadReading();
    startReading(ITEM);
    await vi.advanceTimersByTimeAsync(15_000);
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(0);

    const events = readEvents();
    expect(events).toHaveLength(1);
    expect(events[0]![0]).toBe(ITEM.id);
    expect(events[0]![1] as number).toBeGreaterThanOrEqual(14);
    expect(events[0]![1] as number).toBeLessThanOrEqual(16);
  });

  it("the value is whole seconds visible", async () => {
    const { startReading } = await loadReading();
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(12_700);
    stop();
    await vi.advanceTimersByTimeAsync(0);

    const events = readEvents();
    expect(events).toHaveLength(1);
    const value = events[0]![1];
    expect(Number.isInteger(value)).toBe(true);
    expect([12, 13]).toContain(value);
  });

  it("coming back after a hide sends no second event for the same reader opening", async () => {
    const { startReading } = await loadReading();
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(10_000);
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(5_000);
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(30_000);
    stop();
    await vi.advanceTimersByTimeAsync(0);

    expect(readEvents()).toHaveLength(1);
  });

  it("does not count time before the app is visible", async () => {
    setVisibility("hidden", false);
    const { startReading } = await loadReading();
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(60_000);
    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(8_000);
    stop();
    await vi.advanceTimersByTimeAsync(0);

    const events = readEvents();
    expect(events).toHaveLength(1);
    expect(events[0]![1] as number).toBeGreaterThanOrEqual(7);
    expect(events[0]![1] as number).toBeLessThanOrEqual(9);
  });

  it("enqueues nothing but read events", async () => {
    const { startReading } = await loadReading();
    const stop = startReading(ITEM);
    await vi.advanceTimersByTimeAsync(30_000);
    stop();
    await vi.advanceTimersByTimeAsync(0);
    expect(enqueue.mock.calls.map((c) => c[1])).toEqual(["read"]);
  });
});
