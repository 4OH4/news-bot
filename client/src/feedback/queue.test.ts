import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { answerBy, fakeLocation, installFakeApi, loadFeedback, settle, type Answer } from "./test-env";

const A = "a0a0a0a0a0a0a0a1";
const B = "b0b0b0b0b0b0b0b2";
const C = "c0c0c0c0c0c0c0c3";

/** Console spies, so a rejected event's log can be seen without printing it. */
let logged: { mock: { calls: unknown[][] } }[] = [];

beforeEach(() => {
  vi.stubGlobal("navigator", { onLine: true });
  vi.stubGlobal("location", fakeLocation());
  logged = (["error", "warn", "log", "info"] as const).map((m) =>
    vi.spyOn(console, m).mockImplementation(() => {}),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Enqueues one event per article while offline, so they all wait in the queue. */
async function queueOffline(f: Awaited<ReturnType<typeof loadFeedback>>, ...articles: string[]) {
  const api = installFakeApi("offline");
  const events = [];
  for (const article of articles) events.push(await f.enqueueFeedback(article, "open"));
  await settle();
  return { api, events };
}

describe("enqueueFeedback", () => {
  it("with no network, puts the event in the queue at once and keeps it", async () => {
    const f = await loadFeedback();
    const api = installFakeApi("offline");
    const event = await f.enqueueFeedback(A, "read", 30);
    expect(await f.queuedCount()).toBe(1);
    await settle();
    expect(await f.queuedCount()).toBe(1);
    expect(api.posts).toHaveLength(0);
    expect(event).toMatchObject({ article_id: A, event: "read", value: 30 });
    expect(event.id).toMatch(/^[0-9a-f]{32}$/);
  });

  it("writes the event before resolving, without waiting for the send", async () => {
    const f = await loadFeedback();
    installFakeApi("hang");
    await f.enqueueFeedback(A, "open");
    expect(await f.queuedCount()).toBe(1);
  });

  it("starts a send by itself when the network works", async () => {
    const f = await loadFeedback();
    const api = installFakeApi(204);
    const event = await f.enqueueFeedback(A, "like", 1);
    await vi.waitFor(() => expect(api.posts.map((p) => p.id)).toEqual([event.id]));
    await vi.waitFor(async () => expect(await f.queuedCount()).toBe(0));
  });
});

describe("flushQueue", () => {
  it("sends a queued event once the network is back, then removes it", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A);
    api.answer = () => 204;
    await f.flushQueue();
    expect(api.posts.map((p) => p.id)).toEqual([events[0]!.id]);
    expect(await f.queuedCount()).toBe(0);
  });

  it("posts JSON to /api/feedback with the client's headers and the event as the body", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A);
    api.answer = () => 204;
    await f.flushQueue();
    const post = api.posts[0]!;
    expect(post.url).toBe("/api/feedback");
    expect(post.method).toBe("POST");
    expect(post.headers.get("Content-Type")).toMatch(/^application\/json/);
    expect(post.headers.get("X-Requested-With")).toBe("XMLHttpRequest");
    expect(post.body).toEqual(events[0]);
  });

  it("treats any 2xx as accepted", async () => {
    const f = await loadFeedback();
    const { api } = await queueOffline(f, A, B);
    api.answer = answerBy({ [A]: 200 }, 201);
    await f.flushQueue();
    expect(await f.queuedCount()).toBe(0);
    expect(await f.listRejected()).toEqual([]);
  });

  it("sends the oldest event first, one at a time", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A, B, C);
    api.answer = () => 204;
    await f.flushQueue();
    expect(api.posts.map((p) => p.id)).toEqual(events.map((e) => e.id));
  });

  it("resends the same id when the server got the event but the answer was lost", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A);
    api.answer = () => "lost";
    await f.flushQueue();
    expect(await f.queuedCount()).toBe(1);
    api.answer = () => 204;
    await f.flushQueue();
    expect(api.posts).toHaveLength(2);
    expect(api.posts[0]!.id).toBe(events[0]!.id);
    expect(api.posts[1]!.id).toBe(events[0]!.id);
    expect(api.posts[1]!.body).toEqual(api.posts[0]!.body);
    expect(await f.queuedCount()).toBe(0);
  });

  it("on a 503 for the first entry, keeps it and sends nothing after it", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A, B);
    api.answer = answerBy({ [A]: 503 });
    await f.flushQueue();
    expect(api.posts.map((p) => p.id)).toEqual([events[0]!.id]);
    expect(await f.queuedCount()).toBe(2);
    expect(await f.listRejected()).toEqual([]);
  });

  it("on a 403 (owner email misconfigured), keeps the entry, rejects nothing and sends nothing after it", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A, B);
    api.answer = answerBy({ [A]: 403 });
    await f.flushQueue();
    expect(api.posts.map((p) => p.id)).toEqual([events[0]!.id]);
    expect(await f.queuedCount()).toBe(2);
    expect(await f.listRejected()).toEqual([]);
  });

  it.each<[string, Answer]>([
    ["a network error", "offline"],
    ["500", 500],
    ["502", 502],
    ["401", 401],
    ["408", 408],
    ["429", 429],
  ])("on %s, keeps the entry and stops", async (_name, answer) => {
    const f = await loadFeedback();
    const { api } = await queueOffline(f, A, B);
    api.posts.length = 0;
    api.answer = answerBy({ [A]: answer });
    await f.flushQueue();
    expect(api.posts.map((p) => p.body.article_id)).not.toContain(B);
    expect(await f.queuedCount()).toBe(2);
    expect(await f.listRejected()).toEqual([]);
  });

  it("on a 422 for one entry, moves it to rejected, logs it and sends the next", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A, B);
    api.answer = answerBy({ [A]: 422 });
    await f.flushQueue();
    expect(api.posts.map((p) => p.id)).toEqual([events[0]!.id, events[1]!.id]);
    expect(await f.queuedCount()).toBe(0);
    expect(await f.listRejected()).toEqual([events[0]]);
    expect(logged.some((spy) => spy.mock.calls.length > 0)).toBe(true);
  });

  it.each([400, 404, 409, 410, 415])(
    "on another 4xx (%i), moves the entry to rejected and carries on",
    async (status) => {
      const f = await loadFeedback();
      const { api, events } = await queueOffline(f, A, B);
      api.answer = answerBy({ [A]: status });
      await f.flushQueue();
      expect(await f.queuedCount()).toBe(0);
      expect((await f.listRejected()).map((e) => e.id)).toEqual([events[0]!.id]);
    },
  );

  it("never re-sends a rejected event", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A);
    api.answer = () => 422;
    await f.flushQueue();
    api.posts.length = 0;
    api.answer = () => 204;
    await f.flushQueue();
    expect(api.posts).toHaveLength(0);
    expect(await f.listRejected()).toEqual([events[0]]);
  });

  it("posts each entry once when two flushes start together", async () => {
    const f = await loadFeedback();
    const { api, events } = await queueOffline(f, A, B, C);
    api.posts.length = 0;
    api.delayMs = 10;
    api.answer = () => 204;
    await Promise.all([f.flushQueue(), f.flushQueue()]);
    await settle();
    expect(api.posts.map((p) => p.id).sort()).toEqual(events.map((e) => e.id).sort());
    expect(await f.queuedCount()).toBe(0);
  });

  it("does nothing, and posts nothing, when the queue is empty", async () => {
    const f = await loadFeedback();
    const api = installFakeApi(204);
    await f.flushQueue();
    expect(api.attempts).toBe(0);
  });
});

describe("queuedCount and listRejected", () => {
  it("count the queue and list the rejected events", async () => {
    const f = await loadFeedback();
    expect(await f.queuedCount()).toBe(0);
    expect(await f.listRejected()).toEqual([]);
    const { api, events } = await queueOffline(f, A, B, C);
    expect(await f.queuedCount()).toBe(3);
    api.answer = answerBy({ [A]: 422, [B]: 503 });
    await f.flushQueue();
    expect(await f.queuedCount()).toBe(2);
    expect(await f.listRejected()).toEqual([events[0]]);
  });
});
