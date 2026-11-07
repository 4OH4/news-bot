// Likes (#216): a like or unlike is queued through the feedback queue, the liked item is kept in
// the `liked` store by story key, and a liked article's stored text is kept past pruning.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  articleRecord,
  bodyFor,
  feedbackPosts,
  installRouter,
  loadStores,
  makeFeed,
  makeItem,
  queuedEvents,
  settle,
  type Stores,
} from "../../test/test-env";

const A = makeItem("a1a1a1a1a1a1a1a1");
const B = makeItem("b2b2b2b2b2b2b2b2");
const DAY_MS = 24 * 60 * 60 * 1000;

beforeEach(() => {
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function load(): Promise<{ s: Stores; liked: typeof import("./liked") }> {
  const s = await loadStores();
  const liked = await import("./liked");
  return { s, liked };
}

function likeEvents(events: Record<string, unknown>[]): Record<string, unknown>[] {
  return events.filter((e) => e.event === "like");
}

describe("setLiked", () => {
  it("with no network, liking queues a like of 1 at once, and it is sent once the network returns", async () => {
    const router = installRouter({ online: false });
    const { s, liked } = await load();

    await liked.setLiked(A, true);
    expect(await liked.isLiked(A)).toBe(true);
    const queued = likeEvents(await queuedEvents(s));
    expect(queued).toHaveLength(1);
    expect(queued[0]).toMatchObject({ article_id: A.id, event: "like", value: 1 });
    await settle();
    expect(feedbackPosts(router)).toEqual([]);

    router.online = true;
    await s.flushQueue();
    await settle();
    const sent = likeEvents(feedbackPosts(router));
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ article_id: A.id, event: "like", value: 1 });
    expect(sent[0]!.id).toBe(queued[0]!.id);
    expect(await s.queuedCount()).toBe(0);
  });

  it("liking keeps the stored text: marked keep, and still there after it leaves the feed and a week passes", async () => {
    installRouter({ online: false });
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-01T08:00:00Z"));
    const { s, liked } = await load();
    await s.saveFeed(makeFeed([A, B]), '"e1"');
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));
    await s.putArticleHtml(B.id, bodyFor(B.id, "stored"));

    await liked.setLiked(A, true);
    expect((await articleRecord(A.id))?.keep).toBe(true);

    vi.setSystemTime(new Date(Date.parse("2026-11-01T08:00:00Z") + 8 * DAY_MS));
    const later = makeFeed([makeItem("c3c3c3c3c3c3c3c3")], "run-invented-2");
    await s.saveFeed(later, '"e2"');
    await s.pruneArticles(later);

    expect(await s.getArticleHtml(A.id)).toBe(bodyFor(A.id, "stored"));
    // The unliked one, stored at the same time, is pruned: the keep mark is what saved A.
    expect(await s.getArticleHtml(B.id)).toBeNull();
    expect((await liked.listLiked()).map((i) => i.id)).toEqual([A.id]);
  });

  it("unliking queues a like of 0 with a new id, removes it from the Liked list, and drops the keep mark", async () => {
    installRouter({ online: false });
    const { s, liked } = await load();
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));

    await liked.setLiked(A, true);
    await liked.setLiked(A, false);

    const queued = likeEvents(await queuedEvents(s));
    expect(queued.map((e) => e.value)).toEqual([1, 0]);
    expect(queued[1]).toMatchObject({ article_id: A.id, event: "like", value: 0 });
    expect(queued[1]!.id).not.toBe(queued[0]!.id);
    expect(await liked.isLiked(A)).toBe(false);
    expect(await liked.listLiked()).toEqual([]);
    expect((await articleRecord(A.id))?.keep).toBe(false);
    // The text itself stays until pruning would normally drop it.
    expect(await s.getArticleHtml(A.id)).toBe(bodyFor(A.id, "stored"));
  });
});

describe("listLiked and isLiked", () => {
  it("lists liked items most recent first, each the whole feed item", async () => {
    installRouter({ online: false });
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-02T10:00:00Z"));
    const { liked } = await load();

    await liked.setLiked(A, true);
    vi.setSystemTime(new Date("2026-11-02T10:05:00Z"));
    await liked.setLiked(B, true);

    const list = await liked.listLiked();
    expect(list.map((i) => i.id)).toEqual([B.id, A.id]);
    expect(list[1]).toEqual(A);
  });

  it("is keyed by story: another row of the same story counts as liked and isn't listed twice", async () => {
    installRouter({ online: false });
    const { liked } = await load();
    const sameStory = makeItem("e5e5e5e5e5e5e5e5", { story_id: A.story_id! });

    await liked.setLiked(A, true);
    expect(await liked.isLiked(sameStory)).toBe(true);
    await liked.setLiked(sameStory, true);
    expect(await liked.listLiked()).toHaveLength(1);
  });

  it("an item never liked is not liked", async () => {
    installRouter({ online: false });
    const { liked } = await load();
    expect(await liked.isLiked(B)).toBe(false);
    expect(await liked.listLiked()).toEqual([]);
  });
});
