-- Organisation mentions: how many articles and distinct source domains name each
-- organisation (GKG Organizations field, de-duplicated per article), 12 months.
-- Used to credit wire services, whose copy runs on client sites rather than their own.
SELECT org, COUNT(*) AS articles, COUNT(DISTINCT LOWER(SourceCommonName)) AS domains
FROM `gdelt-bq.gdeltv2.gkg_partitioned` CROSS JOIN UNNEST(SPLIT(Organizations, ';')) AS org
WHERE _PARTITIONTIME >= TIMESTAMP('2025-11-01') AND _PARTITIONTIME < TIMESTAMP('2026-11-01')
  AND SourceCollectionIdentifier = 1
  AND Organizations != ''
GROUP BY org
HAVING COUNT(DISTINCT LOWER(SourceCommonName)) >= 20
