import { describe, expect, it } from "vitest";
import { INITIAL_CHROME, nextChrome, snapChrome } from "./scrollChrome";

const VP = 800;
const H = 60;

describe("nextChrome", () => {
  it("moves the header by the scroll delta, clamped to its height", () => {
    const a = nextChrome(INITIAL_CHROME, 30, VP, H);
    expect(a.offset).toBe(30);
    expect(nextChrome(a, 500, VP, H).offset).toBe(H);
  });

  it("brings the header back at the same rate, and fully at the top", () => {
    let s = nextChrome(INITIAL_CHROME, 500, VP, H);
    s = nextChrome(s, 470, VP, H);
    expect(s.offset).toBe(H - 30);
    expect(nextChrome(s, 0, VP, H).offset).toBe(0);
  });

  it("resets to fully shown at the top, even after overscroll (-50 then 0)", () => {
    const down = nextChrome(INITIAL_CHROME, 300, VP, H);
    expect(down.offset).toBe(H);
    const over = nextChrome(down, -50, VP, H);
    expect(over.offset).toBe(0);
    expect(nextChrome(over, 0, VP, H).offset).toBe(0);
  });

  it("does not count the gap above the sticky header toward its offset", () => {
    expect(nextChrome(INITIAL_CHROME, 30, VP, H, 16).offset).toBe(14);
    expect(nextChrome(INITIAL_CHROME, 10, VP, H, 16).offset).toBe(0);
  });

  it("shows the button only past two screens and scrolling up", () => {
    const down = nextChrome(INITIAL_CHROME, 3 * VP, VP, H);
    expect(down.showButton).toBe(false);
    const up = nextChrome(down, 3 * VP - 10, VP, H);
    expect(up.showButton).toBe(true);
    expect(nextChrome(up, 3 * VP + 10, VP, H).showButton).toBe(false);
    expect(nextChrome(up, VP - 10, VP, H).showButton).toBe(false);
  });

  it("does not show within two screens", () => {
    const s = nextChrome(nextChrome(INITIAL_CHROME, 1.9 * VP, VP, H), 1.8 * VP, VP, H);
    expect(s.showButton).toBe(false);
  });
});

describe("snapChrome", () => {
  it("finishes a part-shown header hidden after a downward move", () => {
    let s = nextChrome(INITIAL_CHROME, 200, VP, H);
    s = nextChrome(s, 170, VP, H);
    s = nextChrome(s, 180, VP, H);
    expect(s.dir).toBe(1);
    expect(s.offset).toBeLessThan(H);
    expect(snapChrome(s, H).offset).toBe(H);
  });

  it("near the top, hides the header only as far as the page has scrolled", () => {
    const s = nextChrome(INITIAL_CHROME, 40, VP, H, 16);
    expect(s.offset).toBe(24);
    expect(snapChrome(s, H, 16).offset).toBe(24);
    const t = nextChrome(INITIAL_CHROME, 30, VP, H);
    expect(snapChrome(t, H).offset).toBe(30);
  });

  it("finishes a part-shown header shown after an upward move", () => {
    let s = nextChrome(INITIAL_CHROME, 500, VP, H);
    s = nextChrome(s, 470, VP, H);
    expect(s.dir).toBe(-1);
    expect(snapChrome(s, H).offset).toBe(0);
  });

  it("leaves a fully shown or fully hidden header alone", () => {
    const hidden = nextChrome(INITIAL_CHROME, 500, VP, H);
    expect(snapChrome(hidden, H)).toBe(hidden);
    const shown = nextChrome(hidden, 300, VP, H);
    expect(snapChrome({ ...shown, offset: 0 }, H).offset).toBe(0);
  });

  it("is always fully shown at the top, whatever the direction", () => {
    const part = { ...INITIAL_CHROME, y: 10, offset: 20, dir: 1 as const };
    expect(snapChrome(part, H, 16).offset).toBe(0);
    expect(snapChrome({ ...part, y: 0 }, H).offset).toBe(0);
  });
});
