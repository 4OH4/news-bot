// #272: the tab bar and header rules, checked as text because happy-dom computes no layout.
// Positions, sizes and computed colours in the production build are in e2e/navigation.spec.ts.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const style = read("../style.css");
const liked = read("../liked/liked.css");
const feedStatus = read("../feed/feed-status.css");
const html = read("../../index.html");

const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/** The merged declarations of every rule listing exactly `selector` (also inside @media). */
function decl(css: string, selector: string): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const m of strip(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const sels = m[1]!.split(",").map((x) => x.replace(/\s+/g, " ").trim());
    if (!sels.includes(selector)) continue;
    for (const d of m[2]!.split(";")) {
      const i = d.indexOf(":");
      if (i > 0) merged[d.slice(0, i).trim()] = d.slice(i + 1).trim();
    }
  }
  return merged;
}

describe("tab bar styles", () => {
  it("lives in style.css, not liked.css", () => {
    expect(strip(liked)).not.toMatch(/\.app-header/);
    expect(strip(style)).toMatch(/\.tab-bar/);
  });

  it("is fixed to the bottom on --bg with a --divider top border", () => {
    const bar = decl(style, ".tab-bar");
    expect(bar["position"]).toBe("fixed");
    expect(bar["bottom"]).toMatch(/^0/);
    expect(bar["left"]).toMatch(/^0/);
    expect(bar["right"]).toMatch(/^0/);
    expect(bar["background"] ?? bar["background-color"]).toBe("var(--bg)");
    expect(bar["border-top"]).toMatch(/1px/);
    expect(bar["border-top"]).toContain("var(--divider)");
  });

  it("clears the gesture bar with the bottom safe-area inset", () => {
    expect(decl(style, ".tab-bar")["padding-bottom"]).toContain("env(safe-area-inset-bottom");
  });

  it("makes each link at least 44px tall and uses tokens for colour and size", () => {
    const a = decl(style, ".tab-bar a");
    expect(a["min-height"]).toBe("44px");
    expect(a["color"]).toBe("var(--muted)");
    expect(a["font-size"]).toBe("var(--fs-meta)");
  });

  it("marks the current link with the accent colour", () => {
    expect(decl(style, '.tab-bar a[aria-current="page"]')["color"]).toBe("var(--accent)");
  });

  it("has no footer rule any more", () => {
    expect(strip(style)).not.toMatch(/\.footer/);
  });

  it("uses no literal colours in the new rules", () => {
    for (const sel of [".tab-bar", ".tab-bar a", '.tab-bar a[aria-current="page"]']) {
      const body = Object.values(decl(style, sel)).join(" ");
      expect(body, sel).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
    }
  });
});

describe("refresh icon styles", () => {
  it("has a spin keyframe and respects prefers-reduced-motion", () => {
    const css = strip(feedStatus) + strip(style);
    expect(css).toMatch(/@keyframes\s+[\w-]*spin/i);
    expect(css).toMatch(/prefers-reduced-motion:\s*reduce/);
  });
});

describe("index.html", () => {
  it("asks for viewport-fit=cover so the safe-area inset is reported", () => {
    expect(html).toMatch(/<meta[^>]*name="viewport"[^>]*viewport-fit=cover/);
  });
});
