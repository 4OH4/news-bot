#!/usr/bin/env bash
# Count English Wikipedia external links per host from the externallinks SQL dump.
# Output: raw/wiki-cites/host-counts-<date>.tsv.gz  (count<TAB>host, host in normal order, scheme dropped)
# Counts are links (rows), not distinct pages; http and https are combined.
set -euo pipefail
R="${NEWSFEED_RESEARCH_DIR:-$HOME/.newsfeed/research/news-sources}"
DATE="${1:-20261101}"
IN="$R/raw/wiki-cites/enwiki-$DATE-externallinks.sql.gz"
OUT="$R/raw/wiki-cites/host-counts-$DATE.tsv.gz"
DEC=$(command -v pigz >/dev/null && echo "pigz -dc" || echo "zcat")
$DEC "$IN" \
 | grep -aoE "\([0-9]+,[0-9]+,'[a-z]*:?//[^']*'" \
 | sed -E "s/^\([0-9]+,[0-9]+,'[a-z]*:?\/\///; s/'$//; s/\.$//" \
 | LC_ALL=C awk '{c[$0]++} END{for(h in c){n=split(h,a,".");s=a[n];for(i=n-1;i>=1;i--)s=s"."a[i];print c[h]"\t"s}}' \
 | LC_ALL=C sort -t$'\t' -k1,1nr | gzip > "$OUT"
echo "wrote $OUT"; zcat "$OUT" | head -20; echo "hosts: $(zcat "$OUT" | wc -l)"
