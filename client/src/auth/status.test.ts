import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

async function load() {
  vi.resetModules();
  const store = await import("./status");
  const status = (): unknown => {
    const v: unknown = store.sessionStatus;
    return typeof v === "function" ? (v as () => unknown)() : v;
  };
  return { ...store, status };
}

beforeEach(() => {
  vi.stubGlobal("navigator", { onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("session status store", () => {
  it("starts as ok", async () => {
    const { status } = await load();
    expect(status()).toBe("ok");
  });

  it("reportAuthRequired sets reconnect while online", async () => {
    const { reportAuthRequired, status } = await load();
    reportAuthRequired();
    expect(status()).toBe("reconnect");
  });

  it("reportAuthRequired changes nothing while offline", async () => {
    const { reportAuthRequired, status } = await load();
    vi.stubGlobal("navigator", { onLine: false });
    reportAuthRequired();
    expect(status()).toBe("ok");
  });

  it("an offline reportAuthRequired leaves an existing reconnect status as it was", async () => {
    const { reportAuthRequired, status } = await load();
    reportAuthRequired();
    vi.stubGlobal("navigator", { onLine: false });
    reportAuthRequired();
    expect(status()).toBe("reconnect");
  });

  it("reportNotAllowed sets not-allowed and reportOk returns to ok", async () => {
    const { reportNotAllowed, reportOk, status } = await load();
    reportNotAllowed();
    expect(status()).toBe("not-allowed");
    reportOk();
    expect(status()).toBe("ok");
  });

  it("calls onStatusChange listeners with the new status", async () => {
    const { reportAuthRequired, reportOk, onStatusChange } = await load();
    const fn = vi.fn();
    onStatusChange(fn);
    reportAuthRequired();
    reportOk();
    expect(fn.mock.calls.map((c) => c[0])).toEqual(["reconnect", "ok"]);
  });
});
