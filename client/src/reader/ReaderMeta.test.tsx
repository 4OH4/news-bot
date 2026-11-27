// @vitest-environment happy-dom
//
// #412: the reader's details line is a MetaLine: domain, author (the only wrapping item), date,
// reading time. Missing fields are left out, with no empty item or stray separator.
import { h, render } from "preact";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  bodyFor,
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
} from "../../test/test-env";
import type { FeedItem } from "../feed/types";

let root: HTMLElement;

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function open(overrides: Partial<FeedItem>): Promise<Element> {
  const item = makeItem("aa11bb22cc33dd44", overrides);
  vi.stubGlobal("location", locationAt(`/article/${item.id}`));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  installRouter({ feed: makeFeed([item]), articles: { [item.id]: bodyFor(item.id, "network") } });
  await loadStores();
  root = document.createElement("div");
  document.body.appendChild(root);
  const { ReaderScreen } = await import("./ReaderScreen");
  render(h(ReaderScreen, null), root);
  await vi.waitFor(() => expect(root.querySelector(".reader-meta")).not.toBeNull());
  return root.querySelector(".reader-meta")!;
}

const texts = (meta: Element) =>
  [...meta.querySelectorAll("span.meta-item")].map((i) =>
    i.textContent!.replaceAll(String.fromCharCode(160), " ").replace(/[\s·]+$/, ""),
  );

describe("reader details line", () => {
  it("has domain, author (wrapping), date and reading time as items, in that order", async () => {
    const meta = await open({ word_count: 400, author: "A. Nobody" });
    const items = [...meta.querySelectorAll("span.meta-item")];
    expect(items).toHaveLength(4);
    expect(texts(meta)[0]).toBe("invented.example.test");
    expect(texts(meta)[1]).toBe("A. Nobody");
    expect(texts(meta)[3]).toBe("2 min read");
    expect(items.map((i) => i.classList.contains("meta-wrap"))).toEqual([false, true, false, false]);
  });

  it("leaves out a missing author and reading time, with no empty item or stray separator", async () => {
    const meta = await open({ author: null, word_count: null });
    expect(meta.querySelectorAll("span.meta-item")).toHaveLength(2);
    const text = meta.textContent!.replaceAll(String.fromCharCode(160), " ").trim();
    expect(text.match(/·/g)).toHaveLength(1);
    expect(text.startsWith("invented.example.test · ")).toBe(true);
    expect(text.endsWith("·")).toBe(false);
  });
});
