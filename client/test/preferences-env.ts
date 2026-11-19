/**
 * Shared set-up for the preferences tests (#338): an invented menu and a fake
 * `/api/preferences` layered over the router from `test-env.ts`. Every name is invented.
 */
import { vi } from "vitest";
import type { PreferencesResponse, PreferenceState } from "../src/preferences/types";
import { installRouter, type FakeRouter } from "./test-env";

type Src = { id: string; label: string; state: PreferenceState; starter: boolean };
const src = (id: string, label: string, starter: boolean, state: PreferenceState = "show"): Src => ({
  id,
  label,
  state,
  starter,
});

/** Sections as the API orders them; News sources has 3 starters and 4 others. */
export function makeMenu(
  over: { version?: number; setup_done?: boolean; states?: Record<string, PreferenceState> } = {},
): PreferencesResponse {
  const st = over.states ?? {};
  const s = (id: string, label: string, starter: boolean): Src =>
    src(id, label, starter, st[id] ?? "show");
  return {
    version: over.version ?? 1,
    setup_done: over.setup_done ?? true,
    menu: {
      sections: [
        {
          id: "news",
          label: "News sources",
          sources: [
            s("bbc-news", "BBC News", true),
            s("invented-wire", "Invented Wire", true),
            s("invented-post", "Invented Post", true),
            s("n-four", "Northern Four", false),
            s("n-five", "Northern Five", false),
            s("n-six", "Northern Six", false),
            s("n-seven", "Northern Seven", false),
          ],
        },
        {
          id: "tech",
          label: "Tech",
          sources: [s("wired", "Wired", true), s("invented-bytes", "Invented Bytes", true)],
        },
        { id: "science", label: "Science", sources: [s("sci-one", "Science One", true)] },
        {
          id: "more",
          label: "More sources",
          sources: [s("m-one", "More One", false), s("m-two", "More Two", false)],
        },
        { id: "long-reads", label: "Long Reads", sources: [s("lr-one", "Long Read One", true)] },
      ],
      topics: [
        { id: "sport", label: "Sport", state: st["sport"] ?? "show", starter: true },
        { id: "politics", label: "Politics", state: "show", starter: true },
        { id: "gardening", label: "Gardening", state: "show", starter: false },
      ],
      multipliers: { hide: 0, fewer: 0.5, show: 1, more: 2 },
    },
  };
}

export interface FakePrefs {
  router: FakeRouter;
  /** Answers to successive PUTs; the last one repeats. Default: echo the body at version + 1. */
  putAnswers: { status: number; body?: PreferencesResponse }[];
  /** Bodies of the PUTs that arrived, with the headers sent. */
  puts: { body: Record<string, unknown>; headers: Headers }[];
  /** `If-None-Match` values sent with `GET /api/feed`. */
  feedConditions: (string | null)[];
  /** What GET /api/preferences returns; null answers 500. */
  get: PreferencesResponse | null;
  /** When set, each PUT is recorded in `puts` at once but answered only after this resolves. */
  putGate: Promise<void> | null;
}

/** Installs the router plus `/api/preferences`, with the menu `get`. */
export function installPrefs(
  get: PreferencesResponse | null = makeMenu(),
  initial: Partial<FakeRouter> = {},
): FakePrefs {
  const router = installRouter(initial);
  const base = globalThis.fetch;
  const fake: FakePrefs = { router, putAnswers: [], puts: [], feedConditions: [], get, putGate: null };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init: RequestInit = {}): Promise<Response> => {
      const url = typeof input === "string" ? input : (input as { url: string }).url;
      const path = new URL(url, "http://localhost").pathname;
      if (path === "/api/feed") {
        fake.feedConditions.push(new Headers(init.headers).get("If-None-Match"));
      }
      if (path !== "/api/preferences") return base(input as RequestInfo, init);
      if (!router.online) throw new TypeError("Failed to fetch");
      const json = (b: unknown, status = 200) =>
        new Response(JSON.stringify(b), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      if ((init.method ?? "GET").toUpperCase() === "GET") {
        return fake.get ? json(fake.get) : new Response(null, { status: 500 });
      }
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      fake.puts.push({ body, headers: new Headers(init.headers) });
      if (fake.putGate) await fake.putGate;
      const answer = fake.putAnswers[Math.min(fake.puts.length - 1, fake.putAnswers.length - 1)];
      if (!answer) return json({ ...(fake.get ?? makeMenu()), version: Number(body.version) + 1 });
      return answer.body ? json(answer.body, answer.status) : new Response(null, { status: answer.status });
    }),
  );
  return fake;
}
