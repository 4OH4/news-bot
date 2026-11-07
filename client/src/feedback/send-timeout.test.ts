// A browser without `AbortSignal.timeout` (Safari before 16) must still send the queue, with a
// time limit made from a timer instead.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeLocation, installFakeApi, loadFeedback, settle } from "./test-env";

const A = "c0c0c0c0c0c0c0c1";

beforeEach(() => {
  vi.stubGlobal("location", fakeLocation());
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("flushQueue without AbortSignal.timeout", () => {
  it("still sends, with an abort signal on the POST and a timer of at most a minute", async () => {
    const f = await loadFeedback();
    const api = installFakeApi("offline");
    const event = await f.enqueueFeedback(A, "open");
    await settle();
    const original = Object.getOwnPropertyDescriptor(AbortSignal, "timeout")!;
    Object.defineProperty(AbortSignal, "timeout", { value: undefined, configurable: true });
    const timers = vi.spyOn(globalThis, "setTimeout");
    api.answer = () => 204;
    try {
      await f.flushQueue();
    } finally {
      Object.defineProperty(AbortSignal, "timeout", original);
    }
    expect(api.posts.map((p) => p.id)).toEqual([event.id]);
    expect(api.signals.at(-1)).toBeInstanceOf(AbortSignal);
    const limits = timers.mock.calls.map((call) => call[1] ?? 0);
    expect(limits.some((ms) => ms > 0 && ms <= 60_000)).toBe(true);
    expect(await f.queuedCount()).toBe(0);
  });
});
