import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const events: string[] = [];
const location = {
  _href: "/",
  get href() {
    return this._href;
  },
  set href(v: string) {
    events.push(`href:${v}`);
    this._href = v;
  },
  replace: vi.fn((url: string) => {
    events.push(`replace:${url}`);
  }),
  assign: vi.fn((url: string) => {
    events.push(`assign:${url}`);
  }),
};

async function load() {
  vi.resetModules();
  return import("./signout");
}

function stubFetch(impl: (...args: unknown[]) => unknown) {
  const spy = vi.fn((...args: unknown[]) => {
    events.push(`fetch:${String(args[0])}`);
    return impl(...args);
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

beforeEach(() => {
  events.length = 0;
  location._href = "/";
  location.replace.mockClear();
  location.assign.mockClear();
  vi.stubGlobal("location", location);
  vi.stubGlobal("navigator", { onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const CLEAR = "/?gcp-iap-mode=CLEAR_LOGIN_COOKIE";

describe("signOut", () => {
  it("runs the hook, then fetches the clear-cookie URL, then replaces the page with /", async () => {
    const { signOut, onSignOut } = await load();
    onSignOut(async () => {
      events.push("hook");
    });
    stubFetch(() => new Response("", { status: 200 }));
    await signOut();
    expect(events).toEqual(["hook", `fetch:${CLEAR}`, "replace:/"]);
  });

  it("requests the clear-cookie URL with same-origin credentials, manual redirect and no cache", async () => {
    const { signOut } = await load();
    const spy = stubFetch(() => new Response("", { status: 200 }));
    await signOut();
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("same-origin");
    expect(init.redirect).toBe("manual");
    expect(init.cache).toBe("no-store");
  });

  it("runs every registered hook", async () => {
    const { signOut, onSignOut } = await load();
    const a = vi.fn(async () => {});
    const b = vi.fn(async () => {});
    onSignOut(a);
    onSignOut(b);
    stubFetch(() => new Response("", { status: 200 }));
    await signOut();
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it("never leaves the browser on, or navigates to, the clear-cookie URL", async () => {
    const { signOut } = await load();
    stubFetch(() => new Response("", { status: 200 }));
    await signOut();
    const navigations = events.filter((e) => !e.startsWith("fetch:") && e !== "hook");
    expect(navigations.length).toBeGreaterThan(0);
    for (const e of navigations) expect(e).not.toContain("gcp-iap-mode");
    expect(location.href).not.toContain("gcp-iap-mode");
  });

  it("still replaces the page with / once when the clear-cookie fetch fails", async () => {
    const { signOut } = await load();
    const spy = stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    await signOut();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(location.replace).toHaveBeenCalledTimes(1);
    expect(location.replace).toHaveBeenCalledWith("/");
  });

  it("still replaces the page with / once when fetch throws synchronously", async () => {
    const { signOut } = await load();
    vi.stubGlobal("fetch", () => {
      throw new TypeError("offline");
    });
    await signOut();
    expect(location.replace).toHaveBeenCalledTimes(1);
    expect(location.replace).toHaveBeenCalledWith("/");
  });

  it("ignores the answer to the clear-cookie request, whatever its status", async () => {
    const { signOut } = await load();
    stubFetch(() => new Response("", { status: 500 }));
    await signOut();
    expect(location.replace).toHaveBeenCalledWith("/");
  });
});

describe("sign-out guard", () => {
  it("awaits the guard before running any hook", async () => {
    const { signOut, onSignOut, setSignOutGuard } = await load();
    setSignOutGuard(async () => {
      events.push("guard");
      return true;
    });
    onSignOut(async () => {
      events.push("hook");
    });
    stubFetch(() => new Response("", { status: 200 }));
    await signOut();
    expect(events).toEqual(["guard", "hook", `fetch:${CLEAR}`, "replace:/"]);
  });

  it("runs no hook, makes no request and navigates nowhere when the guard resolves false", async () => {
    const { signOut, onSignOut, setSignOutGuard } = await load();
    const hook = vi.fn(async () => {});
    onSignOut(hook);
    setSignOutGuard(async () => false);
    const spy = stubFetch(() => new Response("", { status: 200 }));
    await signOut();
    expect(hook).not.toHaveBeenCalled();
    expect(spy).not.toHaveBeenCalled();
    expect(location.replace).not.toHaveBeenCalled();
    expect(location.assign).not.toHaveBeenCalled();
    expect(location.href).toBe("/");
  });
});
