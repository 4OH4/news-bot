import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fetchView } from "./feed";

// #328: the optional `ranking` request parameter.
const page = {
  schema_version: 1,
  generated_at: "2026-11-15T09:00:00Z",
  run_id: "run-invented-1",
  items: [],
};

function stubFetch() {
  const spy = vi.fn<(input: unknown, init?: RequestInit) => Promise<Response>>(
    async () =>
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

beforeEach(() => {
  vi.stubGlobal("location", { href: "/", replace: vi.fn() });
  vi.stubGlobal("navigator", { onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it.each(["rules", "personal"] as const)("sends ranking=%s", async (ranking) => {
  const spy = stubFetch();
  await fetchView("feed", { ranking });
  const url = new URL(String(spy.mock.calls[0]?.[0]), "http://localhost");
  expect(url.searchParams.get("ranking")).toBe(ranking);
});

it("sends no ranking by default", async () => {
  const spy = stubFetch();
  await fetchView("feed", {});
  const url = new URL(String(spy.mock.calls[0]?.[0]), "http://localhost");
  expect(url.searchParams.has("ranking")).toBe(false);
});
