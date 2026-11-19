// @vitest-environment happy-dom
//
// #339: on the first online load, sources muted on the phone before this change are PUT as Hide
// in one request, once. Every name is invented.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installPrefs, makeMenu } from "../../test/preferences-env";
import { loadStores, locationAt, makeFeed, makeItem, settle } from "../../test/test-env";
import { installStorage } from "../feedback/test-env";

const SHOWN = makeItem("9f8e7d6c5b4a3f2e", {
  title: "Invented feed story about ferries",
  source_id: "invented-wire",
});
const MUTED = ["bbc-news", "wired"].map((source_id, i) =>
  makeItem(`aa00bb11cc22dd3${i}`, { source_id, domain: `${source_id}.example.test` }),
);

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("__APP_VERSION__", "test");
  vi.stubGlobal("location", locationAt("/"));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  installStorage();
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** A load of the feed screen with `MUTED` in the phone's old `muted` store; the flag persists. */
async function loadWithOldMutes() {
  const fake = installPrefs(makeMenu({ version: 3 }));
  fake.router.feed = makeFeed([SHOWN]);
  const s = await loadStores();
  const db = await s.openFeedbackDb();
  for (const item of MUTED) {
    await db.put("muted", { source_id: item.source_id, item: item as never, at: "2026-11-01T00:00:00Z" });
  }
  const { FeedScreen } = await import("./FeedScreen");
  render(h(FeedScreen, null), root);
  await vi.waitFor(() => expect(root.textContent).toContain(SHOWN.title), { timeout: 2000 });
  await settle(150);
  render(null, root);
  return fake;
}

describe("migrating the phone's muted sources", () => {
  it("one PUT hides both on the first load, and the next load sends none for them", async () => {
    const first = await loadWithOldMutes();

    expect(first.puts).toHaveLength(1);
    expect(first.puts[0]!.body.sources).toEqual({ "bbc-news": "hide", wired: "hide" });
    expect(localStorage.getItem("muted-migrated")).toBeTruthy();

    const second = await loadWithOldMutes();
    expect(second.puts).toEqual([]);
  });

  it("offline, nothing is sent and the flag stays unset, so the next online load migrates", async () => {
    const offline = installPrefs(makeMenu(), { online: false });
    offline.router.feed = makeFeed([SHOWN]);
    const s = await loadStores();
    const db = await s.openFeedbackDb();
    await db.put("muted", { source_id: "wired", item: MUTED[1] as never, at: "2026-11-01T00:00:00Z" });
    const { FeedScreen } = await import("./FeedScreen");
    render(h(FeedScreen, null), root);
    await settle(150);
    render(null, root);
    expect(offline.puts).toEqual([]);
    expect(localStorage.getItem("muted-migrated")).toBeNull();

    const online = await loadWithOldMutes();
    expect(online.puts).toHaveLength(1);
  });
});
