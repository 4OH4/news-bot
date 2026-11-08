-- Is the partitioned GKG table still being updated? Scans only DATE for ~9 days.
SELECT MIN(DATE) AS first_date, MAX(DATE) AS last_date, COUNT(*) AS records
FROM `gdelt-bq.gdeltv2.gkg_partitioned`
WHERE _PARTITIONTIME >= TIMESTAMP('2026-10-31')
