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
import { installStorage, outboxEntries, throwingStorage } from "./test-env";

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

/** Holds the first `put` into the `liked` store until released (as in hidden.test.ts). */
function holdFirstLikedPut(): { release: () => void } {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  let held = false;
  vi.doMock("./db", async (importOriginal) => {
    const real = await importOriginal<typeof import("./db")>();
    const wrap = (db: object): unknown =>
      new Proxy(db, {
        get(target, prop) {
          const value: unknown = Reflect.get(target, prop, target);
          if (typeof value !== "function") return value;
          const fn = value as (...args: unknown[]) => unknown;
          if (prop !== "put") return fn.bind(target);
          return async (store: unknown, ...rest: unknown[]) => {
            if (store === "liked" && !held) {
              held = true;
              await gate;
            }
            return fn.call(target, store, ...rest);
          };
        },
      });
    return { ...real, openFeedbackDb: () => real.openFeedbackDb().then(wrap) };
  });
  return { release };
}

// #254: the like event is written to the outbox inside the tap, and the phone's like state lands
// in the order tapped without the caller chaining.
describe("setLiked and a page that closes straight after the tap", () => {
  afterEach(() => {
    vi.doUnmock("./db");
  });

  it("writes the like to the outbox synchronously, inside the call", async () => {
    installRouter({ online: false });
    const storage = installStorage();
    const { liked } = await load();

    const done = liked.setLiked(A, true);
    expect(outboxEntries(storage)).toEqual([
      expect.objectContaining({ article_id: A.id, event: "like", value: 1 }),
    ]);
    await done;
  });

  it("Like then Unlike tapped quickly ends not liked, with like 1 then like 0 queued in order", async () => {
    installRouter({ online: false });
    installStorage();
    // The Like's `liked` write resolves late, so a wrong order would end liked (REVIEW.md).
    const hold = holdFirstLikedPut();
    const { s, liked } = await load();

    const first = liked.setLiked(A, true);
    const second = liked.setLiked(A, false);
    // Time for the Unlike's delete to land first, if nothing orders it after the Like's write.
    await settle();
    hold.release();
    await Promise.all([first, second]);
    await settle();

    expect(await liked.isLiked(A)).toBe(false);
    const queued = likeEvents(await queuedEvents(s));
    expect(queued.map((e) => e.value)).toEqual([1, 0]);
    expect(Date.parse(String(queued[1]!.created_at))).toBeGreaterThanOrEqual(
      Date.parse(String(queued[0]!.created_at)),
    );
  });

  it("with localStorage throwing on every call, a like is still queued and posted", async () => {
    const router = installRouter({ online: true });
    installStorage(throwingStorage());
    const { s, liked } = await load();

    await liked.setLiked(A, true);

    await vi.waitFor(() =>
      expect(likeEvents(feedbackPosts(router))).toEqual([
        expect.objectContaining({ article_id: A.id, event: "like", value: 1 }),
      ]),
    );
    await vi.waitFor(async () => expect(await s.queuedCount()).toBe(0));
    expect(await liked.isLiked(A)).toBe(true);
  });

  it("unliking a story re-keyed since the like drops the keep mark on both ids' text", async () => {
    installRouter({ online: false });
    const { s, liked } = await load();
    const rekeyed = makeItem("f6f6f6f6f6f6f6f6", { story_id: A.story_id! });
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));
    await liked.setLiked(A, true);
    // A later feed gives the story a new id; the reader stores its text kept, as it is liked.
    await s.putArticleHtml(rekeyed.id, bodyFor(rekeyed.id, "stored"), { keep: true });

    await liked.setLiked(rekeyed, false);

    expect(await liked.isLiked(rekeyed)).toBe(false);
    expect((await articleRecord(A.id))?.keep).toBe(false);
    expect((await articleRecord(rekeyed.id))?.keep).toBe(false);
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
