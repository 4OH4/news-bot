import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  countRows,
  fillStoryState,
  installFakeApi,
  loadFeedback,
  settle,
  storyStateCounts,
  STORY_KEY,
} from "./test-env";

const STORES = ["queue", "rejected", "seen", "dismissed", "muted", "liked"];

beforeEach(() => {
  vi.stubGlobal("navigator", { onLine: true });
  vi.spyOn(console, "error").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "log").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("openFeedbackDb", () => {
  it("opens its own database, newsfeed-feedback, at version 1", async () => {
    const f = await loadFeedback();
    const db = (await f.openFeedbackDb()) as unknown as IDBDatabase;
    expect(db.name).toBe("newsfeed-feedback");
    expect(db.version).toBe(1);
  });

  it("defines every store #216 and #217 need, so they add no version upgrade", async () => {
    const f = await loadFeedback();
    const db = (await f.openFeedbackDb()) as unknown as IDBDatabase;
    expect([...db.objectStoreNames].sort()).toEqual([...STORES].sort());
  });

  it("keys queue and rejected by the event id, and indexes the queue by seq", async () => {
    const f = await loadFeedback();
    const db = (await f.openFeedbackDb()) as unknown as IDBDatabase;
    const tx = db.transaction(["queue", "rejected"]);
    const queue = tx.objectStore("queue");
    expect(queue.keyPath).toBe("id");
    expect([...queue.indexNames]).toContain("seq");
    expect(tx.objectStore("rejected").keyPath).toBe("id");
  });
});

describe("clearFeedbackData", () => {
  it("empties every store, rejected events and per-story state included", async () => {
    const f = await loadFeedback();
    const api = installFakeApi("offline");
    await f.enqueueFeedback("0a0a0a0a0a0a0a01", "open");
    await settle();
    api.answer = () => 422;
    await f.flushQueue();
    api.answer = () => "offline";
    await f.enqueueFeedback("0a0a0a0a0a0a0a02", "like", 1);
    await settle();
    await fillStoryState(f);

    const db = await f.openFeedbackDb();
    expect(await countRows(db, "queue")).toBe(1);
    expect(await countRows(db, "rejected")).toBe(1);
    expect(await f.isSeen(STORY_KEY)).toBe(true);

    await f.clearFeedbackData();

    for (const name of STORES) expect(await countRows(db, name)).toBe(0);
    expect(await storyStateCounts(f)).toEqual({ seen: 0, dismissed: 0, muted: 0, liked: 0 });
  });
});

describe("seen", () => {
  it("remembers a story key once marked", async () => {
    const f = await loadFeedback();
    expect(await f.isSeen("story-invented-2")).toBe(false);
    await f.markSeen("story-invented-2");
    expect(await f.isSeen("story-invented-2")).toBe(true);
    expect(await f.isSeen("story-invented-3")).toBe(false);
  });

  it("is kept in IndexedDB, so a fresh load of the module still sees it", async () => {
    const f = await loadFeedback();
    await f.markSeen("story-invented-4");
    vi.resetModules();
    const again = await import("./seen");
    expect(await again.isSeen("story-invented-4")).toBe(true);
  });
});
