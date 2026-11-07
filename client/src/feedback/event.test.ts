import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildEvent, newEventId, type FeedbackEventName } from "./event";

/** The shared fixture #176's pytest test also reads, so the two sides can't drift (#215). */
const examples = JSON.parse(
  readFileSync(new URL("../../../docs/feedback.example.json", import.meta.url), "utf8"),
) as Record<string, unknown>[];

const HEX32 = /^[0-9a-f]{32}$/;
const ISO_MS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("buildEvent against docs/feedback.example.json", () => {
  it("reads a non-empty example file", () => {
    expect(Array.isArray(examples)).toBe(true);
    expect(examples.length).toBeGreaterThan(0);
  });

  it.each(examples.map((ex) => [String(ex.event), ex] as const))(
    "makes a %s event with exactly the example's keys and formats",
    (_name, ex) => {
      const value = ex.value === null ? undefined : (ex.value as number);
      const built = buildEvent(
        String(ex.article_id),
        ex.event as FeedbackEventName,
        value,
        new Date(String(ex.created_at)),
      );
      expect(Object.keys(built).sort()).toEqual(Object.keys(ex).sort());
      expect(String(ex.id)).toMatch(HEX32);
      expect(built.id).toMatch(HEX32);
      expect(built.article_id).toBe(ex.article_id);
      expect(built.event).toBe(ex.event);
      expect(built.value).toBe(ex.value);
      expect(String(ex.created_at)).toMatch(ISO_MS);
      expect(built.created_at).toMatch(ISO_MS);
      expect(built.created_at).toBe(ex.created_at);
    },
  );
});

describe("buildEvent", () => {
  it("sets value to null when none is given", () => {
    const built = buildEvent("1a2b3c4d5e6f7081", "open");
    expect(built.value).toBeNull();
  });

  it("keeps a value of 0 rather than turning it into null", () => {
    const built = buildEvent("1a2b3c4d5e6f7081", "read", 0);
    expect(built.value).toBe(0);
  });

  it("takes created_at from the current time when no time is given", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-07T12:34:56.789Z"));
    const built = buildEvent("1a2b3c4d5e6f7081", "dismiss");
    expect(built.created_at).toBe("2026-11-07T12:34:56.789Z");
  });

  it("gives each event a new id", () => {
    const a = buildEvent("1a2b3c4d5e6f7081", "like", 1);
    const b = buildEvent("1a2b3c4d5e6f7081", "like", 1);
    expect(a.id).not.toBe(b.id);
  });
});

describe("newEventId", () => {
  it("is 32 lower-case hex characters", () => {
    for (let i = 0; i < 20; i++) expect(newEventId()).toMatch(HEX32);
  });

  it("draws its randomness from crypto.getRandomValues", () => {
    const spy = vi.spyOn(globalThis.crypto, "getRandomValues");
    newEventId();
    expect(spy).toHaveBeenCalled();
  });

  it("does not repeat across many calls", () => {
    const ids = new Set(Array.from({ length: 200 }, () => newEventId()));
    expect(ids.size).toBe(200);
  });
});
