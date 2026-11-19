// @vitest-environment happy-dom
//
// Cards pass their screen's view and the item's A/B arm to the feedback they record (#327).
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { chooseAction } from "../../test/cards";
import {
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
  queuedEvents,
  type Stores,
} from "../../test/test-env";
import type { FeedItem, ViewName } from "./types";

const PERSONAL = makeItem("a1b2c3d4e5f60718", {
  title: "Invented story about canal locks",
  arm: "personal",
});
const PLAIN = makeItem("b2c3d4e5f6071829", { title: "Invented story about bell ringers" });

let root: HTMLElement;

beforeEach(() => {
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  sessionStorage.clear();
  localStorage.clear(); // #339: a mute left in the pending-source-states outbox must not leak between tests
  root = document.createElement("div");
  document.body.appendChild(root);
  root.addEventListener("click", (e) => e.preventDefault());
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function show(item: FeedItem, view: ViewName, search = ""): Promise<Stores> {
  vi.stubGlobal("location", { ...locationAt("/"), search });
  installRouter({ feed: makeFeed([item]) });
  const s = await loadStores();
  const { FeedScreen } = await import("./FeedScreen");
  render(h(FeedScreen, { view }), root);
  await vi.waitFor(() => expect(root.textContent).toContain(item.title));
  return s;
}

function titleLink(item: FeedItem): HTMLAnchorElement {
  return [...root.querySelectorAll("a")].find((a) => a.textContent === item.title)!;
}

async function eventOf(s: Stores, name: string): Promise<Record<string, unknown>> {
  let found: Record<string, unknown> | undefined;
  await vi.waitFor(async () => {
    found = (await queuedEvents(s)).find((e) => e.event === name);
    expect(found).toBeDefined();
  });
  return found!;
}

describe("view and arm on card events", () => {
  it("an open from the Feed carries view feed and the item's arm", async () => {
    const s = await show(PERSONAL, "feed");
    titleLink(PERSONAL).click();
    expect(await eventOf(s, "open")).toMatchObject({ view: "feed", arm: "personal" });
  });

  it("an open of an item with no arm carries the view and no arm key", async () => {
    const s = await show(PLAIN, "news");
    titleLink(PLAIN).click();
    const open = await eventOf(s, "open");
    expect(open.view).toBe("news");
    expect("arm" in open).toBe(false);
  });

  it.each([
    ["dismiss", /dismiss/i],
    ["mute_source", /mute/i],
  ])("a %s carries view and arm", async (event, label) => {
    const s = await show(PERSONAL, "longer");
    await chooseAction(root, label);
    expect(await eventOf(s, event)).toMatchObject({ view: "longer", arm: "personal" });
  });

  it("a screen opened with ?ranking= sends the view but no arm", async () => {
    const s = await show(PERSONAL, "feed", "?ranking=rules");
    titleLink(PERSONAL).click();
    const open = await eventOf(s, "open");
    expect(open.view).toBe("feed");
    expect("arm" in open).toBe(false);
  });

  it("tapping an article title writes reader-context to sessionStorage inside the click", async () => {
    await show(PERSONAL, "feed");
    titleLink(PERSONAL).click();
    expect(JSON.parse(sessionStorage.getItem("reader-context") ?? "null")).toEqual({
      id: PERSONAL.id,
      view: "feed",
      arm: "personal",
    });
  });
});
