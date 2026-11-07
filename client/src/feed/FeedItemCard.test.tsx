// @vitest-environment happy-dom
//
// Actions on a feed card (#217): a tap queues `open` and marks the story seen; the menu dismisses
// the story or mutes its source; a card hidden by an action shows a one-line Undo row.
import { Fragment, h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chooseAction, looksSeen, menuToggle, nameOf } from "../../test/cards";
import {
  buttonsNamed,
  installRouter,
  loadStores,
  locationAt,
  makeItem,
  queuedEvents,
  settle,
  type Stores,
} from "../../test/test-env";
import type { FeedItem } from "./types";

const ARTICLE = makeItem("a1b2c3d4e5f60718", { title: "Invented story about canal locks" });
const LINK = makeItem("b2c3d4e5f6071829", {
  title: "Invented story about bell ringers",
  display: "link",
  content_path: null,
});
const OTHER = makeItem("c3d4e5f60718293a", {
  title: "Invented story about pier repairs",
  story_id: "story-invented-pier",
});
const ELSEWHERE = makeItem("d4e5f60718293a4b", {
  title: "Invented story about orchard frost",
  source_id: "invented-elsewhere",
  domain: "elsewhere.example.test",
});

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("location", locationAt("/"));
  vi.stubGlobal("open", vi.fn(() => null));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
  // Keep happy-dom from following links; the card's own handler still runs.
  root.addEventListener("click", (e) => e.preventDefault());
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.doUnmock("../feedback/queue");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function load(): Promise<Stores> {
  installRouter({ online: false });
  return loadStores();
}

async function showCards(...items: FeedItem[]): Promise<void> {
  const { FeedItemCard } = await import("./FeedItemCard");
  render(
    h(
      Fragment,
      null,
      items.map((item) => h(FeedItemCard, { key: item.id, item })),
    ),
    root,
  );
  await vi.waitFor(() => expect(root.textContent).toContain(items[0]!.title));
}

function cardFor(item: FeedItem): HTMLElement | null {
  return (
    [...root.querySelectorAll<HTMLElement>("[data-testid='feed-item']")].find((c) =>
      c.textContent?.includes(item.title),
    ) ?? null
  );
}

function titleLink(item: FeedItem): HTMLAnchorElement {
  const link = [...cardFor(item)!.querySelectorAll("a")].find((a) => a.textContent === item.title);
  if (!link) throw new Error(`no title link for ${item.id}`);
  return link;
}

describe("tapping a card", () => {
  it.each([
    ["an article", ARTICLE],
    ["a link", LINK],
  ])("tapping %s queues an open event with its id and shows it as seen", async (_kind, item) => {
    const s = await load();
    await showCards(item);
    expect(looksSeen(cardFor(item)!)).toBe(false);

    titleLink(item).click();

    await vi.waitFor(async () => {
      const opens = (await queuedEvents(s)).filter((e) => e.event === "open");
      expect(opens.map((e) => [e.article_id, e.value])).toEqual([[item.id, null]]);
    });
    await vi.waitFor(() => expect(looksSeen(cardFor(item)!)).toBe(true));
  });

  it("a story opened under one id shows as seen when a later feed gives it another id", async () => {
    const s = await load();
    await showCards(OTHER);
    titleLink(OTHER).click();
    await vi.waitFor(async () => expect(await queuedEvents(s)).toHaveLength(1));
    render(null, root);

    const rekeyed = makeItem("e5f60718293a4b5c", {
      title: "Invented story about pier repairs, updated",
      story_id: OTHER.story_id!,
    });
    await showCards(rekeyed);
    await vi.waitFor(() => expect(looksSeen(cardFor(rekeyed)!)).toBe(true));
  });

  it("the tap goes through enqueueFeedback, not a direct post", async () => {
    const router = installRouter({ online: true });
    const enqueueFeedback = vi.fn(async () => ({}));
    vi.doMock("../feedback/queue", () => ({ enqueueFeedback, flushQueue: vi.fn(async () => {}) }));
    await loadStores();
    await showCards(ARTICLE);

    titleLink(ARTICLE).click();

    await vi.waitFor(() => expect(enqueueFeedback).toHaveBeenCalled());
    const calls = enqueueFeedback.mock.calls as unknown[][];
    expect(calls.map((c) => [c[0], c[1], c[2] ?? null])).toEqual([[ARTICLE.id, "open", null]]);
    expect(router.calls.filter((c) => c.url === "/api/feedback")).toEqual([]);
  });
});

describe("the card menu", () => {
  it("has a named toggle, Dismiss, and Mute <domain> as buttons", async () => {
    await load();
    await showCards(ARTICLE);
    const card = cardFor(ARTICLE)!;
    const toggle = menuToggle(card);
    if (toggle) expect(nameOf(toggle)).not.toBe("");
    if (buttonsNamed(card, /dismiss/i).length === 0) toggle?.click();
    await settle(0);
    expect(buttonsNamed(card, /dismiss/i)).toHaveLength(1);
    const mute = buttonsNamed(card, /mute/i);
    expect(mute).toHaveLength(1);
    expect(nameOf(mute[0]!)).toContain(ARTICLE.domain);
  });

  it("dismiss hides the story behind an Undo row; Undo brings it back and queues dismiss 0", async () => {
    const s = await load();
    await showCards(ARTICLE);

    await chooseAction(cardFor(ARTICLE)!, /dismiss/i);
    await vi.waitFor(() => expect(root.textContent).not.toContain(ARTICLE.title));
    const [undo] = buttonsNamed(root, /undo/i);
    expect(undo).toBeDefined();
    expect(root.textContent).toMatch(/hidden/i);

    undo!.click();
    await vi.waitFor(() => expect(root.textContent).toContain(ARTICLE.title));
    const events = (await queuedEvents(s)).filter((e) => e.event === "dismiss");
    expect(events.map((e) => [e.article_id, e.value])).toEqual([
      [ARTICLE.id, 1],
      [ARTICLE.id, 0],
    ]);
    expect(events[1]!.id).not.toBe(events[0]!.id);
  });

  it("muting from one card hides both cards from that source, without reloading", async () => {
    const s = await load();
    await showCards(ARTICLE, OTHER, ELSEWHERE);

    await chooseAction(cardFor(ARTICLE)!, /mute/i);

    await vi.waitFor(() => {
      expect(root.textContent).not.toContain(ARTICLE.title);
      expect(root.textContent).not.toContain(OTHER.title);
    });
    expect(root.textContent).toContain(ELSEWHERE.title);
    // Only the card the action was taken on offers Undo.
    expect(buttonsNamed(root, /undo/i)).toHaveLength(1);
    const mutes = (await queuedEvents(s)).filter((e) => e.event === "mute_source");
    expect(mutes.map((e) => [e.article_id, e.value])).toEqual([[ARTICLE.id, 1]]);
  });

  it("a card already hidden when the screen loads renders nothing, not an Undo row", async () => {
    await load();
    const hidden = await import("../feedback/hidden");
    await hidden.dismissStory(ARTICLE);
    await hidden.muteSource(ELSEWHERE);

    const { FeedItemCard } = await import("./FeedItemCard");
    render(
      h(Fragment, null, [ARTICLE, ELSEWHERE, LINK].map((item) => h(FeedItemCard, { key: item.id, item }))),
      root,
    );
    await vi.waitFor(() => expect(root.textContent).toContain(LINK.title));
    await settle();
    expect(root.textContent).not.toContain(ARTICLE.title);
    expect(root.textContent).not.toContain(ELSEWHERE.title);
    expect(buttonsNamed(root, /undo/i)).toHaveLength(0);
  });
});
