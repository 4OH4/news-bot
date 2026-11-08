-- Output volume: articles per source domain, 12 months (recorded, not scored).
SELECT LOWER(SourceCommonName) AS domain, COUNT(*) AS articles
FROM `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE _PARTITIONTIME >= TIMESTAMP('2025-11-01') AND _PARTITIONTIME < TIMESTAMP('2026-11-01')
  AND SourceCollectionIdentifier = 1
GROUP BY domain
HAVING COUNT(*) >= 50
