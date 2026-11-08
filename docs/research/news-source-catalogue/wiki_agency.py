#!/usr/bin/env python3
"""Count English Wikipedia articles that credit each wire service in a citation's agency= field.

Wire copy is published on client sites, so links to a wire's own domain (wiki_cites)
undercount it. Citation templates name the wire in `agency=`; one CirrusSearch query per
variant gives the number of articles with such a credit. CirrusSearch's OR between
insource: phrases returns fewer hits than either phrase alone, and regex insource: times
out with partial results, so variants are queried separately and combined by
inclusion-exclusion over AND queries (an article using two spellings counts once).

    python3 -I docs/research/news-source-catalogue/wiki_agency.py   # reads catalogue.yaml, writes raw/wiki-agency/

Output: raw/wiki-agency/agency-credits-<date>.csv  (id, name, query, articles)
Polite: one request a second, generic User-Agent.
"""

from __future__ import annotations

import csv
import datetime as dt
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


def totalhits(query: str) -> int:
    params = {"action": "query", "list": "search", "srsearch": query, "srnamespace": "0",
              "srlimit": "1", "srinfo": "totalhits", "format": "json"}
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return int(json.load(r)["query"]["searchinfo"]["totalhits"])


def main() -> int:
    cat = yaml.safe_load((ROOT / "catalogue.yaml").read_text(encoding="utf-8"))
    wires = [e for e in cat["entries"] if e["kind"] == "wire"]
    out_dir = ROOT / "raw" / "wiki-agency"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"agency-credits-{dt.date.today().isoformat()}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "query", "articles", "per_variant"])
        for e in wires:
            vs = variants(e)
            terms = [f'insource:"agency={v}"' for v in vs]
            total, per = 0, {}
            for k in range(1, len(terms) + 1):
                for combo in itertools.combinations(range(len(terms)), k):
                    n = totalhits(" ".join(terms[i] for i in combo))
                    time.sleep(1)
                    total += n if k % 2 else -n
                    if k == 1:
                        per[vs[combo[0]]] = n
            w.writerow([e["id"], e["name"], " | ".join(terms), total, json.dumps(per)])
            print(f"{total:>7}  {e['id']}  {per}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
