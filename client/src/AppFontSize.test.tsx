// @vitest-environment happy-dom
//
// #271: App.tsx applies the stored text size at startup, next to the stored theme.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, locationAt } from "../test/test-env";

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.fontSize;
  vi.resetModules();
  vi.stubGlobal("location", locationAt("/settings"));
  vi.stubGlobal("__APP_VERSION__", "test");
  installRouter({ online: false });
});

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.fontSize;
  vi.unstubAllGlobals();
});

describe("App startup", () => {
  it("applies a stored text size when the app module loads", async () => {
    localStorage.setItem("newsfeed-font-size", "xl");
    await import("./App");
    expect(document.documentElement.dataset.fontSize).toBe("xl");
  });

  it("leaves the root without data-font-size for a garbage stored value", async () => {
    localStorage.setItem("newsfeed-font-size", "huge");
    await import("./App");
    expect(document.documentElement.hasAttribute("data-font-size")).toBe(false);
  });
});
