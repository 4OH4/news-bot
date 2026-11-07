import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Loads fresh copies of the client and the status store, so state never leaks between tests.
 * `status()` reads `sessionStatus` whether the module exports it as a live binding or a getter.
 */
async function load() {
  vi.resetModules();
  const client = await import("./client");
  const store = await import("../auth/status");
  const status = (): unknown => {
    const v: unknown = store.sessionStatus;
    return typeof v === "function" ? (v as () => unknown)() : v;
  };
  return { ...client, status };
}

function stubFetch(impl: (...args: unknown[]) => unknown) {
  const spy = vi.fn(impl);
  vi.stubGlobal("fetch", spy);
  return spy;
}

function sentHeaders(spy: ReturnType<typeof vi.fn>): Headers {
  const init = spy.mock.calls[0]?.[1] as RequestInit | undefined;
  return new Headers(init?.headers);
}

const location = { href: "/", replace: vi.fn() };

beforeEach(() => {
  location.href = "/";
  location.replace.mockReset();
  vi.stubGlobal("location", location);
  vi.stubGlobal("navigator", { onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("apiFetch requests", () => {
  it("always sends X-Requested-With: XMLHttpRequest", async () => {
    const { apiFetch } = await load();
    const spy = stubFetch(() => new Response("{}", { status: 200 }));
    await apiFetch("/api/feed");
    expect(sentHeaders(spy).get("X-Requested-With")).toBe("XMLHttpRequest");
  });

  it("keeps the caller's headers and still adds X-Requested-With", async () => {
    const { apiFetch } = await load();
    const spy = stubFetch(() => new Response("{}", { status: 200 }));
    await apiFetch("/api/feedback", { method: "POST", headers: { "X-Test": "1" } });
    const headers = sentHeaders(spy);
    expect(headers.get("X-Test")).toBe("1");
    expect(headers.get("X-Requested-With")).toBe("XMLHttpRequest");
  });

  it("uses same-origin credentials and manual redirects", async () => {
    const { apiFetch } = await load();
    const spy = stubFetch(() => new Response("{}", { status: 200 }));
    await apiFetch("/api/feed");
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("same-origin");
    expect(init.redirect).toBe("manual");
  });

  it("returns the response for a 2xx and for a 304", async () => {
    const { apiFetch } = await load();
    stubFetch(() => new Response("{}", { status: 200 }));
    expect((await apiFetch("/api/feed")).status).toBe(200);
    stubFetch(() => new Response(null, { status: 304 }));
    expect((await apiFetch("/api/feed")).status).toBe(304);
  });
});

describe("apiFetch errors", () => {
  it("raises AuthRequiredError on 401 and the status becomes reconnect", async () => {
    const { apiFetch, AuthRequiredError, status } = await load();
    stubFetch(() => new Response("", { status: 401 }));
    await expect(apiFetch("/api/feed")).rejects.toBeInstanceOf(AuthRequiredError);
    expect(status()).toBe("reconnect");
  });

  it("raises OfflineError when fetch throws a TypeError, with no banner and no navigation", async () => {
    const { apiFetch, OfflineError, status } = await load();
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    await expect(apiFetch("/api/feed")).rejects.toBeInstanceOf(OfflineError);
    expect(status()).toBe("ok");
    expect(location.replace).not.toHaveBeenCalled();
    expect(location.href).toBe("/");
  });

  it("handles an opaque redirect as AuthRequiredError", async () => {
    const { apiFetch, AuthRequiredError, status } = await load();
    stubFetch(() => ({ type: "opaqueredirect", status: 0, ok: false }));
    await expect(apiFetch("/api/feed")).rejects.toBeInstanceOf(AuthRequiredError);
    expect(status()).toBe("reconnect");
  });

  it("raises NotAllowedError on 403 and the status becomes not-allowed", async () => {
    const { apiFetch, NotAllowedError, status } = await load();
    stubFetch(() => new Response("", { status: 403 }));
    await expect(apiFetch("/api/feed")).rejects.toBeInstanceOf(NotAllowedError);
    expect(status()).toBe("not-allowed");
  });

  it("does not show the banner for a 401 while the browser reports offline", async () => {
    const { apiFetch, AuthRequiredError, status } = await load();
    vi.stubGlobal("navigator", { onLine: false });
    stubFetch(() => new Response("", { status: 401 }));
    await expect(apiFetch("/api/feed")).rejects.toBeInstanceOf(AuthRequiredError);
    expect(status()).toBe("ok");
  });

  it("does not navigate on a 401 or a 403, and runs no sign-out hook", async () => {
    const { apiFetch } = await load();
    const { onSignOut } = await import("../auth/signout");
    const hook = vi.fn(async () => {});
    onSignOut(hook);
    for (const code of [401, 403]) {
      stubFetch(() => new Response("", { status: code }));
      await apiFetch("/api/feed").catch(() => {});
    }
    expect(hook).not.toHaveBeenCalled();
    expect(location.replace).not.toHaveBeenCalled();
    expect(location.href).toBe("/");
  });

  it("clears the banner when a later call succeeds", async () => {
    const { apiFetch, status } = await load();
    stubFetch(() => new Response("", { status: 401 }));
    await apiFetch("/api/feed").catch(() => {});
    expect(status()).toBe("reconnect");
    stubFetch(() => new Response("{}", { status: 200 }));
    await apiFetch("/api/feed");
    expect(status()).toBe("ok");
  });

  it("clears the banner on a 304 as well", async () => {
    const { apiFetch, status } = await load();
    stubFetch(() => new Response("", { status: 401 }));
    await apiFetch("/api/feed").catch(() => {});
    stubFetch(() => new Response(null, { status: 304 }));
    await apiFetch("/api/feed");
    expect(status()).toBe("ok");
  });

  it("never logs a cookie or header value", async () => {
    const { apiFetch } = await load();
    const logs = (["log", "warn", "error", "info", "debug"] as const).map((m) =>
      vi.spyOn(console, m).mockImplementation(() => {}),
    );
    stubFetch(() => new Response("", { status: 401 }));
    await apiFetch("/api/feed", { headers: { Cookie: "secret-cookie-value" } }).catch(() => {});
    for (const spy of logs) {
      expect(JSON.stringify(spy.mock.calls)).not.toContain("secret-cookie-value");
    }
  });
});
