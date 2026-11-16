// @vitest-environment happy-dom
//
// #332: the four-way Hide / Fewer / Show / More control of the preferences lab.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PreferenceControl, type PreferenceState } from "./PreferenceControl";

let root: HTMLElement;

beforeEach(() => {
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
});

function show(value: PreferenceState, onChange: (v: PreferenceState) => void = () => {}): void {
  render(h(PreferenceControl, { value, onChange, label: "Sport" }), root);
}

function radios(): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('[role="radio"]')];
}

function checked(): HTMLElement[] {
  return radios().filter((r) => r.getAttribute("aria-checked") === "true");
}

function nameOf(r: HTMLElement | undefined): string {
  return (r?.getAttribute("aria-label") ?? "").trim();
}

describe("PreferenceControl", () => {
  it("is a radiogroup named by its label, with four radios in order", () => {
    show("show");
    expect(root.querySelector('[role="radiogroup"]')?.getAttribute("aria-label")).toBe("Sport");
    expect(radios()).toHaveLength(4);
    for (const [i, word] of ["Hide", "Fewer", "Show", "More"].entries()) {
      expect(nameOf(radios()[i])).toMatch(new RegExp(word, "i"));
    }
  });

  it.each(["hide", "fewer", "show", "more"] as const)("selects only %s when it is the value", (value) => {
    show(value);
    expect(checked()).toHaveLength(1);
    expect(nameOf(checked()[0])).toMatch(new RegExp(value, "i"));
  });

  it("calls onChange with the state of the tapped radio", () => {
    const onChange = vi.fn();
    show("show", onChange);
    radios()[0]?.click();
    expect(onChange).toHaveBeenCalledWith("hide");
  });

  it.each([
    ["show", "ArrowRight", "more"],
    ["show", "ArrowLeft", "fewer"],
    ["fewer", "ArrowRight", "show"],
  ] as const)("from %s, %s selects %s", (value, key, expected) => {
    const onChange = vi.fn();
    show(value, onChange);
    checked()[0]?.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
    expect(onChange).toHaveBeenCalledWith(expected);
  });

  it("keeps an accessible name on every radio", () => {
    show("hide");
    for (const r of radios()) expect(nameOf(r)).not.toBe("");
  });
});
