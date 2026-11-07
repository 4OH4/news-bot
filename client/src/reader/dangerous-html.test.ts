import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = new URL("..", import.meta.url).pathname;

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(tsx?|jsx?)$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : [];
  });
}

describe("raw HTML insertion", () => {
  const uses = sourceFiles(SRC).filter((f) =>
    /dangerouslySetInnerHTML|\.innerHTML\s*=|insertAdjacentHTML/.test(readFileSync(f, "utf-8")),
  );

  it("happens in exactly one file, and it is in client/src/reader/", () => {
    expect(uses).toHaveLength(1);
    expect(uses[0]).toContain(`${join("src", "reader")}`);
  });

  it("uses dangerouslySetInnerHTML once, fed by cleanArticleHtml", () => {
    const text = readFileSync(uses[0]!, "utf-8");
    expect(text.match(/dangerouslySetInnerHTML/g)).toHaveLength(
      // the prop name appears once in the JSX (comments may not repeat it)
      1,
    );
    expect(text).toContain("cleanArticleHtml");
  });
});

describe("reader allowlist file", () => {
  it("has tags and attributes keys, and no script-capable tags", () => {
    const lists = JSON.parse(
      readFileSync(new URL("./allowed-tags.json", import.meta.url), "utf-8"),
    ) as { tags: string[]; attributes: string[] };
    expect(lists.tags).toContain("p");
    expect(lists.attributes.sort()).toEqual(["alt", "href", "src"]);
    for (const bad of ["script", "style", "iframe", "svg", "form", "input", "base"]) {
      expect(lists.tags).not.toContain(bad);
    }
  });
});
