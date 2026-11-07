// @vitest-environment happy-dom
//
// The reader (#216): stored text first, the network only for what isn't stored, an offline
// fallback without an error page, a like button, and reading time while it is open.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  articleCalls,
  articleRecord,
  bodyFor,
  buttonsNamed,
  feedbackPosts,
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
  queuedEvents,
  settle,
  type FakeRouter,
} from "./test-env";

const A = makeItem("aa11bb22cc33dd44", {
  title: "Invented headline about canal boats",
  summary: "An invented summary of a story about canal boats.",
});
const FEED = makeFeed([A, makeItem("ff00ee11dd22cc33")]);

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("location", locationAt(`/article/${A.id}`));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.doUnmock("../feedback/reading");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function openReader(): Promise<void> {
  const { ReaderScreen } = await import("./ReaderScreen");
  render(h(ReaderScreen, null), root);
}

function closeReader(): void {
  render(null, root);
}

function likeButton(): HTMLButtonElement | undefined {
  return buttonsNamed(root, /\blike/i).find((b) => !/theme/i.test(b.textContent ?? ""));
}

async function waitForText(text: string): Promise<void> {
  await vi.waitFor(() => expect(root.textContent).toContain(text), { timeout: 2000 });
}

describe("ReaderScreen article text", () => {
  it("a stored article opened online shows the stored text and never calls GET /api/articles/{id}", async () => {
    const router = installRouter({ feed: FEED, articles: { [A.id]: bodyFor(A.id, "network") } });
    const s = await loadStores();
    await s.saveFeed(FEED, '"run-invented-1"');
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));

    await openReader();
    await waitForText(`Invented stored body text for ${A.id}.`);
    await settle(100);

    expect(articleCalls(router)).toEqual([]);
    expect(root.textContent).not.toContain("Invented network body");
  });

  it("an article opened once online shows its text when opened again offline", async () => {
    const router = installRouter({ feed: FEED, articles: { [A.id]: bodyFor(A.id, "network") } });
    const s = await loadStores();
    await s.saveFeed(FEED, '"run-invented-1"');

    await openReader();
    await waitForText(`Invented network body text for ${A.id}.`);
    await vi.waitFor(() => expect(s.getArticleHtml(A.id)).resolves.not.toBeNull());
    closeReader();

    router.online = false;
    await openReader();
    await waitForText(`Invented network body text for ${A.id}.`);
    expect(root.textContent).not.toMatch(/not available offline/i);
    expect(root.textContent).not.toMatch(/could not load/i);
  });

  it("an article never stored, opened offline, shows the title, summary, original link and 'Not available offline', and no error", async () => {
    installRouter({ online: false });
    const s = await loadStores();
    await s.saveFeed(FEED, '"run-invented-1"');

    await openReader();
    await waitForText("Not available offline");
    await waitForText(A.title);
    expect(root.querySelector("h1")?.textContent).toBe(A.title);
    expect(root.textContent).toContain(A.summary!);
    const link = [...root.querySelectorAll("a")].find((a) => a.getAttribute("href") === A.url);
    expect(link).toBeDefined();
    expect(root.textContent).not.toMatch(/could not load/i);
    expect(root.querySelector("[role='alert']")).toBeNull();
  });
});

describe("ReaderScreen like button", () => {
  async function openWithStoredArticle(initial: Partial<FakeRouter>) {
    const router = installRouter({ feed: FEED, ...initial });
    const s = await loadStores();
    await s.saveFeed(FEED, '"run-invented-1"');
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));
    await openReader();
    await waitForText(`Invented stored body text for ${A.id}.`);
    await vi.waitFor(() => expect(likeButton()).toBeDefined());
    return { router, s };
  }

  it("has an accessible name and a pressed state, and shows pressed at once while the send hangs", async () => {
    const { s } = await openWithStoredArticle({ feedback: "hang" });
    const button = likeButton()!;
    expect(button.getAttribute("aria-pressed")).toBe("false");

    button.click();
    await vi.waitFor(() => expect(likeButton()!.getAttribute("aria-pressed")).toBe("true"), {
      timeout: 500,
    });
    const queued = (await queuedEvents(s)).filter((e) => e.event === "like");
    expect(queued).toHaveLength(1);
    expect(queued[0]).toMatchObject({ article_id: A.id, value: 1 });
  });

  it("with no network, a like shows pressed, is queued, and is sent once the network returns", async () => {
    const { router, s } = await openWithStoredArticle({ online: false });
    const { startFeedback } = await import("../feedback/start");
    startFeedback();

    likeButton()!.click();
    await vi.waitFor(() => expect(likeButton()!.getAttribute("aria-pressed")).toBe("true"), {
      timeout: 500,
    });
    await vi.waitFor(async () =>
      expect((await queuedEvents(s)).filter((e) => e.event === "like")).toHaveLength(1),
    );
    expect(feedbackPosts(router)).toEqual([]);

    router.online = true;
    window.dispatchEvent(new Event("online"));
    await vi.waitFor(() =>
      expect(feedbackPosts(router).filter((e) => e.event === "like")).toEqual([
        expect.objectContaining({ article_id: A.id, event: "like", value: 1 }),
      ]),
    );
    await vi.waitFor(() => expect(s.queuedCount()).resolves.toBe(0));
  });

  it("liking from the reader keeps the article's text", async () => {
    await openWithStoredArticle({ online: false });
    likeButton()!.click();
    await vi.waitFor(async () => expect((await articleRecord(A.id))?.keep).toBe(true));
  });

  it("an article already liked opens with the button pressed, and pressing it unlikes", async () => {
    installRouter({ online: false });
    const s = await loadStores();
    await s.saveFeed(FEED, '"run-invented-1"');
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));
    const liked = await import("../feedback/liked");
    await liked.setLiked(A, true);

    await openReader();
    await vi.waitFor(() => expect(likeButton()?.getAttribute("aria-pressed")).toBe("true"));
    likeButton()!.click();
    await vi.waitFor(() => expect(likeButton()!.getAttribute("aria-pressed")).toBe("false"));
    await vi.waitFor(async () => expect(await liked.isLiked(A)).toBe(false));
    const values = (await queuedEvents(s)).filter((e) => e.event === "like").map((e) => e.value);
    expect(values).toEqual([1, 0]);
  });
});

describe("ReaderScreen reading time", () => {
  it("starts reading the open article, and stops when the reader is left", async () => {
    installRouter({ online: false });
    const stop = vi.fn();
    const startReading = vi.fn<(item: { id: string }) => () => void>(() => stop);
    const s = await loadStores();
    vi.doMock("../feedback/reading", () => ({ startReading }));
    await s.saveFeed(FEED, '"run-invented-1"');
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));

    await openReader();
    await vi.waitFor(() => expect(startReading).toHaveBeenCalled());
    expect(startReading.mock.calls.at(-1)![0].id).toBe(A.id);
    expect(stop).not.toHaveBeenCalled();

    closeReader();
    await vi.waitFor(() => expect(stop).toHaveBeenCalled());
    expect(startReading).toHaveBeenCalledTimes(stop.mock.calls.length);
  });
});
