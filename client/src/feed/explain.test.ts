import { describe, expect, it } from "vitest";
import { explainBreakdown } from "./explain";

// #328: the score breakdown in words. Every key family from the issue, with invented numbers.
const texts = (b: Record<string, number>, score = 1, coverage?: number) =>
  explainBreakdown(b, score, coverage).map((r) => `${r.label} ${r.value}`.trim());

describe("explainBreakdown", () => {
  it("words topic, freshness and similarity rows, drops source 1.0 and markers, ends with the score", () => {
    const rows = explainBreakdown(
      { "topic:ai": 6, source: 1, recency: 0.8, similarity: 2.2, similarity_raw: 0.61 },
      5.6,
    );
    expect(rows).toHaveLength(4);
    expect(rows[0]).toEqual({ label: "Matches ai", value: "+6", kind: "boost" });
    expect(rows[1]).toMatchObject({ label: "Freshness", value: "×0.80" });
    expect(rows[2]).toMatchObject({ label: "Like what you read", value: "×2.20" });
    expect(JSON.stringify(rows[3])).toContain("5.6");
    expect(JSON.stringify(rows)).not.toMatch(/source|0\.61/i);
  });

  it("shows a source weight other than 1 and leaves out similarity at 1", () => {
    const all = texts({ source: 1.5, similarity: 1 }).join("|");
    expect(all).toContain("Source weight ×1.50");
    expect(all).not.toContain("Like what you read");
  });

  it("words a user setting", () => {
    expect(texts({ "pref:topic:sport": 0.5 })[0]).toBe("Your setting for sport ×0.50");
  });

  it("words a demotion, a setting and an undemotion for the same topic", () => {
    const rows = explainBreakdown(
      { "demote:sport": 0.2, "pref:topic:sport": 2, "pref:undemote:sport": 5 },
      1,
    );
    const all = rows.map((r) => `${r.label} ${r.value}`);
    expect(all).toContain("Less of sport ×0.20");
    expect(all).toContain("Your setting for sport ×2.00");
    expect(all).toContain("sport no longer demoted ×5.00");
    expect(rows.find((r) => r.label.startsWith("Less of"))!.kind).toBe("damp");
  });

  it.each([
    ["pref:source:wire", 0.5, "Your setting for wire ×0.50"],
    ["pref:clamp", 0.7, "Your settings, capped ×0.70"],
    ["diversity", 0.9, "Spread across sources ×0.90"],
  ])("words %s", (key, value, text) => {
    expect(texts({ [key]: value })[0]).toBe(text);
  });

  it("uses the item's coverage for the sources count, else coverage_sources", () => {
    const b = { coverage: 1.27, coverage_sources: 3 };
    expect(texts(b, 1, 2)[0]).toBe("Covered by 2 sources ×1.27");
    expect(texts(b)[0]).toBe("Covered by 3 sources ×1.27");
    expect(texts(b)).toHaveLength(2);
  });

  it.each(["muted", "mute_keyword_in_body"])("words %s as a muted keyword", (key) => {
    expect(texts({ [key]: 1 })[0]).toContain("Muted keyword");
  });

  it("shows an unknown key and its number, and hides the other markers", () => {
    const all = texts({ foo: 1.5, "demote_body_only:sport": 1 }).join("|");
    expect(all).toContain("foo");
    expect(all).toContain("1.5");
    expect(all).not.toContain("demote_body_only");
  });
});
