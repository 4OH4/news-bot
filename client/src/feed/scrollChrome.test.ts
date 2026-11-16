import { describe, expect, it } from "vitest";
import { INITIAL_CHROME, nextChrome } from "./scrollChrome";

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
