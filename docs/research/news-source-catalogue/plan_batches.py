#!/usr/bin/env python3
"""Plan full-enrichment batches from the current ranking (run after merge_score.py).

    python3 -I docs/research/news-source-catalogue/plan_batches.py [--size 26]

Writes batches.yaml:
  - ranked batches: the main ranking (news + specialist) in rank order, down to the rank at
    which every ranked-region minimum is met (protocol "Working down the ranking"). Latin
    America and Caribbean is filled by the regional batch instead, so it doesn't set the cut.
  - regional batch: Latin America and Caribbean entries below the cut (regional-quota
    additions plus pool entries in that region).
  - wire batch: major wire services below the cut. Reach is built from consumer traffic and
    surveys, which wires barely have, so the ranking can't place them (owner's decision,
    2026-11-08). The list is editorial.
Every outlet is in exactly one batch, so no host is checked by two agents.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("NEWSFEED_RESEARCH_DIR", Path.home() / ".newsfeed/research/news-sources"))
GROUP = {"uk": "UK", "north-america": "US and Canada", "europe": "Europe excl. UK", "asia": "Asia-Pacific",
         "oceania": "Asia-Pacific", "middle-east": "Middle East and Africa", "africa": "Middle East and Africa",
         "latin-america-caribbean": "Latin America and Caribbean"}
MINIMUMS = {"UK": 20, "US and Canada": 20, "Europe excl. UK": 15, "Asia-Pacific": 15,
            "Middle East and Africa": 10, "Latin America and Caribbean": 5}
REGIONAL = "Latin America and Caribbean"
WIRES = ["agence-france-presse", "united-press-international", "la-presse-canadienne",
         "press-trust-of-india", "australian-associated-press", "pa-media", "efe", "kyodo-news",
         "dpa", "xinhua-news-agency"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--size", type=int, default=26)
    a = ap.parse_args()
    rows = list(csv.DictReader((a.root / "ranked.csv").open(encoding="utf-8")))
    meta = yaml.safe_load((a.root / "catalogue.yaml").read_text(encoding="utf-8"))["meta"]

    counts: dict[str, int] = {g: 0 for g in MINIMUMS}
    reached: dict[str, int] = {}
    for r in rows:
        g = GROUP.get(r["region"])
        if g:
            counts[g] += 1
            if counts[g] == MINIMUMS[g]:
                reached[g] = int(r["rank"])
    ranked_groups = [g for g in MINIMUMS if g != REGIONAL]
    missing = [g for g in ranked_groups if g not in reached]
    if missing:
        raise SystemExit(f"minimum not reachable in the ranking for {missing}: plan a regional search")
    cut = max(reached[g] for g in ranked_groups)

    head = [r for r in rows if int(r["rank"]) <= cut]
    batches = []
    for n, start in enumerate(range(0, len(head), a.size), 1):
        chunk = head[start:start + a.size]
        batches.append({"batch": f"enrich-batch-{n:02d}", "kind": "ranked",
                        "ranks": f"{chunk[0]['rank']}-{chunk[-1]['rank']}",
                        "entries": [r["id"] for r in chunk]})
    tail = [r for r in rows if int(r["rank"]) > cut]
    latam = [r["id"] for r in tail if GROUP.get(r["region"]) == REGIONAL]
    n = len(batches) + 1
    batches.append({"batch": f"enrich-batch-{n:02d}", "kind": "regional", "group": REGIONAL,
                    "note": "regional-quota additions and pool entries in the region below the cut",
                    "entries": latam})
    by_id = {r["id"]: r for r in rows}
    wires = [w for w in WIRES if w in by_id and int(by_id[w]["rank"]) > cut and w not in latam]
    batches.append({"batch": f"enrich-batch-{n + 1:02d}", "kind": "wires",
                    "note": "major wires the ranking can't place: reach measures consumer traffic",
                    "entries": wires})

    plan = {
        "planned": dt.date.today().isoformat(),
        "ranking_date": meta.get("ranking_date"),
        "cut_rank": cut,
        "minimum_reached_at_rank": reached,
        "ranked_counts": counts,
        "total_entries": sum(len(b["entries"]) for b in batches),
        "batches": batches,
    }
    (a.root / "batches.yaml").write_text(yaml.safe_dump(plan, sort_keys=False, width=120), encoding="utf-8")
    print(f"cut at rank {cut}; {len(batches)} batches, {plan['total_entries']} entries -> batches.yaml")
    for b in batches:
        print(f"  {b['batch']} {b['kind']:8} {b.get('ranks', ''):>9} {len(b['entries'])} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
