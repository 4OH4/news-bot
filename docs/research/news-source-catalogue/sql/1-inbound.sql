-- Inbound links: for each linked registrable domain and host, how many links and how many
-- distinct GDELT source domains link to it (from PAGE_LINKS in article bodies), 12 months,
-- self-links excluded. Columns read: SourceCommonName, SourceCollectionIdentifier, Extras.
WITH art AS (
  SELECT LOWER(SourceCommonName) AS src,
         REGEXP_EXTRACT(Extras, r'<PAGE_LINKS>(.*?)</PAGE_LINKS>') AS links
  FROM `gdelt-bq.gdeltv2.gkg_partitioned`
  WHERE _PARTITIONTIME >= TIMESTAMP('2025-11-01') AND _PARTITIONTIME < TIMESTAMP('2026-11-01')
    AND SourceCollectionIdentifier = 1
    AND Extras LIKE '%<PAGE_LINKS>%'
),
l AS (
  SELECT src, LOWER(NET.REG_DOMAIN(link)) AS domain, LOWER(NET.HOST(link)) AS host
  FROM art CROSS JOIN UNNEST(SPLIT(links, ';')) AS link
  WHERE link != ''
)
SELECT IF(GROUPING(host) = 1, 'domain', 'host') AS level,
       IF(GROUPING(host) = 1, domain, host) AS target,
       COUNT(*) AS links,
       COUNT(DISTINCT src) AS linking_domains
FROM l
WHERE domain IS NOT NULL AND domain != src
GROUP BY GROUPING SETS ((domain), (domain, host))
HAVING COUNT(DISTINCT src) >= 3
