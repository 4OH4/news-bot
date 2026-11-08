#!/usr/bin/env python3
"""Count English Wikipedia articles that credit each wire service in a citation's agency= field.

Wire copy is published on client sites, so links to a wire's own domain (wiki_cites)
undercount it. Citation templates name the wire in `agency=`; one CirrusSearch query per
variant gives the number of articles with such a credit. CirrusSearch's OR between
insource: phrases returns fewer hits than either phrase alone, and regex insource: times
out with partial results, so variants are queried separately and combined by
inclusion-exclusion over AND queries (an article using two spellings counts once).

    python3 -I docs/research/news-source-catalogue/wiki_agency.py              # agency credits
    python3 -I docs/research/news-source-catalogue/wiki_agency.py --calibrate  # links per citing article

Output: raw/wiki-agency/agency-credits-<date>.csv  (id, name, query, articles, per_variant)
        raw/wiki-agency/links-per-page-calibration-<date>.txt: for each calibration domain, the
        articles whose source contains the domain, its link count from the externallinks dump
        (raw/wiki-cites/host-counts-*.tsv.gz), and their ratio; merge_score.py uses the median.
Polite: one request a second, User-Agent naming the project (Wikimedia API etiquette; robots.txt's /w/ rule targets crawlers, accepted by the owner).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import itertools
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("NEWSFEED_RESEARCH_DIR", Path.home() / ".newsfeed/research/news-sources"))
UA = "newsfeed-research/0.1 (+https://github.com/4OH4/news-bot)"
API = "https://en.wikipedia.org/w/api.php"

# Name variants as they appear in agency= (editorial list; entries not listed use name and aliases).
VARIANTS = {
    "reuters": ["Reuters"],
    "associated-press": ["Associated Press", "AP"],
    "bloomberg": ["Bloomberg", "Bloomberg News"],
    "ria-novosti": ["RIA Novosti", "Sputnik"],
    "yonhap": ["Yonhap", "Yonhap News Agency"],
    "mehr-news-agency": ["Mehr News Agency", "Mehr"],
    "irna": ["IRNA", "Islamic Republic News Agency"],
    "anadolu-agency": ["Anadolu Agency", "Anadolu"],
    "agence-france-presse": ["Agence France-Presse", "Agence France Presse", "AFP"],
    "xinhua-news-agency": ["Xinhua", "Xinhua News Agency"],
    "isna": ["ISNA", "Iranian Students' News Agency"],
    "united-press-international": ["United Press International", "UPI"],
    "inter-press-service": ["Inter Press Service", "IPS"],
    "tass": ["TASS", "ITAR-TASS"],
    "efe": ["EFE", "Agencia EFE"],
    "asian-news-international": ["Asian News International", "ANI"],
    "tasnim-news-agency": ["Tasnim News Agency", "Tasnim"],
    "kyodo-news": ["Kyodo News", "Kyodo"],
    "la-presse-canadienne": ["The Canadian Press", "Canadian Press", "CP"],
    "press-trust-of-india": ["Press Trust of India", "PTI"],
    "dpa": ["dpa", "Deutsche Presse-Agentur"],
    "australian-associated-press": ["Australian Associated Press", "AAP"],
    "pa-media": ["PA Media", "Press Association", "PA"],
    "news-agency-of-nigeria": ["News Agency of Nigeria", "NAN"],
    "agencia-lusa": ["Agência Lusa", "Lusa"],
}


def variants(e: dict) -> list[str]:
    if e["id"] in VARIANTS:
        return VARIANTS[e["id"]]
    names = [e["name"]] + list(e.get("aliases") or [])
    out = []
    for n in names:
        base = n.split("(")[0].strip()
        if base and base not in out:
            out.append(base)
    return out


# Large news domains used to measure links per citing article.
CALIBRATION_DOMAINS = ["bbc.co.uk", "nytimes.com", "theguardian.com", "reuters.com", "apnews.com", "cnn.com",
                       "washingtonpost.com", "independent.co.uk", "aljazeera.com", "abc.net.au", "thehindu.com",
                       "latimes.com"]


def totalhits(query: str, tries: int = 4) -> int:
    """Article count for a search; one request a second, backing off on errors."""
    params = {"action": "query", "list": "search", "srsearch": query, "srnamespace": "0",
              "srlimit": "1", "srinfo": "totalhits", "format": "json"}
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    for attempt in range(tries):
        time.sleep(1)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return int(json.load(r)["query"]["searchinfo"]["totalhits"])
        except (OSError, KeyError, ValueError):
            if attempt == tries - 1:
                raise
            time.sleep(5 * 2**attempt)
    raise AssertionError("unreachable")


def calibrate(root: Path, out_dir: Path) -> Path:
    found = list((root / "raw" / "wiki-cites").glob("host-counts-*.tsv.gz"))
    if not found:
        raise SystemExit(f"no raw/wiki-cites/host-counts-*.tsv.gz under {root}: run count_wiki_cites.sh first")
    counts = max(found)
    links = dict.fromkeys(CALIBRATION_DOMAINS, 0)
    with gzip.open(counts, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            c, _, host = line.rstrip("\n").partition("\t")
            for d in CALIBRATION_DOMAINS:
                if host == d or host.endswith("." + d):
                    links[d] += int(c)
    lines = []
    for d in CALIBRATION_DOMAINS:
        pages = totalhits(f'insource:"{d}"')
        lines.append(f"{d} pages={pages} links={links[d]} ratio={links[d] / pages:.2f}")
        print(lines[-1])
    out = out_dir / f"links-per-page-calibration-{dt.date.today().isoformat()}.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--calibrate", action="store_true", help="measure links per citing article instead")
    a = ap.parse_args()
    if not (a.root / "catalogue.yaml").exists():
        raise SystemExit(f"no catalogue.yaml under {a.root}: check --root or NEWSFEED_RESEARCH_DIR")
    out_dir = a.root / "raw" / "wiki-agency"
    out_dir.mkdir(parents=True, exist_ok=True)
    if a.calibrate:
        print(f"wrote {calibrate(a.root, out_dir)}")
        return 0
    cat = yaml.safe_load((a.root / "catalogue.yaml").read_text(encoding="utf-8"))
    wires = [e for e in cat["entries"] if e["kind"] == "wire"]
    out = out_dir / f"agency-credits-{dt.date.today().isoformat()}.csv"
    tmp = out.with_suffix(".csv.part")  # renamed only when complete, so a failure keeps the old file
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "query", "articles", "per_variant"])
        for e in wires:
            vs = variants(e)
            terms = [f'insource:"agency={v}"' for v in vs]
            total, per = 0, {}
            for k in range(1, len(terms) + 1):
                for combo in itertools.combinations(range(len(terms)), k):
                    n = totalhits(" ".join(terms[i] for i in combo))
                    total += n if k % 2 else -n
                    if k == 1:
                        per[vs[combo[0]]] = n
            w.writerow([e["id"], e["name"], " | ".join(terms), total, json.dumps(per)])
            print(f"{total:>7}  {e['id']}  {per}")
    tmp.replace(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
