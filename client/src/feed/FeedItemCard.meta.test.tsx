// @vitest-environment happy-dom
//
// #412: the card's details line is a MetaLine whose items are the domain link, the time and
// "N sources" (coverage 2 or more), none of them wrapping.
import { h, render } from "preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt, makeItem } from "../../test/test-env";
import type { FeedItem } from "./types";

let root: HTMLElement;

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const threeHoursAgo = () => new Date(Date.now() - 3 * 3600_000).toISOString();

async function show(overrides: Partial<FeedItem>): Promise<Element> {
  vi.stubGlobal("location", locationAt("/"));
  const item = makeItem("a1b2c3d4e5f60718", { published_at: threeHoursAgo(), ...overrides });
  installRouter({ online: false });
  await loadStores();
  const { FeedItemCard } = await import("./FeedItemCard");
  root = document.createElement("div");
  document.body.appendChild(root);
  render(h(FeedItemCard, { item }), root);
  await vi.waitFor(() => expect(root.querySelector(".card-meta")).not.toBeNull());
  return root.querySelector(".card-meta")!;
}

describe("card details line", () => {
  it("has the domain link, time and sources as whole items, and the link opens in a new tab", async () => {
    const meta = await show({ coverage: 3 });
    const items = [...meta.querySelectorAll("span.meta-item")];
    expect(items).toHaveLength(3);
    expect(items.some((i) => i.classList.contains("meta-wrap"))).toBe(false);
    const link = items[0]!.querySelector("a")!;
    expect(link.textContent).toBe("invented.example.test");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(items[2]!.textContent).toContain("3 sources");
  });

  it("has only the domain and time when coverage is below 2", async () => {
    const meta = await show({ coverage: 1 });
    expect(meta.querySelectorAll("span.meta-item")).toHaveLength(2);
  });
});
