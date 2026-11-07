import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { storyKey, type Feed, type FeedItem } from "./types";

const example = JSON.parse(
  readFileSync(new URL("../../../docs/feed.example.json", import.meta.url), "utf-8"),
) as Feed;

function item(overrides: Record<string, unknown>): FeedItem {
  return { ...example.items[0], ...overrides } as FeedItem;
}

describe("storyKey", () => {
  it("returns the id when the item has no story_id", () => {
    const withoutStory = { ...example.items[0], id: "item-1" } as Record<string, unknown>;
    delete withoutStory.story_id;
    expect(storyKey(withoutStory as unknown as FeedItem)).toBe("item-1");
  });

  it("returns the story_id when there is one", () => {
    expect(storyKey(item({ id: "item-1", story_id: "story-9" }))).toBe("story-9");
  });
});
