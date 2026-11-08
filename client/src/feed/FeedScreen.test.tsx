// @vitest-environment happy-dom
//
// The compact feed header (#272): the title with "Updated ..." under it, and a round icon
// Refresh button on the right. Sizes and animations are checked in e2e/navigation.spec.ts,
// because happy-dom computes no layout.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, makeFeed, makeItem } from "../../test/test-env";

const ITEM = makeItem("9f8e7d6c5b4a3f2e", { title: "Invented feed story about ferries" });

let root: HTMLElement;

beforeEach(() => {
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const refreshButton = () => root.querySelector<HTMLButtonElement>('button[aria-label="Refresh"]');

async function showFeed(): Promise<void> {
  installRouter({ feed: makeFeed([ITEM]) });
  await loadStores();
  const { FeedScreen } = await import("./FeedScreen");
  render(h(FeedScreen, null), root);
  await vi.waitFor(() => expect(root.textContent).toContain(ITEM.title), { timeout: 2000 });
  await vi.waitFor(() => expect(root.textContent).toMatch(/Updated/), { timeout: 2000 });
  // Whatever the control looks like, wait until the first refresh is done.
  await vi.waitFor(
    () => expect(root.querySelector<HTMLButtonElement>("header button")?.disabled).toBe(false),
    { timeout: 2000 },
  );
}

describe("compact feed header", () => {
  it("has one header row with the title, the Updated line under it, and the refresh button after", async () => {
    await showFeed();
    const row = root.querySelector("header.top")!;
    expect(row).not.toBeNull();
    const title = row.querySelector("h1")!;
    expect(title.textContent).toBe("Newsfeed");
    const updated = [...row.querySelectorAll("*")].find((e) => /^Updated /.test(e.textContent ?? "") && e.children.length === 0);
    expect(updated).toBeDefined();
    // Same block as the heading, and after it.
    expect(title.parentElement?.contains(updated!)).toBe(true);
    expect(title.parentElement).not.toBe(row);
    expect(title.compareDocumentPosition(updated!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // The button is in the row, outside the title block, after it.
    const button = refreshButton()!;
    expect(row.contains(button)).toBe(true);
    expect(title.parentElement?.contains(button)).toBe(false);
    expect(title.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("has no Refresh text button; the one Refresh button is an icon with an inline SVG", async () => {
    await showFeed();
    const named = [...root.querySelectorAll("button")].filter(
      (b) => b.getAttribute("aria-label") === "Refresh" || b.textContent?.trim() === "Refresh",
    );
    expect(named).toHaveLength(1);
    const button = named[0]!;
    expect(button.getAttribute("aria-label")).toBe("Refresh");
    expect(button.textContent?.trim()).toBe("");
    expect(button.querySelector("svg")).not.toBeNull();
    // CSP style-src 'self' blocks inline style attributes.
    expect(button.querySelector("[style]")).toBeNull();
    expect(button.hasAttribute("style")).toBe(false);
  });

  it("is disabled with a spinning icon while a refresh runs, then enabled again with a new time", async () => {
    await showFeed();
    const before = root.querySelector("header.top")!.textContent;
    const real = fetch;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
      gate.then(() => real(input, init)),
    );
    refreshButton()!.click();
    await vi.waitFor(() => expect(refreshButton()!.disabled).toBe(true), { timeout: 2000 });
    expect(refreshButton()!.querySelector("svg")?.getAttribute("class") ?? "").toMatch(/spin/i);
    release();
    await vi.waitFor(() => expect(refreshButton()!.disabled).toBe(false), { timeout: 2000 });
    expect(refreshButton()!.querySelector("svg")?.getAttribute("class") ?? "").not.toMatch(/spin/i);
    expect(root.querySelector("header.top")!.textContent).toMatch(/Updated/);
    expect(before).toMatch(/Updated/);
  });

  it("keeps the offline notice below the header row", async () => {
    installRouter({ feed: makeFeed([ITEM]) });
    const s = await loadStores();
    await s.saveFeed(makeFeed([ITEM]), '"e"');
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    const { FeedScreen } = await import("./FeedScreen");
    render(h(FeedScreen, null), root);
    await vi.waitFor(() => expect(root.textContent).toContain("You are offline"), { timeout: 2000 });
    const row = root.querySelector("header.top")!;
    const notice = root.querySelector('[role="status"]')!;
    expect(row.contains(notice)).toBe(false);
    expect(row.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
