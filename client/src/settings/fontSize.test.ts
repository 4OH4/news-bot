// @vitest-environment happy-dom
//
// #271: the Text size setting, stored like the theme and applied as data-font-size on the root.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyStoredFontSize, fontSize, setFontSize } from "./fontSize";

const KEY = "newsfeed-font-size";
const root = () => document.documentElement;

beforeEach(() => {
  localStorage.clear();
  delete root().dataset.fontSize;
});

afterEach(() => {
  localStorage.clear();
  delete root().dataset.fontSize;
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("fontSize", () => {
  it("is regular when nothing is stored", () => {
    expect(fontSize()).toBe("regular");
  });

  it.each(["small", "large", "xl"] as const)("returns the stored %s", (size) => {
    localStorage.setItem(KEY, size);
    expect(fontSize()).toBe(size);
  });

  it.each(["huge", "", "LARGE", "12"])("treats the garbage value %j as regular", (value) => {
    localStorage.setItem(KEY, value);
    expect(fontSize()).toBe("regular");
  });
});

describe("setFontSize", () => {
  it.each(["small", "large", "xl"] as const)(
    "%s sets data-font-size and stores the choice",
    (size) => {
      setFontSize(size);
      expect(root().dataset.fontSize).toBe(size);
      expect(localStorage.getItem(KEY)).toBe(size);
      expect(fontSize()).toBe(size);
    },
  );

  it("regular removes the attribute and clears the stored key", () => {
    setFontSize("xl");
    setFontSize("regular");
    expect(root().hasAttribute("data-font-size")).toBe(false);
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(fontSize()).toBe("regular");
  });

  it("still sets the attribute, and does not throw, when storage throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });

    expect(() => setFontSize("large")).not.toThrow();
    expect(root().dataset.fontSize).toBe("large");
    expect(() => setFontSize("regular")).not.toThrow();
    expect(root().hasAttribute("data-font-size")).toBe(false);
    expect(fontSize()).toBe("regular");
  });
});

describe("applyStoredFontSize", () => {
  it("applies nothing when nothing is stored", () => {
    applyStoredFontSize();
    expect(root().hasAttribute("data-font-size")).toBe(false);
  });

  it("applies a stored choice at startup", () => {
    localStorage.setItem(KEY, "large");
    applyStoredFontSize();
    expect(root().dataset.fontSize).toBe("large");
  });

  it("applies nothing for a garbage stored value", () => {
    localStorage.setItem(KEY, "huge");
    applyStoredFontSize();
    expect(root().hasAttribute("data-font-size")).toBe(false);
  });
});
