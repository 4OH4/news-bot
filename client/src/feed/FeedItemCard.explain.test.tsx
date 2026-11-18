// @vitest-environment happy-dom
//
// "Why this story?" in the card menu and the "N sources" note (#328).
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chooseAction } from "../../test/cards";
import { installRouter, loadStores, locationAt, makeItem } from "../../test/test-env";
import type { FeedItem } from "./types";

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("location", locationAt("/"));
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

async function show(overrides: Partial<FeedItem>): Promise<void> {
  const item = makeItem("a1b2c3d4e5f60718", {
    title: "Invented story about canal locks",
    ...overrides,
  });
  installRouter({ online: false });
  await loadStores();
  const { FeedItemCard } = await import("./FeedItemCard");
  render(h(FeedItemCard, { item }), root);
  await vi.waitFor(() => expect(root.textContent).toContain(item.title));
}

const meta = () => root.querySelector(".card-meta")?.textContent ?? "";

describe("card coverage note", () => {
  it("shows '3 sources' in the meta line when coverage is 3", async () => {
    await show({ coverage: 3 });
    expect(meta()).toContain("3 sources");
  });

  it.each([[undefined], [1]])("shows no sources note when coverage is %s", async (coverage) => {
    await show({ coverage });
    expect(meta()).not.toMatch(/sources/);
  });
});

describe("Why this story?", () => {
  const breakdown = { "topic:ai": 6, recency: 0.8, coverage: 1.27, coverage_sources: 3 };

  it("opens a list of the score components in words, using the item's coverage", async () => {
    await show({ score: 5.6, score_breakdown: breakdown, coverage: 2 });
    expect(root.textContent).not.toContain("Matches ai");
    await chooseAction(root, /why this story/i);
    await vi.waitFor(() => expect(root.textContent).toContain("Matches ai"));
    expect(root.textContent).toContain("Freshness");
    expect(root.textContent).toContain("Covered by 2 sources");
    expect(root.textContent).toContain("5.6");
  });

  it.each([
    ["personal", "Placed by: personalised"],
    ["rules", "Placed by: rules"],
  ] as const)("footer for arm %s", async (arm, text) => {
    await show({ score_breakdown: breakdown, arm });
    await chooseAction(root, /why this story/i);
    await vi.waitFor(() => expect(root.textContent).toContain(text));
  });

  it("has no Placed by footer when the item has no arm", async () => {
    await show({ score_breakdown: breakdown });
    await chooseAction(root, /why this story/i);
    await vi.waitFor(() => expect(root.textContent).toContain("Matches ai"));
    expect(root.textContent).not.toContain("Placed by");
  });
});
