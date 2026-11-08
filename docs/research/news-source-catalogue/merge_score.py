#!/usr/bin/env python3
"""Merge harvest shards, join bulk signals, score and rank the news-source catalogue.

Follows docs/research/news-source-catalogue.md (news-bot repo): "Scope" (brand rule),
"Regions come from countries", "Ranked entries", "Scoring" and "Checking our coverage".

Re-runnable: reads shards/ and raw/, writes catalogue.yaml and ranked.csv. Change the
weights with flags, e.g.:

    python3 -I docs/research/news-source-catalogue/merge_score.py --w-reach 0.5 --w-trust 0.25 --w-authority 0.25

Needs PyYAML (system python3, or `uv run --project backend python ...`).
Inputs (under the research root: $NEWSFEED_RESEARCH_DIR, default ~/.newsfeed/research/news-sources):
  shards/harvest-*.yaml            seed harvests
  raw/tranco/*.csv(.zip)           full Tranco list (rank,domain)
  raw/crux/crux-global-*.csv.gz    CrUX top list (origin,rank band)
  raw/wiki-cites/host-counts-*.tsv.gz   link counts per host from the externallinks dump
  raw/online-pop/                  World Bank internet-users % and population
  raw/m49/m49.csv                  UN M49 table
  repo: config/sources.yaml, docs/blocked-publishers.md (local reads only)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import gzip
import io
import json
import math
import os
import re
import statistics
import sys
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("NEWSFEED_RESEARCH_DIR", Path.home() / ".newsfeed/research/news-sources"))
REPO = Path(__file__).resolve().parents[3]  # this file is docs/research/news-source-catalogue/

# --------------------------------------------------------------------------- regions

REGION_OVERRIDES = {
    "GB": "uk",
    "AE": "middle-east", "BH": "middle-east", "KW": "middle-east", "OM": "middle-east",
    "QA": "middle-east", "SA": "middle-east", "IQ": "middle-east", "IR": "middle-east",
    "IL": "middle-east", "PS": "middle-east", "JO": "middle-east", "LB": "middle-east",
    "SY": "middle-east", "YE": "middle-east", "EG": "middle-east", "TR": "middle-east",
    "AM": "europe", "AZ": "europe", "GE": "europe", "CY": "europe",
}
# Not in the UN M49 table; added by hand (protocol: own ISO codes for HK, TW, PS).
EXTRA_COUNTRIES = {
    "TW": ("Taiwan", "Eastern Asia", "asia"),
    "XK": ("Kosovo", "Southern Europe", "europe"),
}


def build_regions(m49_csv: Path) -> dict[str, dict]:
    """Map each ISO alpha-2 code to {name, region, subregion} from M49 plus protocol exceptions."""
    out: dict[str, dict] = {}
    with m49_csv.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            iso = r["ISO-alpha2 Code"].strip()
            if not iso:
                continue
            m_region, sub, inter = r["Region Name"], r["Sub-region Name"], r["Intermediate Region Name"]
            if iso in REGION_OVERRIDES:
                region = REGION_OVERRIDES[iso]
            elif sub == "Northern America":
                region = "north-america"
            elif sub == "Latin America and the Caribbean":
                region = "latin-america-caribbean"
            elif m_region == "Europe":
                region = "europe"
            elif m_region == "Africa":
                region = "africa"
            elif m_region == "Asia":
                region = "asia"
            elif m_region == "Oceania":
                region = "oceania"
            else:  # Antarctica and similar: no region in the vocabulary
                region = None
            out[iso] = {"name": r["Country or Area"], "region": region,
                        "subregion": sub or None, "m49_intermediate": inter or None}
    for iso, (name, sub, region) in EXTRA_COUNTRIES.items():
        out.setdefault(iso, {"name": name, "region": region, "subregion": sub,
                             "m49_intermediate": None, "note": "not in UN M49; added by hand"})
    return out


# --------------------------------------------------------------------------- helpers

def norm_domain(d: str) -> str:
    d = d.strip().lower()
    d = re.sub(r"^[a-z]+://", "", d).split("/")[0].split(":")[0]
    d = re.sub(r"^www\d*\.", "", d)
    return d.strip(".")


STOP = {"the", "online", "com", "co", "uk", "website", "site", "digital"}


def norm_name(n: str) -> str:
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[^a-z0-9]+", " ", n)  # keep parentheticals: "Al Jazeera (Arabic)" is distinct
    return " ".join(w for w in n.split() if w not in STOP)


def slug(n: str) -> str:
    s = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unnamed"


def as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# Registrable domains that host several unrelated brands or are shared infrastructure.
# They never link records into one brand, and a bulk signal measured on one is credited to
# every entry listing it (noted on the signal), since it can't be split.
SHARED_DOMAINS = {
    "google.com", "yahoo.com", "msn.com", "aol.com", "go.com", "indiatimes.com", "sky.com",
    "substack.com", "medium.com", "pressreader.com", "gannett-dns.com", "arcpublishing.com",
    "blogspot.com", "wordpress.com", "apple.com", "microsoft.com", "naver.com", "yimg.com",
}
# Domains of a distinct masthead that a multi-domain source row (e.g. one RSP row for
# "Al Jazeera") would otherwise fuse with a sibling brand. They don't link records.
NO_LINK_DOMAINS = {"aljazeera.net"}


class Domains:
    """Assign hosts to entries by the longest matching entry domain (suffix match)."""

    def __init__(self, entries: list[dict]):
        self.owner: dict[str, list[int]] = defaultdict(list)
        for i, e in enumerate(entries):
            for d in e["domains"]:
                self.owner[d].append(i)

    def entries_for(self, host: str) -> tuple[list[int], str | None]:
        host = norm_domain(host)
        parts = host.split(".")
        for k in range(len(parts) - 1):
            cand = ".".join(parts[k:])
            if cand in self.owner:
                return self.owner[cand], cand
        return [], None


# --------------------------------------------------------------------------- loading shards

# Name preference when merging: DNR often names a channel (e.g. a radio station) rather
# than the brand, so it comes last; within a seed, prefer records with online reach.
SEED_ORDER = ["press-gazette", "rsp", "tranco", "dnr"]
GENERIC_PAT = re.compile(
    r"^(local|regional|other|any)\b.*\b(news|newspapers?|tv|television|radio|stations?|press)\b"
    r"|\b(local|regional) (television|tv|radio|newspapers?)\b",
    re.IGNORECASE,
)


ENTRY_SHARD_GLOBS = {"harvest-*.yaml": "pool", "manual-*.yaml": "manual", "regional-*.yaml": "regional-quota"}


def load_shards(shard_dir: Path) -> tuple[list[dict], dict]:
    """Load entry shards: seed harvests (added_by pool), manual-*.yaml and regional-*.yaml."""
    recs, meta = [], {}
    paths = [(p, ab) for g, ab in ENTRY_SHARD_GLOBS.items() for p in sorted(shard_dir.glob(g))]
    for p, default_added_by in paths:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        seed = doc.get("seed") or p.stem.removeprefix("harvest-")
        meta[seed] = {k: doc.get(k) for k in ("source_keys", "retrieved", "notes")}
        for e in doc.get("entries") or []:
            e = dict(e)
            e["_seed"] = seed
            e["_added_by"] = e.get("added_by") or doc.get("added_by") or default_added_by
            e["domains"] = sorted({norm_domain(d) for d in as_list(e.get("domains")) if d})
            recs.append(e)
    return recs, meta


def cluster(recs: list[dict]) -> list[list[dict]]:
    """Union records sharing a domain, or the same normalised name and a compatible country."""
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_dom: dict[str, int] = {}
    by_name: dict[tuple[str, str], int] = {}
    for i, r in enumerate(recs):
        for d in r["domains"]:
            if d in SHARED_DOMAINS or d in NO_LINK_DOMAINS:
                continue
            if d in by_dom:
                union(by_dom[d], i)
            else:
                by_dom[d] = i
    for i, r in enumerate(recs):
        nn = norm_name(r.get("name", ""))
        if not nn or GENERIC_PAT.search(r.get("name", "")):
            continue
        c = ((r.get("location") or {}).get("country") or "").upper()
        key = (nn, c)
        if key in by_name:
            union(by_name[key], i)
        else:
            by_name[key] = i
    # Name with unknown country joins a same-name record when exactly one country has it.
    names_countries: dict[str, set] = defaultdict(set)
    for (nn, c), i in by_name.items():
        names_countries[nn].add(c)
    for (nn, c), i in by_name.items():
        if c == "":
            others = names_countries[nn] - {""}
            if len(others) == 1:
                union(by_name[(nn, others.pop())], i)
    groups: dict[int, list[dict]] = defaultdict(list)
    for i, r in enumerate(recs):
        groups[find(i)].append(r)
    return list(groups.values())


def load_overrides(root: Path) -> dict:
    """Read overrides.yaml plus shards/override-*.yaml (written by check agents).

    Schema (all keys optional):
      merge: [{ids: [keep-id, other-id, ...], note: "..."}]   # fuse entries; first id is kept
      set: {entry-id: {"location.country": "IN", "english_edition": "partial", note: "..."}}
      shared_domains: [...]     # added to SHARED_DOMAINS
      no_link_domains: [...]    # added to NO_LINK_DOMAINS
    """
    out = {"merge": [], "set": [], "shared_domains": [], "no_link_domains": []}
    files = [root / "overrides.yaml"] + sorted((root / "shards").glob("override-*.yaml"))
    for p in files:
        if not p.exists():
            continue
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        out["merge"] += [dict(m, _file=p.name) for m in doc.get("merge") or []]
        out["set"] += [(eid, fields, p.name) for eid, fields in (doc.get("set") or {}).items()]
        out["shared_domains"] += doc.get("shared_domains") or []
        out["no_link_domains"] += doc.get("no_link_domains") or []
    return out


def apply_overrides(entries: list[dict], ov: dict) -> list[dict]:
    by_id = {e["id"]: e for e in entries}
    for m in ov["merge"]:
        ids = [i for i in m["ids"] if i in by_id]
        missing = set(m["ids"]) - set(ids)
        if missing:
            print(f"warning: override merge ({m['_file']}): unknown ids {sorted(missing)}", file=sys.stderr)
        if len(ids) < 2:
            continue
        keep = by_id[ids[0]]
        recs = [r for i in ids for r in by_id[i]["_recs"]]
        merged = merge_group(recs)
        merged["id"] = keep["id"]
        merged["name"] = keep["name"]
        merged["_notes"].append(f"override ({m['_file']}): merged {', '.join(ids[1:])}. {m.get('note', '')}".strip())
        for i in ids[1:]:
            del by_id[i]
        by_id[keep["id"]] = merged
    for eid, fields, fname in ov["set"]:
        e = by_id.get(eid)
        if e is None:
            print(f"warning: override set ({fname}): unknown id {eid}", file=sys.stderr)
            continue
        fields = dict(fields)
        note = fields.pop("note", "")
        for path, value in fields.items():
            tgt, parts = e, path.split(".")
            for k in parts[:-1]:
                tgt = tgt.setdefault(k, {})
            tgt[parts[-1]] = value
        e["_notes"].append(f"override ({fname}): {', '.join(f'{k}={v}' for k, v in fields.items())}"
                           + (f". {note}" if note else ""))
    return list(by_id.values())


def mode(vals):
    vals = [v for v in vals if v not in (None, "", [])]
    if not vals:
        return None
    counts = defaultdict(int)
    for v in vals:
        counts[json.dumps(v, sort_keys=True)] += 1
    return json.loads(max(counts, key=counts.get))


SIGNAL_KEYS = ["dnr_reach_online", "dnr_reach_offline", "dnr_trust", "rsp", "rsp_topics",
               "wiki_cites", "tranco_rank", "crux_band", "similarweb_visits",
               "press_gazette_rank", "ipsos_iris_audience", "gdelt_inbound", "mediacloud_inbound"]
LIST_SIGNALS = {"dnr_reach_online", "dnr_reach_offline", "dnr_trust", "rsp_topics",
                "press_gazette_rank", "similarweb_visits", "ipsos_iris_audience"}


def merge_group(g: list[dict]) -> dict:
    # Hand-curated records (manual, regional-quota) name the brand best, so they come first.
    g = sorted(g, key=lambda r: (SEED_ORDER.index(r["_seed"]) if r["_seed"] in SEED_ORDER
                                 else (-1 if r["_added_by"] != "pool" else 9),
                                 not (r.get("signals") or {}).get("dnr_reach_online"),
                                 "offline-only" in (r.get("source_note") or "")))
    name = g[0].get("name")
    country = mode([((r.get("location") or {}).get("country") or "").upper() or None for r in g])
    kinds = [r.get("kind") for r in g]
    kind = mode([k for k in kinds if k != "other"]) or mode(kinds) or "other"
    ee_rank = {"full": 2, "partial": 1, "none": 0}
    ees = [r.get("english_edition") for r in g if r.get("english_edition") in ee_rank]
    english_edition = max(ees, key=ee_rank.get) if ees else None
    signals: dict = {}
    for k in SIGNAL_KEYS:
        vals = [r.get("signals", {}).get(k) for r in g if (r.get("signals") or {}).get(k) not in (None, [], {})]
        if not vals:
            continue
        if k in LIST_SIGNALS:
            rows, seen = [], set()
            for v in vals:
                for row in as_list(v):
                    key = json.dumps(row, sort_keys=True)
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
            signals[k] = rows
        elif k == "tranco_rank":
            signals[k] = min(vals, key=lambda v: v.get("value") or 10**9)
        else:
            signals[k] = vals[0]
            if len(vals) > 1 and any(json.dumps(v, sort_keys=True) != json.dumps(vals[0], sort_keys=True) for v in vals):
                signals.setdefault("_conflicts", []).append({k: vals})
    notes = [f"{r['_seed']}: {r['source_note']}" for r in g if r.get("source_note")]
    aliases = sorted({r.get("name") for r in g if r.get("name") and r.get("name") != name})
    return {
        "name": name,
        "aliases": aliases,
        "domains": sorted({d for r in g for d in r["domains"]}),
        "kind": kind,
        "languages": sorted({l for r in g for l in as_list(r.get("languages"))}),
        "english_edition": english_edition,
        "location": {"country": country},
        "topics": sorted({t for r in g for t in as_list(r.get("topics"))}) or None,
        "signals": signals,
        "harvested_from": sorted({r["_seed"] for r in g}),
        "added_by": "pool" if any(r["_added_by"] == "pool" for r in g) else g[0]["_added_by"],
        "_recs": g,
        "_notes": notes,
        "_generic": bool(GENERIC_PAT.search(name or "")) and not any(r["domains"] for r in g),
    }


# --------------------------------------------------------------------------- bulk signals

def load_tranco(raw: Path):
    files = sorted(glob.glob(str(raw / "tranco" / "*.csv"))) + sorted(glob.glob(str(raw / "tranco" / "*.zip")))
    files = [f for f in files if "classification" not in f]
    if not files:
        return None, None
    f = files[-1]
    m = re.search(r"(\d{4}-\d{2}-\d{2})", f)
    key = f"tranco-{m.group(1)}" if m else "tranco"
    if f.endswith(".zip"):
        with zipfile.ZipFile(f) as z:
            text = io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8").read()
    else:
        text = Path(f).read_text(encoding="utf-8")
    ranks = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and row[0].isdigit():
            ranks.setdefault(norm_domain(row[1]), int(row[0]))
    return ranks, key


def load_crux(raw: Path):
    files = sorted(glob.glob(str(raw / "crux" / "crux-global-*.csv.gz")))
    if not files:
        return None, None
    f = files[-1]
    ym = re.search(r"(\d{4})(\d{2})", Path(f).name)
    key = f"crux-{ym.group(1)}-{ym.group(2)}"
    bands = []
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            bands.append((row["origin"], int(row["rank"])))
    return bands, key


def load_wiki_counts(raw: Path):
    files = sorted(glob.glob(str(raw / "wiki-cites" / "host-counts-*.tsv.gz")))
    if not files:
        return None, None
    f = files[-1]
    d = re.search(r"(\d{4})(\d{2})(\d{2})", Path(f).name)
    key = f"wiki-cites-{d.group(1)}-{d.group(2)}-{d.group(3)}"
    def gen():
        with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                c, _, h = line.rstrip("\n").partition("\t")
                if h:
                    yield h, int(c)
    return gen, key


def load_online_pop(raw: Path):
    """ISO2 -> (internet users, year, pct). Internet % latest year x population that year."""
    base = raw / "online-pop"
    iso3to2 = {}
    try:
        cj = json.loads((base / "wb-countries.json").read_text())
        for c in cj[1]:
            if c.get("iso2Code") and c.get("region", {}).get("value") != "Aggregates":
                iso3to2[c["id"]] = c["iso2Code"]
    except FileNotFoundError:
        return {}, None

    def read(pattern):
        f = glob.glob(str(base / pattern))[0]
        with open(f, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        hdr = rows[4]
        out = {}
        for r in rows[5:]:
            vals = {hdr[i]: float(r[i]) for i in range(4, len(r)) if i < len(hdr) and r[i]}
            out[r[1]] = vals
        return out

    pct, pop = read("wb-IT.NET.USER.ZS/API_*.csv"), read("wb-SP.POP.TOTL/API_*.csv")
    out = {}
    for iso3, pv in pct.items():
        if iso3 not in iso3to2 or not pv or iso3 not in pop:
            continue
        year = max(pv)
        p = pop[iso3].get(year) or (pop[iso3][max(pop[iso3])] if pop[iso3] else None)
        if p:
            out[iso3to2[iso3]] = (pv[year] / 100 * p, year, pv[year])
    return out, "online-pop-wb-2026-11"


# Topic groups. "soft" outlets get their own ranking (owner's decision, 2026-11-08); an
# entry is soft only if every topic it has is soft. Harvesters used slightly different
# tags, so they're normalised first.
TOPIC_ALIASES = {"games": "gaming", "technology": "tech", "sports": "sport", "celebrities": "celebrity"}
SOFT_TOPICS = {"entertainment", "celebrity", "gaming", "sport", "lifestyle", "culture", "music",
               "travel", "food", "royalty", "adult-industry", "consumer", "fashion"}
NEWS_TOPICS = {"general", "politics", "world", "local", "regional", "investigations", "current-affairs"}


def topic_group(topics) -> str:
    t = {TOPIC_ALIASES.get(x.lower(), x.lower()) for x in as_list(topics)}
    if not t or t & NEWS_TOPICS:
        return "news"
    if t <= SOFT_TOPICS:
        return "soft"
    return "specialist"


def load_gdelt(raw: Path) -> dict | None:
    """Read BigQuery exports in raw/gdelt/ (see gdelt_bq.sh), if present."""
    base = raw / "gdelt"
    files = {k: base / f"{k}.csv" for k in ("inbound", "orgs", "volume")}
    if not files["inbound"].exists():
        return None
    meta = yaml.safe_load((base / "meta.yaml").read_text()) if (base / "meta.yaml").exists() else {}
    key = meta.get("source_key", "gdelt")

    def rows(p):
        if not p.exists():
            return []
        with p.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    return {"key": key, "inbound": rows(files["inbound"]), "orgs": rows(files["orgs"]),
            "volume": rows(files["volume"])}


def join_gdelt(entries: list[dict], gd: dict) -> None:
    """gdelt_inbound = distinct outlet domains linking to the entry (domain-level rows for
    registrable domains, host-level rows for subdomain entries; max across an entry's
    domains, a lower bound for multi-domain brands). For wires, distinct domains naming
    the wire is used when larger, since wire copy runs on client sites."""
    dom_rows = {r["target"]: r for r in gd["inbound"] if r.get("level") == "domain"}
    host_rows = {r["target"]: r for r in gd["inbound"] if r.get("level") == "host"}
    orgs = {norm_name(r["org"]): r for r in gd["orgs"]}
    vol = {r["domain"]: int(r["articles"]) for r in gd["volume"]}
    for e in entries:
        best, links, via = None, 0, None
        for d in e["domains"]:
            if d in SHARED_DOMAINS:
                continue
            r = dom_rows.get(d) or host_rows.get(d) or host_rows.get("www." + d)
            if r:
                links += int(r["links"])
                if best is None or int(r["linking_domains"]) > best:
                    best, via = int(r["linking_domains"]), d
        s = e["signals"]
        if best is not None:
            s["gdelt_inbound"] = {"value": best, "links": links, "source": gd["key"], "domain": via,
                                  "method": "distinct GDELT source domains linking (PAGE_LINKS), self-links excluded"}
        if e["kind"] == "wire":
            names = [e["name"]] + e["aliases"] + WIRE_ORG_NAMES.get(e["id"], [])
            hits = [orgs[n] for n in {norm_name(x) for x in names} if n in orgs]
            if hits:
                h = max(hits, key=lambda r: int(r["domains"]))
                s["gdelt_mentions"] = {"value": int(h["domains"]), "articles": int(h["articles"]),
                                       "org": h["org"], "source": gd["key"]}
                if int(h["domains"]) > (best or 0):
                    s["gdelt_inbound"] = {"value": int(h["domains"]), "source": gd["key"],
                                          "method": "distinct GDELT source domains naming the wire "
                                                    f"('{h['org']}'), larger than its link count ({best})"}
        v = sum(vol.get(d, 0) for d in e["domains"] if d not in SHARED_DOMAINS)
        if v:
            s["gdelt_volume"] = {"value": v, "source": gd["key"], "method": "articles in GDELT GKG over the window"}


# Extra names GDELT may use for wires (GKG organisation names are lower case, no punctuation).
WIRE_ORG_NAMES = {
    "agence-france-presse": ["agence france presse", "agence france-presse", "afp"],
    "associated-press": ["associated press", "ap"],
    "reuters": ["reuters", "thomson reuters"],
    "united-press-international": ["united press international", "upi"],
    "xinhua-news-agency": ["xinhua", "xinhua news agency"],
    "kyodo-news": ["kyodo", "kyodo news", "kyodo news agency"],
    "pa-media": ["press association", "pa media"],
    "press-trust-of-india": ["press trust of india", "pti"],
    "efe": ["efe", "agencia efe"],
    "anadolu-agency": ["anadolu agency", "anadolu"],
    "bloomberg": ["bloomberg", "bloomberg news"],
    "yonhap": ["yonhap", "yonhap news agency"],
    "ansa": ["ansa"],
    "la-presse-canadienne": ["canadian press", "the canadian press", "la presse canadienne"],
    "australian-associated-press": ["australian associated press", "aap"],
    "dpa": ["deutsche presse agentur", "dpa"],
}


def load_wiki_agency(raw: Path) -> tuple[dict[str, int], float, str | None]:
    """Latest raw/wiki-agency/agency-credits-*.csv and the links-per-page calibration median."""
    base = raw / "wiki-agency"
    files = sorted(base.glob("agency-credits-*.csv"))
    if not files:
        return {}, 0.0, None
    f = files[-1]
    key = "wiki-agency-" + f.stem.removeprefix("agency-credits-")
    with f.open(newline="", encoding="utf-8") as fh:
        credits = {r["id"]: int(r["articles"]) for r in csv.DictReader(fh)}
    ratios = []
    for cal in sorted(base.glob("links-per-page-calibration-*.txt"))[-1:]:
        ratios = [float(m) for m in re.findall(r"ratio=([\d.]+)", cal.read_text())]
    lpp = statistics.median(ratios) if ratios else 2.5
    return credits, lpp, key


def shared_note(d: str) -> dict:
    root = next((s for s in SHARED_DOMAINS if d == s or d.endswith("." + s)), None)
    return {"note": f"measured on shared domain {root}; credited to every entry listing it"} if root else {}


# --------------------------------------------------------------------------- scoring

# DNR surveys some countries as separate language markets. A segment's % is a share of
# that segment's online population, so it is weighted by the segment's approximate share
# of the country's population (census language shares, rounded; a computation parameter,
# not a copied signal value). Unknown segments fall back to weight 1 with a warning.
SEGMENT_SHARE = {
    ("BE", "dutch"): 0.6, ("BE", "flemish"): 0.6, ("BE", "french"): 0.4,
    ("CA", "english"): 0.77, ("CA", "french"): 0.23,
    ("CH", "german"): 0.7, ("CH", "french"): 0.25,
}


def segment_share(country: str, segment: str | None) -> float:
    if not segment:
        return 1.0
    seg = segment.lower()
    for (c, lang), w in SEGMENT_SHARE.items():
        if c == country and lang in seg:
            return w
    print(f"warning: no population share for DNR segment {country}/{segment}", file=sys.stderr)
    return 1.0


RSP_SCORE = {"generally-reliable": 1.0, "no-consensus": 0.5, "generally-unreliable": 0.1,
             "deprecated": 0.0, "blacklisted": 0.0}


def percentiles(values: dict[int, float], higher_better: bool = True) -> dict[int, float]:
    """Mid-rank percentile in [0, 1] among entries that have the value. Log transform is
    applied by the caller for documentation only: percentiles are rank-based, so any
    monotone transform (log included) gives the same result."""
    items = sorted(values.items(), key=lambda kv: kv[1] if higher_better else -kv[1])
    n = len(items)
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        p = ((i + j) / 2 + 0.5) / n
        for k in range(i, j + 1):
            out[items[k][0]] = p
        i = j + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--repo", type=Path, default=REPO)
    ap.add_argument("--w-reach", type=float, default=0.4)
    ap.add_argument("--w-trust", type=float, default=0.3)
    ap.add_argument("--w-authority", type=float, default=0.3)
    ap.add_argument("--percentile-pool", choices=["all", "ranked"], default="all",
                    help="entries percentiles and medians are computed over (protocol: 'the pool')")
    ap.add_argument("--use-iris", action="store_true",
                    help="score Ipsos iris UK audience as a reach measure (recorded but unscored by default)")
    ap.add_argument("--use-similarweb", action="store_true",
                    help="score Similarweb visits as a reach measure (recorded but unscored by default)")
    a = ap.parse_args(argv)
    root, raw = a.root, a.root / "raw"
    today = dt.date.today().isoformat()

    overrides = load_overrides(root)
    SHARED_DOMAINS.update(overrides["shared_domains"])
    NO_LINK_DOMAINS.update(overrides["no_link_domains"])
    recs, shard_meta = load_shards(root / "shards")
    if not recs:
        print("no shards found", file=sys.stderr)
        return 1
    entries = [merge_group(g) for g in cluster(recs)]
    # A no-link domain stays only on its own masthead (the holder with fewest domains).
    for d in NO_LINK_DOMAINS:
        holders = [e for e in entries if d in e["domains"]]
        if len(holders) > 1:
            keep = min(holders, key=lambda e: len(e["domains"]))
            for e in holders:
                if e is not keep:
                    e["domains"].remove(d)
                    e["_notes"].append(f"merge: {d} moved to the separate entry '{keep['name']}'")
    regions = build_regions(raw / "m49" / "m49.csv")

    # ---- ids: config/sources.yaml ids where the publisher matches, else slugs
    cfg_ids = config_id_map(a.repo)
    used = set()
    for e in sorted(entries, key=lambda e: (-len(e["harvested_from"]), e["name"] or "")):
        cid = next((cfg_ids[d] for d in e["domains"] if d in cfg_ids), None)
        base = cid or slug(e["name"] or (e["domains"][0] if e["domains"] else "unnamed"))
        i, eid = 2, base
        if eid in used and not cid:
            c = (e["location"]["country"] or "").lower()
            eid = f"{base}-{c}" if c else base
        while eid in used:
            eid, i = f"{base}-{i}", i + 1
        used.add(eid)
        e["id"] = eid

    # ---- hand corrections (overrides.yaml and shards/override-*.yaml)
    entries = apply_overrides(entries, overrides)

    # ---- region derivation
    for e in entries:
        c = e["location"]["country"]
        r = regions.get(c) if c else None
        e["location"]["region"] = r["region"] if r else None
        e["location"]["subregion"] = r["subregion"] if r else None

    # ---- bulk joins
    dom = Domains(entries)
    tranco, tranco_key = load_tranco(raw)
    if tranco:
        best: dict[int, tuple[int, str]] = {}
        for d, rank in tranco.items():
            for i in dom.entries_for(d)[0]:
                if i not in best or rank < best[i][0]:
                    best[i] = (rank, d)
        for i, (rank, d) in best.items():
            entries[i]["signals"]["tranco_rank"] = {"value": rank, "source": tranco_key, "domain": d,
                                                    **shared_note(d)}
    crux, crux_key = load_crux(raw)
    if crux:
        best = {}
        for origin, band in crux:
            for i in dom.entries_for(re.sub(r"^https?://", "", origin))[0]:
                if i not in best or band < best[i][0]:
                    best[i] = (band, origin)
        for i, (band, origin) in best.items():
            entries[i]["signals"]["crux_band"] = {"value": band, "source": crux_key, "origin": origin,
                                                  **shared_note(norm_domain(origin))}
    wiki, wiki_key = load_wiki_counts(raw)
    if wiki:
        tot: dict[int, int] = defaultdict(int)
        for host, c in wiki():
            ids, matched = dom.entries_for(host)
            if matched in SHARED_DOMAINS:
                continue  # e.g. all of google.com is not Google News: count only specific hosts
            for i in ids:
                tot[i] += c
        for i, e in enumerate(entries):
            specific = [d for d in e["domains"] if d not in SHARED_DOMAINS]
            if specific:
                e["signals"]["wiki_cites"] = {"value": tot.get(i, 0), "source": wiki_key,
                                              "method": "links summed over all hosts under the entry's "
                                                        "domains, excluding shared domains"}
    online_pop, pop_key = load_online_pop(raw)

    # ---- status
    blocked = blocked_domains(a.repo)
    for e in entries:
        st = {"status": "untested", "feed_url": None, "robots_allows_feed": None,
              "robots_allows_articles": None, "checked": None, "note": ""}
        if any(d in cfg_ids for d in e["domains"]):
            st["status"] = "in-use"
            st["note"] = "config/sources.yaml: " + ", ".join(sorted(cfg_ids.feeds_for(e["domains"])))
        elif any(d in blocked for d in e["domains"]):
            st["status"] = "blocked"
            st["note"] = "docs/blocked-publishers.md: " + blocked[next(d for d in e["domains"] if d in blocked)]
        elif any(d in NOTED for d in e["domains"]):
            st["note"] = NOTED[next(d for d in e["domains"] if d in NOTED)]
        e["our_status"] = st

    # ---- ranked entries
    for e in entries:
        e["ranked"] = e["kind"] != "aggregator" and e["english_edition"] != "none" and not e["_generic"]
        if e["english_edition"] is None:
            e["ranked"] = False  # unknown language: not eligible until classified
    pool_idx = [i for i, e in enumerate(entries) if a.percentile_pool == "all" or e["ranked"]]
    pool_set = set(pool_idx)

    # ---- reach measures (Similarweb and Ipsos iris are recorded but not scored by default:
    # both come only from top-50 lists, so a percentile among listed entries pushes the
    # lower-listed, still very large, sites down. Kept for the UK study.)
    audience, sw, tr, cr, iris = {}, {}, {}, {}, {}
    missing_pop = set()
    for i in pool_idx:
        s = entries[i]["signals"]
        rows = s.get("dnr_reach_online") or []
        aud, ok = 0.0, False
        for row in rows:
            c, v = (row.get("country") or "").upper(), row.get("value")
            if v is None:
                continue
            if c in online_pop:
                aud += v / 100 * online_pop[c][0] * segment_share(c, row.get("segment"))
                ok = True
            else:
                missing_pop.add(c)
        if ok:
            audience[i] = aud
            s["dnr_audience_est"] = {"value": round(aud), "source": pop_key,
                                     "method": "sum over surveyed countries (and language segments, weighted by "
                                               "population share) of % weekly online reach x internet users"}
        vis = [r.get("value") for r in as_list(s.get("similarweb_visits")) if r.get("value")]
        if vis:
            sw[i] = max(vis)
        iv = [r.get("value") for r in as_list(s.get("ipsos_iris_audience")) if r.get("value")]
        if iv:
            iris[i] = max(iv)
        if (s.get("tranco_rank") or {}).get("value"):
            tr[i] = s["tranco_rank"]["value"]
        if (s.get("crux_band") or {}).get("value"):
            cr[i] = s["crux_band"]["value"]
    reach_parts = [percentiles({k: math.log10(v + 1) for k, v in audience.items()}),
                   percentiles({k: math.log10(v) for k, v in tr.items()}, higher_better=False),
                   percentiles({k: math.log10(v) for k, v in cr.items()}, higher_better=False)]
    if a.use_similarweb:
        reach_parts.append(percentiles({k: math.log10(v + 1) for k, v in sw.items()}))
    if a.use_iris:
        reach_parts.append(percentiles({k: math.log10(v + 1) for k, v in iris.items()}))

    # ---- trust: two parts kept separate, then averaged where both exist.
    # trust_editors: Wikipedia general-news rating mapped as in the protocol.
    # trust_audience: DNR "% trust among those aware", as a percentile among the brands DNR
    # lists for the same country (and language segment), averaged across countries. Raw DNR
    # levels differ by market (polarised markets score every brand low), so they aren't
    # comparable across countries; the raw rows stay in signals.dnr_trust.
    by_market: dict[tuple, dict[int, float]] = defaultdict(dict)
    for i in pool_idx:
        for r in entries[i]["signals"].get("dnr_trust") or []:
            if r.get("value") is not None:
                by_market[((r.get("country") or "").upper(), r.get("segment"))][i] = float(r["value"])
    aud_rows: dict[int, list[float]] = defaultdict(list)
    for vals in by_market.values():
        for i, p in percentiles(vals).items():
            aud_rows[i].append(p)
    trust_ed, trust_aud, trust = {}, {}, {}
    for i in pool_idx:
        rv = (entries[i]["signals"].get("rsp") or {}).get("value")
        if rv in RSP_SCORE:
            trust_ed[i] = RSP_SCORE[rv]
        if aud_rows.get(i):
            trust_aud[i] = statistics.mean(aud_rows[i])
        parts = [d[i] for d in (trust_ed, trust_aud) if i in d]
        if parts:
            trust[i] = statistics.mean(parts)

    # ---- authority: Wikipedia citations, plus GDELT "cited by" (distinct outlet domains
    # linking to the entry, or for wires naming it) when raw/gdelt/ exists.
    gd = load_gdelt(raw)
    if gd:
        join_gdelt(entries, gd)
    wc = {i: entries[i]["signals"]["wiki_cites"]["value"] for i in pool_idx
          if (entries[i]["signals"].get("wiki_cites") or {}).get("value") is not None}
    # Wires: Wikipedia credits them in citations' agency= field rather than linking their
    # own domain. Articles crediting the wire x links per citing article (median over large
    # news domains) gives a link-equivalent; the larger of that and its own links is used.
    agency, lpp, agency_key = load_wiki_agency(raw)
    for i in pool_idx:
        e = entries[i]
        if e["kind"] != "wire" or e["id"] not in agency:
            continue
        arts = agency[e["id"]]
        equiv = round(arts * lpp)
        e["signals"]["wiki_agency"] = {
            "value": arts, "source": agency_key, "link_equivalent": equiv,
            "method": f"English Wikipedia articles crediting the wire in agency= (name variants, "
                      f"inclusion-exclusion); link-equivalent = articles x {lpp:.2f} links per citing article"}
        if equiv > wc.get(i, 0):
            wc[i] = equiv
    gc = {i: entries[i]["signals"]["gdelt_inbound"]["value"] for i in pool_idx
          if (entries[i]["signals"].get("gdelt_inbound") or {}).get("value") is not None}
    auth_parts = [percentiles({k: math.log10(v + 1) for k, v in wc.items()}),
                  percentiles({k: math.log10(v + 1) for k, v in gc.items()})]
    auth = {}
    for i in pool_idx:
        ps = [p[i] for p in auth_parts if i in p]
        if ps:
            auth[i] = statistics.mean(ps)

    reach = {}
    for i in pool_idx:
        ps = [p[i] for p in reach_parts if i in p]
        if ps:
            reach[i] = statistics.mean(ps)
    med = {k: (statistics.median(v.values()) if v else 0.5)
           for k, v in (("reach", reach), ("trust", trust), ("authority", auth))}
    wsum = a.w_reach + a.w_trust + a.w_authority
    for i, e in enumerate(entries):
        sc = {"reach": reach.get(i), "trust": trust.get(i), "authority": auth.get(i)}
        n = sum(v is not None for v in sc.values())
        if i in pool_set:
            comb = (a.w_reach * (sc["reach"] if sc["reach"] is not None else med["reach"])
                    + a.w_trust * (sc["trust"] if sc["trust"] is not None else med["trust"])
                    + a.w_authority * (sc["authority"] if sc["authority"] is not None else med["authority"])) / wsum
        else:
            comb = None
        e["scores"] = {k: (round(v, 4) if v is not None else None) for k, v in sc.items()}
        e["scores"]["trust_editors"] = trust_ed.get(i)
        e["scores"]["trust_audience"] = round(trust_aud[i], 4) if i in trust_aud else None
        e["scores"]["combined"] = round(comb, 4) if comb is not None else None
        e["scores"]["signals_available"] = n

    # ---- rankings: news + specialist outlets in the main ranking; soft-topic outlets
    # (entertainment, sport, lifestyle, gaming, culture) ranked separately in soft_rank.
    for e in entries:
        e["topic_group"] = topic_group(e.get("topics"))
    key = lambda e: (-e["scores"]["combined"], -e["scores"]["signals_available"], e["id"])
    eligible = [e for e in entries if e["ranked"] and e["scores"]["combined"] is not None]
    ranked = sorted([e for e in eligible if e["topic_group"] != "soft"], key=key)
    soft = sorted([e for e in eligible if e["topic_group"] == "soft"], key=key)
    for r, e in enumerate(ranked, 1):
        e["rank"] = r
    for r, e in enumerate(soft, 1):
        e["soft_rank"] = r

    # ---- write
    out_entries = [to_entry(e) for e in sorted(
        entries, key=lambda e: (e.get("rank") or 10**6, e.get("soft_rank") or 10**6, e["id"]))]
    cat = {
        "meta": {
            "protocol": "docs/research/news-source-catalogue.md (news-bot repo)",
            "built": today,
            "ranking_date": today,
            "weights": {"reach": a.w_reach, "trust": a.w_trust, "authority": a.w_authority},
            "percentile_pool": a.percentile_pool,
            "use_iris": a.use_iris,
            "use_similarweb": a.use_similarweb,
            "trust_method": "mean of trust_editors (Wikipedia rating) and trust_audience (DNR trust as "
                            "within-market percentile, averaged across markets), whichever exist",
            "authority_method": "mean of percentiles of wiki_cites (for wires, the larger of own-domain links "
                                "and the agency= link-equivalent) and gdelt_inbound, whichever exist",
            "soft_topics": sorted(SOFT_TOPICS),
            "similarweb_method": "max across Press Gazette markets (US list is US-only traffic, global-en is worldwide)",
            "tranco_crux_method": "best (lowest) rank across the entry's domains; ranks can't be added",
            "medians_used_for_missing": {k: round(v, 4) for k, v in med.items()},
            "pool_size": len(entries),
            "ranked": len(ranked),
            "ranked_soft": len(soft),
            "bulk_sources": {"tranco": tranco_key, "crux": crux_key, "wiki_cites": wiki_key,
                             "online_pop": pop_key, "gdelt": gd["key"] if gd else None, "mediacloud": None},
            "countries_without_online_pop": sorted(missing_pop),
            "shards": shard_meta,
        },
        "regions": {k: v for k, v in sorted(regions.items())},
        "entries": out_entries,
    }
    (root / "catalogue.yaml").write_text(
        yaml.safe_dump(cat, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    cols = ["rank", "id", "name", "country", "region", "reach", "trust", "authority", "combined",
            "signals_available", "our_status", "trust_editors", "trust_audience", "topic_group",
            "kind", "added_by"]
    for fname, rows, rk in (("ranked.csv", ranked, "rank"), ("ranked-soft.csv", soft, "soft_rank")):
        with (root / fname).open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for e in rows:
                s = e["scores"]
                w.writerow([e[rk], e["id"], e["name"], e["location"]["country"], e["location"]["region"],
                            s["reach"], s["trust"], s["authority"], s["combined"], s["signals_available"],
                            e["our_status"]["status"], s["trust_editors"], s["trust_audience"],
                            e["topic_group"], e["kind"], e["added_by"]])
    print(f"pool {len(entries)}, ranked {len(ranked)} (+{len(soft)} soft), "
          f"unranked {len(entries) - len(ranked) - len(soft)}")
    print(f"wrote catalogue.yaml, ranked.csv and ranked-soft.csv in {root}")
    return 0


def to_entry(e: dict) -> dict:
    flags = []
    rv = (e["signals"].get("rsp") or {}).get("value")
    if rv in ("deprecated", "blacklisted"):
        flags.append("rsp-deprecated")
    sig = {k: e["signals"].get(k) for k in SIGNAL_KEYS}
    for k in SIGNAL_KEYS:
        if sig[k] is None:
            sig[k] = [] if k in LIST_SIGNALS else {"value": None, "source": None}
    for k in ("gdelt_volume", "gdelt_mentions", "wiki_agency"):
        if k in e["signals"]:
            sig[k] = e["signals"][k]
    if "dnr_audience_est" in e["signals"]:
        sig["dnr_audience_est"] = e["signals"]["dnr_audience_est"]
    if "_conflicts" in e["signals"]:
        sig["_conflicts"] = e["signals"]["_conflicts"]
    note = "; ".join(e["_notes"])
    if e["_generic"]:
        note = ("Generic DNR category, not a single publisher; kept but not ranked. " + note).strip()
    return {
        "id": e["id"], "name": e["name"], "aliases": e["aliases"], "domains": e["domains"],
        "kind": e["kind"], "languages": e["languages"], "english_edition": e["english_edition"],
        "location": e["location"], "topics": e["topics"], "signals": sig,
        "topic_group": e.get("topic_group"), "scores": e["scores"], "rank": e.get("rank"),
        "soft_rank": e.get("soft_rank"), "ranked": e["ranked"], "flags": flags,
        "detail": "pool", "added_by": e["added_by"], "harvested_from": e["harvested_from"],
        "our_status": e["our_status"], "note": note,
    }


# --------------------------------------------------------------------------- repo lookups

# config/sources.yaml feed ids -> publisher domain. Section feeds (guardian-world,
# guardian-longread, bbc-future) map to their publisher, but only publisher-named ids
# become catalogue ids; the Guardian gets the slug "guardian".
CONFIG_PUBLISHERS = {
    "bbc-news": "bbc.co.uk", "bbc-future": "bbc.com", "guardian-world": "theguardian.com",
    "guardian-longread": "theguardian.com", "independent-uk": "independent.co.uk",
    "inews": "inews.co.uk", "dw-news": "dw.com", "business-insider": "businessinsider.com",
    "ars-technica": "arstechnica.com", "the-verge": "theverge.com", "wired": "wired.com",
    "lwn": "lwn.net", "404media": "404media.co", "mit-tech-review": "technologyreview.com",
    "nature-news": "nature.com", "quanta": "quantamagazine.org", "phys-org": "phys.org",
    "carbon-brief": "carbonbrief.org", "aeon": "aeon.co", "new-yorker": "newyorker.com",
    "simon-willison": "simonwillison.net", "julia-evans": "jvns.ca",
    "martin-fowler": "martinfowler.com", "cloudflare-blog": "blog.cloudflare.com",
    "noahpinion": "noahpinion.blog",
}
SECTION_IDS = {"bbc-future", "guardian-world", "guardian-longread"}


class ConfigIds(dict):
    def __init__(self, mapping, feeds):
        super().__init__(mapping)
        self.feeds = feeds

    def feeds_for(self, domains):
        return {fid for fid, d in self.feeds.items() if d in domains}


def config_id_map(repo: Path) -> ConfigIds:
    cfg = yaml.safe_load((repo / "config/sources.yaml").read_text(encoding="utf-8"))
    present = {s["id"] for s in cfg["sources"] if s.get("enabled", True)}
    feeds = {fid: d for fid, d in CONFIG_PUBLISHERS.items() if fid in present}
    unknown = {s["id"] for s in cfg["sources"] if s.get("type") == "rss"} - set(CONFIG_PUBLISHERS)
    if unknown:
        print(f"warning: config sources without a publisher mapping: {sorted(unknown)}", file=sys.stderr)
    m = {}
    for fid, d in feeds.items():
        if fid in SECTION_IDS:
            m.setdefault(d, "guardian" if d == "theguardian.com" else None)
        else:
            m[d] = fid
    m["bbc.com"] = m["bbc.co.uk"] = "bbc-news"
    return ConfigIds({k: v for k, v in m.items() if v}, feeds)


BLOCKED_DOMAINS = {  # docs/blocked-publishers.md "Blocked" table, publisher -> domains
    "The Register": ["theregister.com", "theregister.co.uk"],
    "New Scientist": ["newscientist.com"],
    "Rest of World": ["restofworld.org"],
    "Sky News": ["news.sky.com", "skynews.com"],
    "The Atlantic": ["theatlantic.com"],
    "The Economist": ["economist.com"],
    "Financial Times": ["ft.com"],
    "New York Times": ["nytimes.com"],
    "Bloomberg": ["bloomberg.com"],
    "The Information": ["theinformation.com"],
    "Medium-hosted blogs": ["medium.com"],
    "The Telegraph": ["telegraph.co.uk"],
    "Wall Street Journal": ["wsj.com"],
}
NOTED = {
    "aljazeera.com": "docs/blocked-publishers.md lists it as recoverable, needs a decision (robots.txt and stated terms)",
    "aljazeera.net": "docs/blocked-publishers.md lists aljazeera.com as recoverable, needs a decision",
    "cnn.com": "docs/blocked-publishers.md: not added (stale world feed, pages over 5 MB cap)",
    "washingtonpost.com": "docs/blocked-publishers.md: not added (HTTP 502 on feed, 2026-10-17)",
    "spectator.co.uk": "docs/blocked-publishers.md: not added (feed URL 404)",
}


def blocked_domains(repo: Path) -> dict[str, str]:
    text = (repo / "docs/blocked-publishers.md").read_text(encoding="utf-8")
    sec = text.split("## Blocked", 1)[1].split("\n## ", 1)[0]
    out = {}
    for line in sec.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0] in BLOCKED_DOMAINS:
            for d in BLOCKED_DOMAINS[cells[0]]:
                out[d] = f"{cells[0]}: {cells[4]}"
    missing = set(BLOCKED_DOMAINS) - {v.split(':')[0] for v in out.values()}
    if missing:
        print(f"warning: blocked publishers not found in table: {sorted(missing)}", file=sys.stderr)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
