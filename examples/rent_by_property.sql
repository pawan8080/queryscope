-- Total rent and active leases by property and month.
-- The flagship example: joins first, filter after, and a COUNT(DISTINCT)
-- hiding in the SELECT that most people never cost out.
SELECT
    p.property_name,
    d.year_month,
    SUM(f.rent_amount) AS total_rent,
    COUNT(DISTINCT f.lease_id) AS active_leases
FROM fact_leases f
JOIN dim_property p ON f.property_key = p.property_key
JOIN dim_date d ON f.date_key = d.date_key
WHERE d.year = 2026
  AND f.lease_status = 'Active'
GROUP BY p.property_name, d.year_month
ORDER BY total_rent DESC;
