/** The score breakdown in words (#328). Pure: no state, no I/O. */

export interface ExplainRow {
  label: string;
  value: string;
  kind: "boost" | "damp" | "info";
}

const factor = (n: number): string => `×${n.toFixed(2)}`;

/** Keys that explain the numbers above them but aren't a reason of their own. */
function isMarker(key: string): boolean {
  return (
    key === "similarity_raw" || key === "coverage_sources" || key.startsWith("demote_body_only:")
  );
}

function kindOf(n: number): ExplainRow["kind"] {
  return n > 1 ? "boost" : n < 1 ? "damp" : "info";
}

function rowFor(
  key: string,
  n: number,
  coverage: number | undefined,
  sources: number | undefined,
): ExplainRow | null {
  if (isMarker(key)) return null;
  if (key.startsWith("topic:")) {
    return { label: `Matches ${key.slice(6)}`, value: `+${n}`, kind: "boost" };
  }
  if (key.startsWith("demote:")) {
    return { label: `Less of ${key.slice(7)}`, value: factor(n), kind: "damp" };
  }
  if (key.startsWith("pref:undemote:")) {
    return { label: `${key.slice(14)} no longer demoted`, value: factor(n), kind: "boost" };
  }
  if (key === "pref:clamp") {
    return { label: "Your settings, capped", value: factor(n), kind: "damp" };
  }
  if (key.startsWith("pref:source:") || key.startsWith("pref:topic:")) {
    const id = key.slice(key.indexOf(":", 5) + 1);
    return { label: `Your setting for ${id}`, value: factor(n), kind: kindOf(n) };
  }
  switch (key) {
    case "source":
      return n === 1 ? null : { label: "Source weight", value: factor(n), kind: kindOf(n) };
    case "recency":
      return { label: "Freshness", value: factor(n), kind: kindOf(n) };
    case "coverage": {
      const count = coverage ?? sources;
      const label =
        count === undefined ? "Covered by several sources" : `Covered by ${count} sources`;
      return { label, value: factor(n), kind: kindOf(n) };
    }
    case "similarity":
      return n === 1 ? null : { label: "Like what you read", value: factor(n), kind: kindOf(n) };
    case "diversity":
      return { label: "Spread across sources", value: factor(n), kind: kindOf(n) };
    case "muted":
    case "mute_keyword_in_body":
      return { label: "Muted keyword", value: "", kind: "damp" };
    default:
      // Unknown keys stay visible, so nothing the ranker did is hidden.
      return { label: key, value: String(n), kind: "info" };
  }
}

/**
 * The rows of "Why this story?". `coverage` is the item's own field, the number its card
 * shows; it wins over the rank stage's `coverage_sources` so the two always agree.
 */
export function explainBreakdown(
  breakdown: Record<string, number>,
  score: number,
  coverage?: number,
): ExplainRow[] {
  const rows: ExplainRow[] = [];
  for (const [key, n] of Object.entries(breakdown)) {
    const row = rowFor(key, n, coverage, breakdown["coverage_sources"]);
    if (row) rows.push(row);
  }
  rows.push({ label: "Score", value: score.toFixed(2), kind: "info" });
  return rows;
}
