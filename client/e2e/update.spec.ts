import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { extname, join, normalize } from "node:path";
import { expect, test } from "@playwright/test";

const TYPES: Record<string, string> = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".png": "image/png",
  ".webmanifest": "application/manifest+json",
};
const headers: Record<string, string> = JSON.parse(
  readFileSync(new URL("../security-headers.json", import.meta.url), "utf8"),
);

function build(version: string): string {
  const dir = `.e2e/v${version}`;
  execSync(`npx vite build --outDir ${dir} --emptyOutDir`, {
    env: { ...process.env, VITE_APP_VERSION: version },
    stdio: "pipe",
  });
  return dir;
}

test("a new version replaces the old precache and loads after a reload", async ({ page }) => {
  test.setTimeout(180_000);
  const roots = { "1": build("1"), "2": build("2") };
  let current: keyof typeof roots = "1";

  // Serves whichever build `current` names, as a deploy would replace the files in place.
  const server: Server = createServer((req, res) => {
    const path = new URL(req.url ?? "/", "http://x").pathname;
    const file = join(roots[current], normalize(path) === "/" ? "index.html" : normalize(path));
    try {
      res.writeHead(200, {
        ...headers,
        "Content-Type": TYPES[extname(file)] ?? "application/octet-stream",
        "Cache-Control": "no-cache",
      });
      res.end(readFileSync(file));
    } catch {
      res.writeHead(404).end();
    }
  });
  await new Promise<void>((ok) => server.listen(4180, ok));

  try {
    await page.goto("http://localhost:4180/");
    await expect(page.locator("#version")).toHaveAttribute("data-version", "1");
    await page.evaluate(() => navigator.serviceWorker.ready);
    const v1Assets = () =>
      page.evaluate(async () => {
        const urls: string[] = [];
        for (const name of await caches.keys()) {
          for (const r of await (await caches.open(name)).keys()) urls.push(new URL(r.url).pathname);
        }
        return urls.filter((u) => u.startsWith("/assets/") && u.endsWith(".js"));
      });
    await expect.poll(async () => (await v1Assets()).length).toBeGreaterThan(0);
    const before = await v1Assets();

    current = "2";
    // Reloading checks for a new service worker; autoUpdate activates it at once.
    await expect
      .poll(async () => {
        await page.reload();
        const after = await v1Assets();
        return after.length > 0 && before.every((u) => !after.includes(u));
      }, { timeout: 30_000 })
      .toBe(true);

    await page.reload();
    await expect(page.locator("#version")).toHaveAttribute("data-version", "2");
  } finally {
    server.close();
  }
});
