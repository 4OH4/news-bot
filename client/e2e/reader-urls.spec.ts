import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

// #258: the reader's second sanitising pass keeps only http and https URLs, data: included.
// This runs in a real browser because DOMPurify does not behave faithfully under happy-dom.

type Item = Record<string, unknown> & { id: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;
const item = example.items[0]!;

const HTML = `
  <p>Invented text before.</p>
  <figure>
    <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="invented data picture">
    <img src="https://images.example.com/a.png" alt="invented https picture">
  </figure>
  <p><a href="data:text/html,%3Cb%3Ehi%3C/b%3E">invented data link</a>
     <a href="https://example.com/out">invented https link</a></p>`;

test.use({ viewport: { width: 360, height: 740 } });

test("data: image and link URLs are removed, and https ones are kept", async ({ page }) => {
  await page.route("**/api/feed", (route) => route.fulfill({ json: example }));
  await page.route(`**/api/articles/${item.id}`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: HTML }),
  );
  await page.route("https://images.example.com/**", (route) => route.abort());
  await page.goto(`/article/${item.id}`);
  await expect(page.getByText("Invented text before.")).toBeVisible();

  const main = page.locator("main");
  await expect(main.locator("img[alt='invented data picture']")).not.toHaveAttribute("src", /.*/);
  await expect(main.getByText("invented data link")).not.toHaveAttribute("href", /.*/);
  await expect(main.locator("img[alt='invented https picture']")).toHaveAttribute(
    "src",
    "https://images.example.com/a.png",
  );
  await expect(main.getByText("invented https link")).toHaveAttribute("href", "https://example.com/out");
});
