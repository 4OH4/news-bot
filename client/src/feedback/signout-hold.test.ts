// @vitest-environment happy-dom
//
// What happens to an event enqueued while the sign-out dialog is open: it waits, and is stored
// once the owner cancels or, after a discard, once the stores have been cleared.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeLocation, installFakeApi, loadApp, settle } from "./test-env";

const A = "f0f0f0f0f0f0f0f1";
const B = "f0f0f0f0f0f0f0f2";

beforeEach(() => {
  vi.stubGlobal("location", fakeLocation());
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function click(name: RegExp): Promise<void> {
  const find = () =>
    [...document.querySelectorAll("button")].filter((b) => name.test(b.textContent ?? ""));
  await vi.waitFor(() => expect(find().length).toBeGreaterThan(0));
  find()[0]!.click();
}

async function queuedIds(f: Awaited<ReturnType<typeof loadApp>>): Promise<string[]> {
  return (await (await f.openFeedbackDb()).getAll("queue")).map((row) => row.id);
}

describe("an event enqueued while the sign-out dialog is open", () => {
  it("waits, and is stored as normal once the owner cancels", async () => {
    const f = await loadApp();
    installFakeApi("offline");
    f.startFeedback();
    const first = await f.enqueueFeedback(A, "open");
    await settle();
    const done = f.signOut();
    await vi.waitFor(() => expect(document.body.textContent).toMatch(/cancel/i));
    // Enqueued after the dialog opened: held until the answer.
    let stored = false;
    const late = f.enqueueFeedback(B, "like", 1).then((e) => {
      stored = true;
      return e;
    });
    await settle();
    expect(stored).toBe(false);
    await click(/cancel/i);
    await done;
    const event = await late;
    expect(stored).toBe(true);
    expect(await queuedIds(f)).toEqual([first.id, event.id]);
  });

  it("after discard, is kept for the next start while the discarded events are cleared", async () => {
    const f = await loadApp();
    installFakeApi("offline");
    f.startFeedback();
    await f.enqueueFeedback(A, "open");
    await settle();
    const done = f.signOut();
    await vi.waitFor(() => expect(document.body.textContent).toMatch(/discard/i));
    let stored = false;
    const late = f.enqueueFeedback(B, "like", 1).then((e) => {
      stored = true;
      return e;
    });
    await settle();
    expect(stored).toBe(false);
    await click(/discard/i);
    await done;
    const event = await late;
    expect(await queuedIds(f)).toEqual([event.id]);
  });
});
