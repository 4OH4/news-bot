import { describe, expect, it } from "vitest";
import { relativeTime } from "./time";

const NOW = Date.parse("2026-10-18T12:00:00Z");

describe("relativeTime", () => {
  it("returns an empty string for null or unreadable dates", () => {
    expect(relativeTime(null, NOW)).toBe("");
    expect(relativeTime("not a date", NOW)).toBe("");
  });

  it("uses hours for a few hours ago", () => {
    expect(relativeTime("2026-10-18T09:00:00Z", NOW)).toMatch(/3 hours ago/);
  });

  it("uses days for older items", () => {
    expect(relativeTime("2026-10-16T12:00:00Z", NOW)).toMatch(/2 days ago/);
  });
});
