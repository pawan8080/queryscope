-- A query that looks harmless and is not: wildcard select, a function
-- wrapped around the filter column, and a sort with no limit.
SELECT *
FROM web_events e
JOIN users u ON e.user_id = u.id
WHERE YEAR(e.created_at) = 2026
ORDER BY e.created_at DESC;
