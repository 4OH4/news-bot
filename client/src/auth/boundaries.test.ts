import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";
import { NETWORK_ONLY_NAVIGATIONS } from "../sw-routes";

const SRC = new URL("..", import.meta.url).pathname;

/** Every non-test source file under src/, as [path relative to src, text]. */
function sources(dir = SRC): [string, string][] {
  const out: [string, string][] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...sources(full));
    else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push([relative(SRC, full), readFileSync(full, "utf8")]);
    }
  }
  return out;
}

describe("module boundaries", () => {
  it("only auth/reconnect.ts sets location.href", () => {
    const files = sources();
    const setters = files.filter(([, text]) => /\blocation\.href\s*=[^=]/.test(text));
    expect(setters.map(([p]) => p)).toEqual(["auth/reconnect.ts"]);
  });

  it("only auth/signout.ts names CLEAR_COOKIE_URL or gcp-iap-mode", () => {
    const named = sources()
      .filter(([, text]) => /CLEAR_COOKIE_URL|gcp-iap-mode/.test(text))
      .map(([p]) => p)
      // sw-routes.ts must list the IAP query form to keep it network-only (#174).
      .filter((p) => p !== "sw-routes.ts");
    expect(named).toEqual(["auth/signout.ts"]);
  });

  it("no source mentions DO_SESSION_REFRESH apart from the #174 route list", () => {
    const named = sources()
      .filter(([, text]) => text.includes("DO_SESSION_REFRESH"))
      .map(([p]) => p)
      .filter((p) => p !== "sw-routes.ts");
    expect(named).toEqual([]);
  });

  it("keeps the reconnect path network-only in the service worker", async () => {
    const { RECONNECT_PATH } = await import("./reconnect");
    expect(NETWORK_ONLY_NAVIGATIONS.some((re) => re.test(RECONNECT_PATH))).toBe(true);
  });

  it("adds no sign-in or Google SDK dependency", () => {
    const pkg = JSON.parse(
      readFileSync(new URL("../../package.json", import.meta.url), "utf8"),
    ) as { dependencies: Record<string, string> };
    expect(Object.keys(pkg.dependencies)).toEqual(["preact"]);
  });
});
