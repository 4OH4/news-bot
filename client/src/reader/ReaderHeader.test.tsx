// @vitest-environment happy-dom
//
// #300: the reader has no top bar. The article starts with its headline, and Like is an outline
// heart button beside the meta line, named "Like" in both states with aria-pressed for the state.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bodyFor,
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
} from "../../test/test-env";

const A = makeItem("aa11bb22cc33dd44", { title: "Invented headline about canal boats" });
const FEED = makeFeed([A]);

let root: HTMLElement;

beforeEach(async () => {
  sessionStorage.clear();
  vi.stubGlobal("location", locationAt(`/article/${A.id}`));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  installRouter({ feed: FEED, articles: { [A.id]: bodyFor(A.id, "network") } });
  await loadStores();
  root = document.createElement("div");
  document.body.appendChild(root);
  const { ReaderScreen } = await import("./ReaderScreen");
  render(h(ReaderScreen, null), root);
  await vi.waitFor(() => expect(root.querySelector("button.reader-like")).not.toBeNull());
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const like = () => root.querySelector<HTMLButtonElement>("button.reader-like")!;

describe("ReaderScreen header", () => {
  it("has no nav bar, back link or theme button, and starts with the title heading", () => {
    expect(root.querySelector("nav")).toBeNull();
    expect(root.querySelector(".reader-bar")).toBeNull();
    expect(root.textContent).not.toMatch(/back to/i);
    expect(root.textContent).not.toMatch(/switch to (dark|light) theme/i);
    expect(root.querySelector("h1")?.textContent).toBe(A.title);
    expect(root.querySelector("main")!.firstElementChild!.tagName).toBe("HEADER");
  });

  it("Like is an icon button named Like with a hidden inline SVG heart and no visible text", () => {
    expect(like().getAttribute("aria-label")).toBe("Like");
    expect(like().getAttribute("aria-pressed")).toBe("false");
    expect(like().querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(like().textContent?.trim()).toBe("");
  });

  it("keeps the name Like when liked and flips aria-pressed", async () => {
    like().click();
    await vi.waitFor(() => expect(like().getAttribute("aria-pressed")).toBe("true"));
    expect(like().getAttribute("aria-label")).toBe("Like");
    expect(like().textContent?.trim()).toBe("");
  });

  it("places the title, then the meta line, then the Like button in the header", () => {
    const parts = [...root.querySelectorAll("header h1, header .reader-meta, header .reader-like")];
    expect(parts.map((e) => e.className)).toEqual(["reader-title", "reader-meta", "reader-like"]);
  });
});
