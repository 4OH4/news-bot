// @vitest-environment happy-dom
//
// The reader sends the view and arm of the card that opened it, and only for that article (#327).
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bodyFor,
  buttonsNamed,
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
  queuedEvents,
  type Stores,
} from "../../test/test-env";

const A = makeItem("aa11bb22cc33dd44", { title: "Invented headline about canal boats" });
const CONTEXT_KEY = "reader-context";

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("location", locationAt(`/article/${A.id}`));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  sessionStorage.clear();
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Opens the reader with `context` in sessionStorage, reads for 90 s, leaves; returns the events. */
async function readWith(context: unknown, like = false): Promise<Record<string, unknown>[]> {
  if (context !== undefined) sessionStorage.setItem(CONTEXT_KEY, JSON.stringify(context));
  const feed = makeFeed([A]);
  // A hanging send keeps every event in the queue, where the test reads it back.
  installRouter({ feed, articles: { [A.id]: bodyFor(A.id) }, feedback: "hang" });
  const s: Stores = await loadStores();
  await s.saveFeed(feed, '"run-invented-1"');
  const { ReaderScreen } = await import("./ReaderScreen");
  render(h(ReaderScreen, null), root);
  await vi.waitFor(() => expect(root.textContent).toContain(`body text for ${A.id}`));
  if (like) {
    const button = buttonsNamed(root, /\blike/i).find((b) => !/theme/i.test(b.textContent ?? ""));
    await vi.waitFor(() => expect(button?.disabled).toBeFalsy());
    button!.click();
    await vi.waitFor(async () => expect(await queuedEvents(s)).toHaveLength(1));
  }
  const now = Date.now();
  vi.spyOn(Date, "now").mockReturnValue(now + 90_000);
  render(null, root);
  await vi.waitFor(async () => expect(await queuedEvents(s)).toHaveLength(like ? 2 : 1));
  return queuedEvents(s);
}

const hasNeither = (e: Record<string, unknown>) => !("view" in e) && !("arm" in e);

describe("reader feedback context", () => {
  it("a read after opening from a card carries that card's view and arm", async () => {
    const events = await readWith({ id: A.id, view: "feed", arm: "personal" });
    const read = events.find((e) => e.event === "read")!;
    expect(read).toMatchObject({ article_id: A.id, view: "feed", arm: "personal" });
  });

  it("a like in the reader carries the same view and arm", async () => {
    const events = await readWith({ id: A.id, view: "feed", arm: "personal" }, true);
    const like = events.find((e) => e.event === "like")!;
    expect(like).toMatchObject({ view: "feed", arm: "personal" });
  });

  it("opening the same article again with no second write carries neither field", async () => {
    const first = await readWith({ id: A.id, view: "feed", arm: "personal" });
    expect(first.find((e) => e.event === "read")).toMatchObject({ view: "feed", arm: "personal" });
    expect(sessionStorage.getItem(CONTEXT_KEY)).toBeNull();
    vi.restoreAllMocks();
    document.body.innerHTML = "";
    root = document.createElement("div");
    document.body.appendChild(root);
    const second = await readWith(undefined);
    const reads = second.filter((e) => e.event === "read");
    expect(hasNeither(reads[reads.length - 1]!)).toBe(true);
  });

  it.each([
    ["no context (opened from Liked or a shared link)", undefined],
    ["a stale context for another article", { id: "0000000000000000", view: "feed", arm: "rules" }],
  ])("a read with %s has neither field", async (_name, context) => {
    const events = await readWith(context);
    const read = events.find((e) => e.event === "read")!;
    expect(hasNeither(read)).toBe(true);
  });
});
