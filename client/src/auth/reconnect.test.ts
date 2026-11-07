import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * The module registers its `pageshow` and `visibilitychange` listeners when it loads (the
 * issue names no install function), so the fake window and document exist before the import.
 */
const win = new EventTarget();
const doc = Object.assign(new EventTarget(), { visibilityState: "visible" });
const location = { href: "/", replace: vi.fn(), assign: vi.fn() };

async function load() {
  vi.resetModules();
  const reconnect = await import("./reconnect");
  const store = await import("./status");
  const status = (): unknown => {
    const v: unknown = store.sessionStatus;
    return typeof v === "function" ? (v as () => unknown)() : v;
  };
  return { ...reconnect, ...store, status };
}

function stubFetch(impl: (...args: unknown[]) => unknown) {
  const spy = vi.fn(impl);
  vi.stubGlobal("fetch", spy);
  return spy;
}

function pageshow(persisted: boolean) {
  const ev = new Event("pageshow");
  Object.defineProperty(ev, "persisted", { value: persisted });
  win.dispatchEvent(ev);
}

function becomeVisible() {
  doc.visibilityState = "visible";
  doc.dispatchEvent(new Event("visibilitychange"));
}

/** Lets the re-check's promise chain settle. */
const settle = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  location.href = "/";
  location.replace.mockReset();
  location.assign.mockReset();
  doc.visibilityState = "visible";
  vi.stubGlobal("location", location);
  vi.stubGlobal("navigator", { onLine: true });
  vi.stubGlobal("window", win);
  vi.stubGlobal("document", doc);
  vi.stubGlobal("addEventListener", win.addEventListener.bind(win));
  vi.stubGlobal("removeEventListener", win.removeEventListener.bind(win));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reconnect", () => {
  it("RECONNECT_PATH is /reconnect", async () => {
    const { RECONNECT_PATH } = await load();
    expect(RECONNECT_PATH).toBe("/reconnect");
  });

  it("navigates to /reconnect and to nothing else", async () => {
    const { reconnect } = await load();
    reconnect();
    expect(location.href).toBe("/reconnect");
    expect(location.replace).not.toHaveBeenCalled();
    expect(location.assign).not.toHaveBeenCalled();
  });
});

describe("recheckSession", () => {
  it("clears the banner after pageshow with persisted true when /api/feed answers 200", async () => {
    const { reportAuthRequired, status } = await load();
    reportAuthRequired();
    const spy = stubFetch(() => new Response("{}", { status: 200 }));
    pageshow(true);
    await settle();
    expect(String(spy.mock.calls[0]?.[0])).toBe("/api/feed");
    expect((spy.mock.calls[0]?.[1] as RequestInit).cache).toBe("no-store");
    expect(status()).toBe("ok");
  });

  it("keeps the banner and navigates nowhere when the page becomes visible and /api/feed answers 401", async () => {
    const { reportAuthRequired, status } = await load();
    reportAuthRequired();
    const spy = stubFetch(() => new Response("", { status: 401 }));
    becomeVisible();
    await settle();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(status()).toBe("reconnect");
    expect(location.href).toBe("/");
    expect(location.replace).not.toHaveBeenCalled();
  });

  it("makes no request while the browser is offline and keeps the banner", async () => {
    const { reportAuthRequired, status } = await load();
    reportAuthRequired();
    vi.stubGlobal("navigator", { onLine: false });
    const spy = stubFetch(() => new Response("{}", { status: 200 }));
    becomeVisible();
    pageshow(true);
    await settle();
    expect(spy).not.toHaveBeenCalled();
    expect(status()).toBe("reconnect");
  });

  it("keeps the banner when the re-check is offline", async () => {
    const { reportAuthRequired, status } = await load();
    reportAuthRequired();
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    becomeVisible();
    await settle();
    expect(status()).toBe("reconnect");
    expect(location.href).toBe("/");
  });

  it("makes no request when the status is ok", async () => {
    const { recheckSession } = await load();
    const spy = stubFetch(() => new Response("{}", { status: 200 }));
    becomeVisible();
    pageshow(false);
    pageshow(true);
    await recheckSession();
    await settle();
    expect(spy).not.toHaveBeenCalled();
  });

  it("runs at most one re-check at a time", async () => {
    const { reportAuthRequired } = await load();
    reportAuthRequired();
    let release: (r: Response) => void = () => {};
    const spy = stubFetch(() => new Promise<Response>((r) => (release = r)));
    becomeVisible();
    pageshow(true);
    becomeVisible();
    await settle();
    expect(spy).toHaveBeenCalledTimes(1);
    release(new Response("{}", { status: 200 }));
    await settle();
  });
});
