// The view and A/B arm that ride along with a feedback event (#327).
import { afterEach, describe, expect, it, vi } from "vitest";
import { feedbackPosts, installRouter, loadStores, queuedEvents, settle } from "../../test/test-env";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("enqueueFeedback context", () => {
  it("posts view and arm when a context is given", async () => {
    const router = installRouter({ feedback: 204 });
    const s = await loadStores();
    await s.enqueueFeedback("a1b2c3d4e5f60718", "open", undefined, {
      view: "feed",
      arm: "personal",
    });
    await vi.waitFor(() => expect(feedbackPosts(router)).toHaveLength(1));
    expect(feedbackPosts(router)[0]).toMatchObject({ view: "feed", arm: "personal" });
  });

  it("leaves arm out entirely when the context has only a view", async () => {
    const router = installRouter({ feedback: 204 });
    const s = await loadStores();
    await s.enqueueFeedback("a1b2c3d4e5f60718", "open", undefined, { view: "news" });
    await vi.waitFor(() => expect(feedbackPosts(router)).toHaveLength(1));
    const [body] = feedbackPosts(router);
    expect(body!.view).toBe("news");
    expect("arm" in body!).toBe(false);
  });

  it("sends an event queued by the previous version, with neither field, unchanged", async () => {
    const router = installRouter({ feedback: 204 });
    const s = await loadStores();
    const old = {
      id: "9e8d7c6b5a493827160f1e2d3c4b5a69",
      article_id: "7c6b5a4938271605",
      event: "open" as const,
      value: null,
      created_at: "2026-11-07T09:15:00.000Z",
    };
    await s.storeEvent(old);
    await s.flushQueue();
    await settle();
    expect(feedbackPosts(router)).toEqual([old]);
    expect(await queuedEvents(s)).toEqual([]);
  });
});
