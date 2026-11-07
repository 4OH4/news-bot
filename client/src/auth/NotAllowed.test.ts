// @vitest-environment happy-dom
//
// The not-allowed screen's sign-out button goes through the feedback guard (#184).
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fakeLocation,
  fillStoryState,
  installFakeApi,
  loadFeedback,
  settle,
  storyStateCounts,
} from "../feedback/test-env";

let loc: ReturnType<typeof fakeLocation>;
let root: HTMLElement;

beforeEach(() => {
  loc = fakeLocation();
  vi.stubGlobal("location", loc);
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

function buttons(name: RegExp): HTMLButtonElement[] {
  return [...document.querySelectorAll("button")].filter((b) =>
    name.test(`${b.textContent ?? ""} ${b.getAttribute("aria-label") ?? ""}`),
  );
}

/** Fresh modules with the feedback start-up wiring, then the not-allowed screen rendered. */
async function showNotAllowed() {
  const f = await loadFeedback();
  const api = installFakeApi(403);
  f.startFeedback();
  await settle();
  const hook = vi.fn(async () => {});
  f.onSignOut(hook);
  f.reportNotAllowed();
  const { NotAllowed } = await import("./NotAllowed");
  render(h(NotAllowed, null), root);
  return { f, api, hook };
}

describe("NotAllowed sign-out", () => {
  it("with an empty queue, goes ahead: the hooks run and the page is replaced with /", async () => {
    const { f, hook } = await showNotAllowed();
    await fillStoryState(f);
    buttons(/sign out/i)[0]!.click();
    await vi.waitFor(() => expect(loc.replace).toHaveBeenCalledWith("/"));
    expect(hook).toHaveBeenCalledTimes(1);
    expect(await storyStateCounts(f)).toEqual({ seen: 0, dismissed: 0, muted: 0, liked: 0 });
  });

  it("with queued events, shows send and cancel but no discard; send gets 403, nothing navigates and nothing is cleared", async () => {
    const { f, api, hook } = await showNotAllowed();
    api.answer = () => "offline";
    const event = await f.enqueueFeedback("f0f0f0f0f0f0f0f1", "open");
    await settle();
    await fillStoryState(f);
    api.answer = () => 403;

    buttons(/sign out/i)[0]!.click();
    await vi.waitFor(() => expect(buttons(/cancel/i).length).toBeGreaterThan(0));
    expect(buttons(/send/i).length).toBeGreaterThan(0);
    expect(buttons(/discard/i)).toHaveLength(0);

    const before = api.posts.length;
    buttons(/send/i)[0]!.click();
    await vi.waitFor(() => expect(api.posts.length).toBeGreaterThan(before));
    await settle();
    expect(api.posts.at(-1)?.id).toBe(event.id);
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
    expect(api.other.filter((u) => u.includes("gcp-iap-mode"))).toEqual([]);
    expect(await f.queuedCount()).toBe(1);
    expect(await storyStateCounts(f)).toEqual({ seen: 1, dismissed: 1, muted: 1, liked: 1 });
  });
});
