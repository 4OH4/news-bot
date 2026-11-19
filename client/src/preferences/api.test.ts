// @vitest-environment happy-dom
//
// #338: the preferences API client.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installPrefs, makeMenu } from "../../test/preferences-env";
import {
  getPreferences,
  PreferencesConflict,
  PreferencesOffline,
  putPreferences,
  setSourceState,
} from "./api";

const BODY = {
  version: 3,
  sources: { "bbc-news": "hide" as const },
  topics: { sport: "more" as const },
  setup_done: true,
};

beforeEach(() => {
  vi.stubGlobal("location", { href: "/", replace: vi.fn() });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("preferences api", () => {
  it("getPreferences returns the menu from GET /api/preferences", async () => {
    installPrefs(makeMenu({ version: 7 }));
    const got = await getPreferences();
    expect(got.version).toBe(7);
    expect(got.menu.sections.map((s) => s.label)).toContain("Long Reads");
  });

  it("putPreferences sends JSON with X-Requested-With and returns the saved state", async () => {
    const fake = installPrefs();
    const saved = await putPreferences(BODY);
    expect(saved.version).toBe(4);
    expect(fake.puts[0]?.body).toEqual(BODY);
    expect(fake.puts[0]?.headers.get("X-Requested-With")).toBe("XMLHttpRequest");
    expect(fake.puts[0]?.headers.get("Content-Type")).toMatch(/application\/json/);
  });

  it("every PUT sets keepalive so a save in flight survives the page closing (#395)", async () => {
    installPrefs();
    await putPreferences(BODY);
    await putPreferences(BODY, { keepalive: false });
    const puts = vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(puts).toHaveLength(2);
    for (const [, init] of puts) expect(init?.keepalive).toBe(true);
  });

  it("GET /api/preferences does not set keepalive (#395)", async () => {
    installPrefs();
    await getPreferences();
    const [, init] = vi.mocked(fetch).mock.calls[0] ?? [];
    expect(init?.method).toBe("GET");
    expect(init?.keepalive).toBeUndefined();
  });

  it("a 409 rejects with PreferencesConflict carrying the server's current state", async () => {
    const fake = installPrefs();
    const current = makeMenu({ version: 9, states: { wired: "fewer" } });
    fake.putAnswers = [{ status: 409, body: current }];
    const err = await putPreferences(BODY).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(PreferencesConflict);
    expect((err as PreferencesConflict).current).toEqual(current);
  });

  it("a network failure rejects with PreferencesOffline", async () => {
    installPrefs(makeMenu(), { online: false });
    await expect(putPreferences(BODY)).rejects.toBeInstanceOf(PreferencesOffline);
  });
});

// #339: one source's state changes; everything else is sent back as the server has it.
describe("setSourceState", () => {
  it("reads the current preferences and PUTs them with the one source set to hide", async () => {
    const fake = installPrefs(makeMenu({ version: 5, states: { wired: "fewer", sport: "more" } }));
    await setSourceState("bbc-news", "hide");
    expect(fake.puts).toHaveLength(1);
    expect(fake.puts[0]?.body).toEqual({
      version: 5,
      sources: { "bbc-news": "hide", wired: "fewer" },
      topics: { sport: "more" },
      setup_done: true,
    });
  });

  it("show removes the source from the PUT, since only non-Show states are sent", async () => {
    const fake = installPrefs(makeMenu({ states: { "bbc-news": "hide", wired: "fewer" } }));
    await setSourceState("bbc-news", "show");
    expect(fake.puts[0]?.body.sources).toEqual({ wired: "fewer" });
  });

  it("a 409 reloads the preferences and retries once, with the change on the newer state", async () => {
    const newer = makeMenu({ version: 9, states: { wired: "fewer" } });
    // The fake's GET is static, so it serves the newer state throughout; the first PUT is
    // refused anyway, as if another device had saved in between.
    const fake = installPrefs(newer);
    fake.putAnswers = [
      { status: 409, body: newer },
      { status: 200, body: makeMenu({ version: 10 }) },
    ];
    await setSourceState("bbc-news", "hide");
    expect(fake.puts).toHaveLength(2);
    expect(fake.puts[1]?.body).toMatchObject({
      version: 9,
      sources: { "bbc-news": "hide", wired: "fewer" },
    });
  });
});
