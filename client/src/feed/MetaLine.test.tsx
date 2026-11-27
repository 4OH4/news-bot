// @vitest-environment happy-dom
//
// #412: MetaLine renders a details line as separate items with dot separators, so CSS can keep
// each item whole on a line. This checks structure only; layout is measured in e2e/meta-wrap.
import { h, render } from "preact";
import { afterEach, describe, expect, it } from "vitest";
import { MetaLine } from "./MetaLine";

let root: HTMLElement;

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
});

describe("MetaLine", () => {
  it("renders one span.meta-item per item in order, marks only the wrap item, and reads 'A · B · C'", () => {
    root = document.createElement("div");
    document.body.appendChild(root);
    render(
      h(MetaLine, {
        className: "test-meta",
        items: [
          { key: "a", content: "A" },
          { key: "b", content: "B", wrap: true },
          { key: "c", content: "C" },
        ],
      }),
      root,
    );
    const line = root.querySelector("p")!;
    expect(line.className).toBe("test-meta");
    const items = [...line.querySelectorAll("span.meta-item")];
    expect(items).toHaveLength(3);
    expect(items.map((i) => i.classList.contains("meta-wrap"))).toEqual([false, true, false]);
    // The separator may use a non-breaking space.
    expect(line.textContent!.replaceAll(String.fromCharCode(160), " ").trim()).toBe("A · B · C");
  });
});
