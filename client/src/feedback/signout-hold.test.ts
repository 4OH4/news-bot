// @vitest-environment happy-dom
//
// An event enqueued while the sign-out dialog is open is written at once, so closing the page
// can't lose it, and a sign-out deletes only the events the dialog counted.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeLocation, installFakeApi, loadApp, settle } from "./test-env";

const A = "f0f0f0f0f0f0f0f1";
const B = "f0f0f0f0f0f0f0f2";

let loc: ReturnType<typeof fakeLocation>;

beforeEach(() => {
  loc = fakeLocation();
  vi.stubGlobal("location", loc);
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function buttons(name: RegExp): HTMLButtonElement[] {
  return [...document.querySelectorAll("button")].filter((b) => name.test(b.textContent ?? ""));
}

async function click(name: RegExp): Promise<void> {
  await vi.waitFor(() => expect(buttons(name).length).toBeGreaterThan(0));
  buttons(name)[0]!.click();
}

/** Queued ids in queue order (`seq`), not key order: the ids are random. */
async function queuedIds(f: Awaited<ReturnType<typeof loadApp>>): Promise<string[]> {
  return (await (await f.openFeedbackDb()).getAllFromIndex("queue", "seq")).map((row) => row.id);
}

/** Fresh modules wired as `startFeedback` does, offline, with one queued event (A). */
async function withOneQueued() {
  const f = await loadApp();
  installFakeApi("offline");
  f.startFeedback();
  const first = await f.enqueueFeedback(A, "open");
  await settle();
  return { f, first };
}

describe("an event enqueued while the sign-out dialog is open", () => {
  it("is stored at once, before the owner answers", async () => {
    const { f, first } = await withOneQueued();
    const done = f.signOut();
    await vi.waitFor(() => expect(buttons(/cancel/i).length).toBeGreaterThan(0));
    const late = await f.enqueueFeedback(B, "like", 1);
    expect(await queuedIds(f)).toEqual([first.id, late.id]);
    await click(/cancel/i);
    await done;
    expect(await queuedIds(f)).toEqual([first.id, late.id]);
  });

  it("after discard, is kept while the events the dialog counted are deleted", async () => {
    const { f } = await withOneQueued();
    const done = f.signOut();
    await vi.waitFor(() => expect(buttons(/discard/i).length).toBeGreaterThan(0));
    const late = await f.enqueueFeedback(B, "like", 1);
    await click(/discard/i);
    await done;
    expect(await queuedIds(f)).toEqual([late.id]);
    expect(loc.replace).toHaveBeenCalledWith("/");
  });
});

describe("signOut tapped twice while the dialog is open", () => {
  it("shows one dialog, and both taps get its answer", async () => {
    const { f, first } = await withOneQueued();
    const hook = vi.fn(async () => {});
    f.onSignOut(hook);
    const one = f.signOut();
    await vi.waitFor(() => expect(buttons(/cancel/i)).toHaveLength(1));
    const two = f.signOut();
    await settle();
    expect(buttons(/cancel/i)).toHaveLength(1);
    await click(/cancel/i);
    await Promise.all([one, two]);
    expect(hook).not.toHaveBeenCalled();
    expect(loc.replace).not.toHaveBeenCalled();
    expect(await queuedIds(f)).toEqual([first.id]);
  });
});
