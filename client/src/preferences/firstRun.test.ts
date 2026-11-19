// @vitest-environment happy-dom
//
// #338: the first-run check on its own. The feed's first load and the preferences GET are
// stand-ins, so each guard can be checked: it waits for the first feed load, skips while the
// session needs attention, and asks the server once per session after it has answered.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PreferencesResponse } from "./types";

let feedLoaded: () => void;
let getPreferences: ReturnType<typeof vi.fn<() => Promise<PreferencesResponse>>>;
let replace: ReturnType<typeof vi.fn>;

const answer = (setup_done: boolean) =>
  Promise.resolve({ setup_done } as unknown as PreferencesResponse);

/** Fresh modules: a pending first feed load and a stubbed GET. */
async function load() {
  vi.resetModules();
  const firstFeedLoad = new Promise<void>((resolve) => {
    feedLoaded = resolve;
  });
  vi.doMock("../feed/useFeed", () => ({ firstFeedLoad }));
  vi.doMock("./api", () => ({ getPreferences }));
  const status = await import("../auth/status");
  const firstRun = await import("./firstRun");
  return { ...status, ...firstRun };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  vi.stubGlobal("navigator", { onLine: true });
  replace = vi.fn();
  vi.stubGlobal("location", { replace });
  getPreferences = vi.fn(() => answer(false));
  sessionStorage.clear();
});

afterEach(() => {
  vi.doUnmock("../feed/useFeed");
  vi.doUnmock("./api");
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("openSetupIfNeeded", () => {
  it("asks nothing until the first feed load has an answer", async () => {
    const { openSetupIfNeeded } = await load();
    const done = openSetupIfNeeded();
    await flush();
    expect(getPreferences).not.toHaveBeenCalled();
    feedLoaded();
    await done;
    expect(getPreferences).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/settings/preferences?setup=1");
  });

  it("does nothing when the first feed load left the session needing Reconnect", async () => {
    const { openSetupIfNeeded, reportAuthRequired } = await load();
    const done = openSetupIfNeeded();
    // The feed's 401 arrives during the first load.
    reportAuthRequired();
    feedLoaded();
    await done;
    expect(getPreferences).not.toHaveBeenCalled();
    expect(replace).not.toHaveBeenCalled();
  });

  it("opens setup once: a second feed load in the session neither asks nor redirects", async () => {
    const { openSetupIfNeeded } = await load();
    feedLoaded();
    await openSetupIfNeeded();
    await openSetupIfNeeded();
    expect(getPreferences).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledTimes(1);
  });

  it("once setup is done, later feed loads in the session send no request", async () => {
    getPreferences = vi.fn(() => answer(true));
    const { openSetupIfNeeded } = await load();
    feedLoaded();
    await openSetupIfNeeded();
    await openSetupIfNeeded();
    expect(getPreferences).toHaveBeenCalledTimes(1);
    expect(replace).not.toHaveBeenCalled();
  });

  it("after a failed request the next feed load asks again", async () => {
    getPreferences = vi.fn(() => Promise.reject(new Error("500")));
    const { openSetupIfNeeded } = await load();
    feedLoaded();
    await openSetupIfNeeded();
    getPreferences.mockImplementation(() => answer(false));
    await openSetupIfNeeded();
    expect(getPreferences).toHaveBeenCalledTimes(2);
    expect(replace).toHaveBeenCalledTimes(1);
  });
});
