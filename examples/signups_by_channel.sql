-- The simple case: one table, one filter, one grouping.
-- QueryScope should stay quiet here; not every query has a problem.
SELECT
    channel,
    COUNT(*) AS signups
FROM signups
WHERE country = 'CA'
GROUP BY channel
ORDER BY signups DESC
LIMIT 10;
