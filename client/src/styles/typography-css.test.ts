// #271: the stylesheets are checked as text, because happy-dom does not compute styles from
// imported CSS. Computed values in the production build are checked in e2e/typography.spec.ts.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");
const tokens = read("./tokens.css");
const style = read("../style.css");
const cardActions = read("../feed/card-actions.css");
const reader = read("./reader.css");

const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

type Rule = { selectors: string[]; body: string };

/** Flat rules, including those inside an @media block (nesting is one level deep here). */
function rules(css: string): Rule[] {
  const out: Rule[] = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  for (const m of strip(css).matchAll(re)) {
    const head = m[1]!.trim();
    if (head.startsWith("@")) continue;
    out.push({
      selectors: head.split(",").map((s) => s.replace(/\s+/g, " ").trim()),
      body: m[2]!,
    });
  }
  return out;
}

function declarations(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const d of body.split(";")) {
    const i = d.indexOf(":");
    if (i > 0) out[d.slice(0, i).trim()] = d.slice(i + 1).trim();
  }
  return out;
}

/** The merged declarations of every rule that lists exactly `selector`; later rules win. */
function decl(css: string, selector: string): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const r of rules(css)) {
    if (r.selectors.includes(selector)) Object.assign(merged, declarations(r.body));
  }
  return merged;
}

/** The custom properties declared by the rule with exactly this selector. */
function tokenBlock(selector: string): Record<string, string> {
  return decl(tokens, selector);
}

const LIGHT = tokenBlock(":root");
const DARK_MEDIA = tokenBlock(':root:not([data-theme="light"])');
const DARK_ATTR = tokenBlock(':root[data-theme="dark"]');

describe("type tokens", () => {
  it("defines the two font stacks", () => {
    expect(LIGHT["--font-headline"]).toBe(
      'ui-serif, "Iowan Old Style", Charter, Georgia, "Noto Serif", serif',
    );
    expect(LIGHT["--font-body"]).toBe("system-ui, sans-serif");
  });

  it.each([
    ["--fs-meta", 0.8125],
    ["--fs-body", 0.9375],
    ["--fs-headline", 1.1875],
    ["--fs-reader-body", 1.0625],
    ["--fs-reader-title", 1.75],
  ])("%s is a rem value near %d", (name, expected) => {
    const m = /^([\d.]+)rem$/.exec(LIGHT[name] ?? "");
    expect(m, `${name} must be in rem`).not.toBeNull();
    expect(Math.abs(Number(m![1]) - expected)).toBeLessThanOrEqual(0.07);
  });

  it("keeps the reader sizes at their current values", () => {
    expect(LIGHT["--fs-reader-body"]).toBe("1.0625rem");
    expect(LIGHT["--fs-reader-title"]).toBe("1.75rem");
  });

  it("defines the two line heights", () => {
    expect(LIGHT["--lh-tight"]).toBe("1.25");
    expect(LIGHT["--lh-body"]).toBe("1.5");
  });

  it("sets the root font size per text-size step, with percentages and none for regular", () => {
    expect(decl(tokens, ':root[data-font-size="small"]')["font-size"]).toBe("87.5%");
    expect(decl(tokens, ':root[data-font-size="large"]')["font-size"]).toBe("112.5%");
    expect(decl(tokens, ':root[data-font-size="xl"]')["font-size"]).toBe("125%");
    expect(tokens).not.toMatch(/data-font-size="regular"/);
  });
});

describe("colour tokens", () => {
  const NAMES = ["--headline", "--text-secondary", "--accent", "--divider"];

  it.each(NAMES)("%s has a hex value in light and in both dark blocks", (name) => {
    for (const block of [LIGHT, DARK_MEDIA, DARK_ATTR]) {
      expect(block[name]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it.each(NAMES)("%s differs between light and dark", (name) => {
    expect(DARK_ATTR[name]).not.toBe(LIGHT[name]);
  });

  it.each(NAMES)("%s is the same in the two dark blocks", (name) => {
    expect(DARK_MEDIA[name]).toBe(DARK_ATTR[name]);
  });

  function luminance(hex: string): number {
    const [r, g, b] = [1, 3, 5].map((i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    }) as [number, number, number];
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  function contrast(a: string, b: string): number {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
    return (hi + 0.05) / (lo + 0.05);
  }

  it.each([
    ["light", LIGHT],
    ["dark", DARK_ATTR],
  ] as const)("headline, secondary text and accent reach 4.5:1 on --bg in %s", (_n, block) => {
    for (const name of ["--headline", "--text-secondary", "--accent"]) {
      const value = block[name];
      expect(value, name).toBeDefined();
      expect(contrast(value!, block["--bg"] ?? LIGHT["--bg"]!), name).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("feed card rules", () => {
  it(".card has no box: no border, radius or surface, and a hairline divider below", () => {
    const d = decl(style, ".card");
    expect(d["border"]).toBeUndefined();
    expect(d["border-top"]).toBeUndefined();
    expect(d["border-left"]).toBeUndefined();
    expect(d["border-right"]).toBeUndefined();
    expect(d["border-radius"]).toBeUndefined();
    expect(d["background"]).toBeUndefined();
    expect(d["border-bottom"]).toBe("1px solid var(--divider)");
  });

  it("the last card has no divider", () => {
    const d = { ...decl(style, ".card:last-child"), ...decl(style, ".card:last-of-type") };
    expect(d["border-bottom"]).toMatch(/^(none|0)\b/);
  });

  it(".card-title is a bold serif headline", () => {
    const d = decl(style, ".card-title");
    expect(d["font-family"]).toBe("var(--font-headline)");
    expect(d["font-weight"]).toBe("700");
    expect(d["font-size"]).toBe("var(--fs-headline)");
    expect(d["line-height"]).toBe("var(--lh-tight)");
  });

  it("the headline link is dark with no underline, underlined on hover, outlined on focus", () => {
    const link = { ...decl(style, ".card-title a"), ...decl(cardActions, ".card-title a") };
    expect(link["color"]).toBe("var(--headline)");
    expect(link["text-decoration"]).toBe("none");
    const hover = { ...decl(style, ".card-title a:hover"), ...decl(cardActions, ".card-title a:hover") };
    expect(hover["text-decoration"]).toBe("underline");
    const focus = {
      ...decl(style, ".card-title a:focus-visible"),
      ...decl(cardActions, ".card-title a:focus-visible"),
    };
    expect(focus["outline"]).toMatch(/2px/);
    expect(focus["outline"]).toContain("var(--accent)");
  });

  it("a seen headline stays muted, so it differs from the unseen --headline", () => {
    expect(decl(cardActions, ".card-seen .card-title a")["color"]).toBe("var(--muted)");
  });

  it(".card-meta is small, muted, uppercase and spaced, and its link inherits the colour", () => {
    const d = decl(style, ".card-meta");
    expect(d["font-size"]).toBe("var(--fs-meta)");
    expect(d["color"]).toBe("var(--muted)");
    expect(d["text-transform"]).toBe("uppercase");
    expect(d["letter-spacing"]).toBeDefined();
    const a = decl(style, ".card-meta a");
    expect(a["color"]).toBe("inherit");
    expect(a["text-decoration"]).toBe("none");
    expect(decl(style, ".card-meta a:hover")["text-decoration"]).toBe("underline");
  });

  it("the source domain is not the accent colour", () => {
    for (const css of [style, cardActions]) {
      for (const r of rules(css)) {
        if (r.selectors.some((s) => s.includes(".card-meta"))) {
          expect(r.body).not.toContain("--accent");
        }
      }
    }
  });

  it(".card-summary is quiet body text limited to three lines", () => {
    const d = decl(style, ".card-summary");
    expect(d["font-family"]).toBe("var(--font-body)");
    expect(d["font-size"]).toBe("var(--fs-body)");
    expect(d["line-height"]).toBe("var(--lh-body)");
    expect(d["color"]).toBe("var(--text-secondary)");
    expect(d["display"]).toBe("-webkit-box");
    expect(d["-webkit-line-clamp"]).toBe("3");
    expect(d["-webkit-box-orient"]).toBe("vertical");
    expect(d["overflow"]).toBe("hidden");
  });

  it("no other rule resets display on .card-summary", () => {
    for (const css of [style, cardActions]) {
      for (const r of rules(css)) {
        if (r.selectors.includes(".card-summary")) {
          const display = declarations(r.body)["display"];
          if (display !== undefined) expect(display).toBe("-webkit-box");
        }
      }
    }
  });

  it(".card-flags uses the meta size", () => {
    expect(decl(style, ".card-flags")["font-size"]).toBe("var(--fs-meta)");
  });

  it("no card rule hard-codes a colour", () => {
    for (const css of [style, cardActions]) {
      for (const r of rules(css)) {
        if (r.selectors.some((s) => s.includes(".card"))) expect(r.body).not.toMatch(/#[0-9a-f]{3,8}\b/i);
      }
    }
  });
});

describe("reader rules", () => {
  it(".reader-title is the serif headline in the headline colour", () => {
    const d = decl(reader, ".reader-title");
    expect(d["font-family"]).toBe("var(--font-headline)");
    expect(d["font-size"]).toBe("var(--fs-reader-title)");
    expect(d["color"]).toBe("var(--headline)");
  });

  it("the reader body stays sans-serif and uses the reader body size", () => {
    const d = decl(reader, ".reader");
    expect(d["font-size"]).toBe("var(--fs-reader-body)");
    expect(d["font-family"] ?? "var(--font-body)").not.toContain("--font-headline");
  });

  it("meta and figcaption use the meta size", () => {
    expect(decl(reader, ".reader-meta")["font-size"]).toBe("var(--fs-meta)");
    expect(decl(reader, ".reader-body figcaption")["font-size"]).toBe("var(--fs-meta)");
  });
});

describe("no literal font sizes", () => {
  it.each([
    ["style.css", style],
    ["card-actions.css", cardActions],
    ["reader.css", reader],
  ])("%s uses a --fs- token for every font-size", (_name, css) => {
    for (const r of rules(css)) {
      const size = declarations(r.body)["font-size"];
      if (size !== undefined) expect(size, r.selectors.join(", ")).toMatch(/^var\(--fs-[a-z-]+\)$/);
    }
  });

  it("the page title and footer use tokens", () => {
    expect(decl(style, ".top h1")["font-size"]).toMatch(/^var\(--fs-/);
    expect(decl(style, ".footer")["font-size"]).toMatch(/^var\(--fs-/);
  });

  it("keeps tap targets in px", () => {
    expect(decl(style, "button")["min-height"]).toBe("44px");
    expect(decl(cardActions, ".card-menu summary")["min-height"]).toBe("44px");
  });
});
