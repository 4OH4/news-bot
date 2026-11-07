// Dismissed stories and muted sources (#217): kept by story key and by source, each change queued
// through the feedback queue, and undone by the same event with value 0.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  installRouter,
  loadStores,
  makeItem,
  queuedEvents,
  type Stores,
} from "../../test/test-env";

const A = makeItem("a1a1a1a1a1a1a1a1", { story_id: "story-invented-a" });
const B = makeItem("b2b2b2b2b2b2b2b2", { source_id: "invented-other", domain: "other.example.test" });

beforeEach(() => {
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
});

afterEach(() => {
  vi.doUnmock("./queue");
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function load(): Promise<{ s: Stores; hidden: typeof import("./hidden") }> {
  installRouter({ online: false });
  const s = await loadStores();
  const hidden = await import("./hidden");
  return { s, hidden };
}

describe("dismissStory and undoDismiss", () => {
  it("a dismissed story stays hidden when a later feed re-keys it under the same story_id", async () => {
    const { hidden } = await load();
    const rekeyed = makeItem("c3c3c3c3c3c3c3c3", { story_id: A.story_id! });

    await hidden.dismissStory(A);

    expect(await hidden.isHidden(A)).toBe(true);
    expect(await hidden.isHidden(rekeyed)).toBe(true);
    expect(await hidden.isHidden(B)).toBe(false);
  });

  it("an item with no story_id is hidden by its id", async () => {
    const { hidden } = await load();
    const bare = makeItem("d4d4d4d4d4d4d4d4", { story_id: undefined });

    await hidden.dismissStory(bare);

    expect(await hidden.isHidden(bare)).toBe(true);
    expect(await hidden.isHidden(makeItem("e5e5e5e5e5e5e5e5", { story_id: undefined }))).toBe(
      false,
    );
  });

  it("undo shows the story again and queues dismiss 0 under a new id, after the dismiss 1", async () => {
    const { s, hidden } = await load();

    await hidden.dismissStory(A);
    await hidden.undoDismiss(A);

    expect(await hidden.isHidden(A)).toBe(false);
    const queued = await queuedEvents(s);
    expect(queued.map((e) => [e.article_id, e.event, e.value])).toEqual([
      [A.id, "dismiss", 1],
      [A.id, "dismiss", 0],
    ]);
    expect(queued[1]!.id).not.toBe(queued[0]!.id);
  });
});

describe("muteSource, unmuteSource and listMuted", () => {
  it("hides every item from a muted source, in a later feed too, and unmuting brings them back", async () => {
    const { s, hidden } = await load();
    const later = [
      makeItem("f6f6f6f6f6f6f6f6", { story_id: "story-invented-f" }),
      makeItem("a7a7a7a7a7a7a7a7", { story_id: "story-invented-g" }),
    ];

    await hidden.muteSource(A);
    for (const item of later) expect(await hidden.isHidden(item)).toBe(true);
    expect(await hidden.isHidden(B)).toBe(false);

    await hidden.unmuteSource(A.source_id);
    for (const item of [A, ...later]) expect(await hidden.isHidden(item)).toBe(false);
    expect(await hidden.listMuted()).toEqual([]);
    const queued = await queuedEvents(s);
    expect(queued.map((e) => [e.article_id, e.event, e.value])).toEqual([
      [A.id, "mute_source", 1],
      [A.id, "mute_source", 0],
    ]);
  });

  it("unmuting a source whose item has left the feed queues mute_source 0 with that item's id", async () => {
    const { s, hidden } = await load();

    await hidden.muteSource(A);
    // Only the source id is known to Settings; the item is long gone from the feed.
    await hidden.unmuteSource(A.source_id);

    const last = (await queuedEvents(s)).at(-1)!;
    expect(last).toMatchObject({ article_id: A.id, event: "mute_source", value: 0 });
  });

  it("lists muted sources' items most recently muted first", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-07T09:00:00Z"));
    const { hidden } = await load();

    await hidden.muteSource(A);
    vi.setSystemTime(new Date("2026-11-07T09:05:00Z"));
    await hidden.muteSource(B);

    const list = await hidden.listMuted();
    expect(list.map((i) => i.source_id)).toEqual([B.source_id, A.source_id]);
    expect(list[0]).toEqual(B);
  });
});

describe("subscribeHidden", () => {
  it("tells listeners when a source is muted or unmuted, until they unsubscribe", async () => {
    const { hidden } = await load();
    const listener = vi.fn();
    const unsubscribe = hidden.subscribeHidden(listener);

    await hidden.muteSource(A);
    await vi.waitFor(() => expect(listener).toHaveBeenCalled());
    const afterMute = listener.mock.calls.length;
    await hidden.unmuteSource(A.source_id);
    await vi.waitFor(() => expect(listener.mock.calls.length).toBeGreaterThan(afterMute));

    unsubscribe();
    const before = listener.mock.calls.length;
    await hidden.muteSource(B);
    await new Promise((r) => setTimeout(r, 20));
    expect(listener.mock.calls.length).toBe(before);
  });
});

describe("the queue is the only way out", () => {
  it("every change is queued through enqueueFeedback and nothing is posted directly", async () => {
    const router = installRouter({ online: true });
    const enqueueFeedback = vi.fn(async () => ({}));
    vi.doMock("./queue", () => ({ enqueueFeedback, flushQueue: vi.fn(async () => {}) }));
    await loadStores();
    const hidden = await import("./hidden");

    await hidden.dismissStory(A);
    await hidden.undoDismiss(A);
    await hidden.muteSource(B);
    await hidden.unmuteSource(B.source_id);

    const calls = enqueueFeedback.mock.calls as unknown[][];
    expect(calls.map((c) => [c[0], c[1], c[2] ?? null])).toEqual([
      [A.id, "dismiss", 1],
      [A.id, "dismiss", 0],
      [B.id, "mute_source", 1],
      [B.id, "mute_source", 0],
    ]);
    expect(router.calls.filter((c) => c.url === "/api/feedback")).toEqual([]);
  });
});
