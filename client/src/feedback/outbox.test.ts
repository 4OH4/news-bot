// The feedback outbox (#254): every event is written synchronously to `localStorage`, one key per
// event, before its IndexedDB write starts, so a page that closes straight after a tap can't lose
// it. On the next start `moveOutbox` queues what is left, keeping each event's own id and time.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FeedbackPayload } from "./event";
import {
  installFakeApi,
  installStorage,
  loadFeedback,
  OUTBOX_PREFIX,
  outboxEntries,
  throwingStorage,
} from "./test-env";

const A = "a4a4a4a4a4a4a4a1";
const B = "b4b4b4b4b4b4b4b2";
const PENDING_READS = "newsfeed-pending-reads";

let storage: Storage;
let logged: { mock: { calls: unknown[][] } }[] = [];

beforeEach(() => {
  vi.stubGlobal("navigator", { onLine: true });
  storage = installStorage();
  logged = (["error", "warn", "log", "info"] as const).map((m) =>
    vi.spyOn(console, m).mockImplementation(() => {}),
  );
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function anyLogged(): boolean {
  return logged.some((spy) => spy.mock.calls.length > 0);
}

async function load() {
  const f = await loadFeedback();
  const outbox = await import("./outbox");
  return { ...f, ...outbox };
}

/** An invented event as the tap would have built it. */
function payload(id: string, articleId: string, createdAt: string, event = "like", value = 1) {
  return { id, article_id: articleId, event, value, created_at: createdAt } as FeedbackPayload;
}

const ID1 = "1".repeat(32);
const ID2 = "2".repeat(32);
const ID3 = "3".repeat(32);

async function queuedRows(f: Awaited<ReturnType<typeof load>>) {
  const rows = await (await f.openFeedbackDb()).getAll("queue");
  return [...rows].sort((a, b) => a.seq - b.seq);
}

describe("writeOutbox and removeOutbox", () => {
  it("keeps each event under its own key, holding the whole payload, until removed", async () => {
    const f = await load();
    const first = payload(ID1, A, "2026-11-07T08:00:00.000Z");
    const second = payload(ID2, B, "2026-11-07T08:00:00.000Z", "dismiss", 1);
    f.writeOutbox(first);
    f.writeOutbox(second);
    expect(JSON.parse(storage.getItem(`${OUTBOX_PREFIX}${ID1}`)!)).toEqual(first);
    expect(JSON.parse(storage.getItem(`${OUTBOX_PREFIX}${ID2}`)!)).toEqual(second);

    f.removeOutbox(ID1);
    expect(storage.getItem(`${OUTBOX_PREFIX}${ID1}`)).toBeNull();
    expect(outboxEntries(storage)).toEqual([second]);
  });

  it("with storage that throws, logs and returns instead of throwing", async () => {
    const f = await load();
    installStorage(throwingStorage());
    expect(() => f.writeOutbox(payload(ID1, A, "2026-11-07T08:00:00.000Z"))).not.toThrow();
    expect(anyLogged()).toBe(true);
  });
});

describe("moveOutbox", () => {
  it("queues an entry from 08:00 found at 11:00 with its own id and created_at, and posts it so", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-07T11:00:00Z"));
    const f = await load();
    const api = installFakeApi("offline");
    const left = payload(ID1, A, "2026-11-07T08:00:00.000Z");
    storage.setItem(`${OUTBOX_PREFIX}${ID1}`, JSON.stringify(left));

    await f.moveOutbox();

    expect((await queuedRows(f)).map((r) => f.toPayload(r))).toEqual([left]);
    expect(outboxEntries(storage)).toEqual([]);
    api.answer = () => 204;
    await f.flushQueue();
    expect(api.posts.map((p) => p.body)).toEqual([left]);
  });

  it("queues oldest created_at first", async () => {
    const f = await load();
    installFakeApi("offline");
    f.writeOutbox(payload(ID1, A, "2026-11-07T09:00:00.000Z"));
    f.writeOutbox(payload(ID2, B, "2026-11-07T08:00:00.000Z"));
    f.writeOutbox(payload(ID3, A, "2026-11-07T08:30:00.000Z"));

    await f.moveOutbox();

    expect((await queuedRows(f)).map((r) => r.id)).toEqual([ID2, ID3, ID1]);
  });

  it("an event already in the queue is queued once, posted once, and its entry removed", async () => {
    const f = await load();
    const api = installFakeApi("offline");
    // The page died after the queue write but before the outbox entry was removed.
    const event = await f.enqueueFeedback(A, "dismiss", 1);
    f.writeOutbox(event);

    await f.moveOutbox();

    expect(await f.queuedCount()).toBe(1);
    expect(outboxEntries(storage)).toEqual([]);
    api.answer = () => 204;
    await f.flushQueue();
    expect(api.posts.map((p) => p.id)).toEqual([event.id]);
  });

  it("an entry for an event the server already took is posted again under the same id", async () => {
    const f = await load();
    const api = installFakeApi(204);
    const event = await f.enqueueFeedback(A, "like", 1);
    await f.flushQueue();
    expect(api.posts.map((p) => p.id)).toEqual([event.id]);
    // Left behind by a page that froze mid-write; the server's store upserts by id.
    f.writeOutbox(event);

    await f.moveOutbox();
    await f.flushQueue();

    expect(api.posts.map((p) => p.body)).toEqual([event, event]);
    expect(await f.queuedCount()).toBe(0);
  });

  it("queues both of two entries written at the same moment by two tabs", async () => {
    const f = await load();
    installFakeApi("offline");
    const at = "2026-11-07T08:00:00.000Z";
    // Each tab writes only its own key, so neither write can overwrite the other.
    storage.setItem(`${OUTBOX_PREFIX}${ID1}`, JSON.stringify(payload(ID1, A, at)));
    storage.setItem(`${OUTBOX_PREFIX}${ID2}`, JSON.stringify(payload(ID2, B, at, "dismiss", 1)));

    await f.moveOutbox();

    expect((await queuedRows(f)).map((r) => r.id).sort()).toEqual([ID1, ID2]);
    expect(outboxEntries(storage)).toEqual([]);
  });

  it.each([
    ["not valid JSON", "{not json"],
    ["an unknown event", JSON.stringify(payload("4".repeat(32), A, "2026-11-07T08:00:00.000Z", "share"))],
    ["no id", JSON.stringify({ ...payload("x", A, "2026-11-07T08:00:00.000Z"), id: undefined })],
    ["a created_at that doesn't parse", JSON.stringify(payload("5".repeat(32), A, "invented-time"))],
  ])("removes and logs an entry with %s, and still queues the valid one", async (_kind, raw) => {
    const f = await load();
    installFakeApi("offline");
    const bad = `${OUTBOX_PREFIX}${"9".repeat(32)}`;
    storage.setItem(bad, raw);
    const good = payload(ID1, A, "2026-11-07T08:00:00.000Z");
    f.writeOutbox(good);

    await f.moveOutbox();

    expect(storage.getItem(bad)).toBeNull();
    expect(anyLogged()).toBe(true);
    expect((await queuedRows(f)).map((r) => f.toPayload(r))).toEqual([good]);
  });

  it("leaves an entry the queue refuses for the next start", async () => {
    const f = await load();
    const left = payload(ID1, A, "2026-11-07T08:00:00.000Z");
    f.writeOutbox(left);
    vi.stubGlobal("indexedDB", {
      open() {
        throw new DOMException("invented: no database", "UnknownError");
      },
    });

    await f.moveOutbox().catch(() => {});

    expect(outboxEntries(storage)).toEqual([left]);
  });

  it("drains the old newsfeed-pending-reads key once, as read events stamped at move time", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-07T11:00:00Z"));
    const f = await load();
    installFakeApi("offline");
    storage.setItem(PENDING_READS, JSON.stringify({ o1: { article_id: A, value: 12 } }));

    await f.moveOutbox();
    await f.moveOutbox();

    const rows = (await queuedRows(f)).map((r) => f.toPayload(r));
    expect(rows).toEqual([
      expect.objectContaining({
        article_id: A,
        event: "read",
        value: 12,
        created_at: "2026-11-07T11:00:00.000Z",
      }),
    ]);
    expect(rows[0]!.id).toMatch(/^[0-9a-f]{32}$/);
    expect(storage.getItem(PENDING_READS)).toBeNull();
  });
});

/** Binds a proxied object's methods to the real object, so idb's own wrappers keep working. */
function bound(target: object, prop: string | symbol): unknown {
  const value: unknown = Reflect.get(target, prop, target);
  return typeof value === "function" ? (value as (...a: unknown[]) => unknown).bind(target) : value;
}

/**
 * Makes every `add` into the queue fail with a non-constraint error, as a full disk would. Only
 * ConstraintError (the id is already queued) may count as stored; this one must not.
 */
function failQueueAdds(): void {
  vi.doMock("./db", async (importOriginal) => {
    const real = await importOriginal<typeof import("./db")>();
    const failing = (store: object) =>
      new Proxy(store, {
        get: (s, p) =>
          p === "add"
            ? () => Promise.reject(new DOMException("invented: disk full", "QuotaExceededError"))
            : bound(s, p),
      });
    const wrap = (db: object) =>
      new Proxy(db, {
        get(target, prop) {
          if (prop !== "transaction") return bound(target, prop);
          return (...args: unknown[]) => {
            const open = Reflect.get(target, prop, target) as (...a: unknown[]) => object;
            const tx = open.apply(target, args);
            if (args[0] !== "queue" || args[1] !== "readwrite") return tx;
            const store = failing(Reflect.get(tx, "store", tx) as object);
            return new Proxy(tx, { get: (t, p) => (p === "store" ? store : bound(t, p)) });
          };
        },
      });
    return { ...real, openFeedbackDb: () => real.openFeedbackDb().then(wrap) };
  });
}

// Review of PR #262: only a repeated id may count as stored, so any other failure must leave the
// outbox entry in place.
describe("a queue write that fails for any reason but a repeated id", () => {
  afterEach(() => {
    vi.doUnmock("./db");
  });

  it("enqueueFeedback rejects and leaves the outbox entry for the next start", async () => {
    failQueueAdds();
    const f = await load();
    installFakeApi("offline");

    await expect(f.enqueueFeedback(A, "like", 1)).rejects.toMatchObject({
      name: "QuotaExceededError",
    });

    expect(outboxEntries(storage)).toEqual([
      expect.objectContaining({ article_id: A, event: "like", value: 1 }),
    ]);
    expect(await f.queuedCount()).toBe(0);
  });

  it("moveOutbox leaves the entry in place and logs", async () => {
    failQueueAdds();
    const f = await load();
    installFakeApi("offline");
    const left = payload(ID1, A, "2026-11-07T08:00:00.000Z");
    f.writeOutbox(left);

    await f.moveOutbox();

    expect(outboxEntries(storage)).toEqual([left]);
    expect(await f.queuedCount()).toBe(0);
    expect(anyLogged()).toBe(true);
  });
});
