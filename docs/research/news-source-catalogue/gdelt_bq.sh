#!/usr/bin/env bash
# Run the GDELT queries for the news-source catalogue in YOUR BigQuery project and save
# the results to raw/gdelt/. Run it as yourself (it uses your gcloud login).
#
#   bash docs/research/news-source-catalogue/gdelt_bq.sh <project-id>
#
# Use a BigQuery sandbox project (no billing account linked), so nothing can be charged.
# Each query is dry-run first; the script shows the bytes it would scan and asks before
# running it. Every query also has a hard cap on bytes billed.
set -euo pipefail
PROJECT="${1:?usage: gdelt_bq.sh <project-id>}"
R="${NEWSFEED_RESEARCH_DIR:-$HOME/.newsfeed/research/news-sources}"
SQL="$(cd "$(dirname "$0")" && pwd)/sql"
OUT="$R/raw/gdelt"
CAP=200000000000   # 200 GB per query, hard limit (~$1.25 at on-demand rates; $0 in a sandbox)
mkdir -p "$OUT"
command -v bq >/dev/null || { echo "bq not found: it comes with the Google Cloud SDK (gcloud components install bq)"; exit 1; }

billing=$(gcloud billing projects describe "$PROJECT" --format='value(billingEnabled)' 2>/dev/null || echo "unknown")
echo "Project: $PROJECT (billing enabled: ${billing:-unknown})"
[ "$billing" = "True" ] && echo "WARNING: billing is enabled on this project. Queries above the 1 TiB monthly free tier would be charged (capped at 200 GB per query)."

total=0
for name in 0-freshness 1-inbound 2-orgs 3-volume; do
  f="$SQL/$name.sql"
  bytes=$(bq --project_id="$PROJECT" query --use_legacy_sql=false --dry_run --format=json < "$f" \
          | python3 -c '
import json, re, sys
t = sys.stdin.read()
try:
    print(int(json.loads(t)["statistics"]["totalBytesProcessed"]))
except Exception:
    m = re.search(r"process (\d+) bytes", t)
    if not m:
        sys.exit("could not read the dry-run byte count:\n" + t)
    print(m.group(1))')
  total=$((total + bytes))
  printf '%-12s would scan %8.2f GB\n' "$name" "$(echo "$bytes/1000000000" | bc -l)"
done
printf 'Total: %.2f GB (free tier: 1,000+ GB per month)\n' "$(echo "$total/1000000000" | bc -l)"
read -r -p "Run all four queries now? [y/N] " ok
[ "$ok" = "y" ] || { echo "Stopped; nothing was run."; exit 0; }

for name in 0-freshness 1-inbound 2-orgs 3-volume; do
  out="$OUT/${name#*-}.csv"
  echo "Running $name -> $out"
  bq --project_id="$PROJECT" query --use_legacy_sql=false --maximum_bytes_billed=$CAP \
     --format=csv --max_rows=5000000 < "$SQL/$name.sql" > "$out"
  echo "  $(($(wc -l < "$out") - 1)) rows"
done
cat > "$OUT/meta.yaml" <<META
source_key: gdelt-2025-11-01_2026-10-31
table: gdelt-bq.gdeltv2.gkg_partitioned
window: "[2025-11-01, 2026-11-01)"
retrieved: $(date -u +%Y-%m-%d)
project_kind: ${billing:-unknown} billing
META
echo "Done. Freshness check:"; cat "$OUT/freshness.csv"
echo "Tell Claude the queries have finished; it will merge and re-rank."
