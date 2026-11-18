import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// #328: the breakdown opens from the card menu, and the Settings compare link loads the Feed
// with ranking=personal and a label. The feed is docs/feed.example.json with a known breakdown.

type Item = Record<string, unknown> & { id: string; title: string; display: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;
const top = {
  ...example.items[0]!,
  score: 5.6,
  score_breakdown: { "topic:ai": 6, recency: 0.8 },
  arm: "personal",
};
const feed: FeedBody = { ...example, items: [top] };

test.use({ serviceWorkers: "block", viewport: { width: 360, height: 740 } });

test("the card menu opens the score breakdown", async ({ page }) => {
  await page.route("**/api/feed?*", (route) => route.fulfill({ json: feed }));
  await page.goto("/");
  await page.locator("summary").first().click();
  await page.getByRole("button", { name: /why this story/i }).click();
  await expect(page.getByText("Matches ai")).toBeVisible();
  await expect(page.getByText("Placed by: personalised")).toBeVisible();
});

test("the Settings compare link loads the Feed with ranking=personal", async ({ page }) => {
  const asked: string[] = [];
  await page.route("**/api/feed?*", (route) => {
    asked.push(route.request().url());
    return route.fulfill({ json: feed });
  });
  await page.goto("/settings");
  await page.getByRole("link", { name: "Feed, personalised only" }).click();
  // Exact: the default substring match also finds the Settings link before it is clicked.
  await expect(page.getByText("Personalised only", { exact: true })).toBeVisible();
  // The label shows at once; the request follows it, so wait for the request.
  await expect
    .poll(() => asked.some((u) => new URL(u).searchParams.get("ranking") === "personal"))
    .toBe(true);
});
