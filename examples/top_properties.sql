-- The stress test: two CTEs, window functions, a scalar subquery in the
-- SELECT, a function-wrapped filter, a DISTINCT aggregate, and an outer
-- GROUP BY + HAVING stacked on an already-grouped grain.
-- Top 3 properties per month by rent, with month-over-month growth.
WITH monthly AS (
    SELECT
        f.property_key,
        d.year_month,
        SUM(f.rent_amount) AS rent,
        COUNT(DISTINCT f.lease_id) AS leases
    FROM fact_leases f
    JOIN dim_date d ON f.date_key = d.date_key
    WHERE YEAR(d.full_date) = 2026
    GROUP BY f.property_key, d.year_month
),
ranked AS (
    SELECT
        m.*,
        RANK() OVER (PARTITION BY m.year_month ORDER BY m.rent DESC) AS rent_rank,
        LAG(m.rent) OVER (PARTITION BY m.property_key ORDER BY m.year_month) AS prev_rent
    FROM monthly m
)
SELECT
    p.property_name,
    r.year_month,
    r.rent,
    r.prev_rent,
    CASE
        WHEN r.prev_rent IS NULL THEN NULL
        ELSE (r.rent - r.prev_rent) / r.prev_rent
    END AS rent_growth,
    (SELECT MAX(rent) FROM ranked) AS best_month_rent
FROM ranked r
JOIN dim_property p ON r.property_key = p.property_key
WHERE r.rent_rank <= 3
GROUP BY p.property_name, r.year_month, r.rent, r.prev_rent
HAVING SUM(r.leases) > 10
ORDER BY r.year_month, r.rent DESC;
