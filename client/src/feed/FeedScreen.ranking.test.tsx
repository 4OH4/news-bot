// @vitest-environment happy-dom
//
// The compare-ranking Feed (#328): `?ranking=` goes to the API, a label says which order is on,
// and these lists never replace the normal Feed's stored copy.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt, makeFeed, makeItem } from "../../test/test-env";

const NORMAL = makeItem("a1b2c3d4e5f60718", { title: "Invented normal story about locks" });
const RULES = makeItem("b2c3d4e5f6071829", { title: "Invented rules story about bells" });

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

function feedRequests(): URL[] {
  const fetchMock = fetch as unknown as { mock: { calls: unknown[][] } };
  return fetchMock.mock.calls
    .map((c) => new URL(String(c[0]), "http://localhost"))
    .filter((u) => u.pathname === "/api/feed");
}

async function show(search: string, item = NORMAL) {
  vi.stubGlobal("location", { ...locationAt("/"), search });
  installRouter({ feed: makeFeed([item]) });
  const s = await loadStores();
  const { FeedScreen } = await import("./FeedScreen");
  render(h(FeedScreen, null), root);
  await vi.waitFor(() => expect(root.textContent).toContain(item.title));
  return s;
}

describe("Feed with ?ranking=", () => {
  it.each([
    ["personal", "Personalised only"],
    ["rules", "Rules only"],
  ])("ranking=%s goes to the API and shows '%s'", async (ranking, label) => {
    await show(`?ranking=${ranking}`);
    expect(feedRequests()[0]!.searchParams.get("ranking")).toBe(ranking);
    expect(root.textContent).toContain(label);
    const back = [...root.querySelectorAll("a")].find((a) => a.getAttribute("href") === "/");
    expect(back).toBeDefined();
  });

  it("the normal Feed sends no ranking and shows no label", async () => {
    await show("");
    expect(feedRequests()[0]!.searchParams.has("ranking")).toBe(false);
    expect(root.textContent).not.toMatch(/Rules only|Personalised only/);
  });

  it("does not overwrite the normal Feed's stored list", async () => {
    const s = await show("");
    await vi.waitFor(async () => expect(await s.loadFeed("feed")).toBeTruthy());
    render(null, root);
    vi.stubGlobal("location", { ...locationAt("/"), search: "?ranking=rules" });
    installRouter({ feed: makeFeed([RULES], "run-invented-2") });
    const { FeedScreen } = await import("./FeedScreen");
    render(h(FeedScreen, null), root);
    await vi.waitFor(() => expect(root.textContent).toContain(RULES.title));
    const stored = await s.loadFeed("feed");
    expect(stored?.feed.items.map((i) => i.id)).toEqual([NORMAL.id]);
  });
});
