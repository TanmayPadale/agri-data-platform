-- 3. Top 3 hottest days per farm per month (RANK inside a CTE).
--
-- Why the CTE: SQL evaluates WHERE before SELECT, so a window result such as
-- `heat_rank` cannot be filtered in the same query that computes it. Compute it in
-- a CTE (or subquery), then filter outside.
--
-- RANK gives ties the same number and skips the next (1, 1, 3), so a month can
-- return more than 3 rows when days tie. DENSE_RANK would give 1, 1, 2 and
-- ROW_NUMBER would break ties arbitrarily (1, 2, 3).
WITH ranked AS (
    SELECT
        location_id,
        date_trunc('month', day)::date AS month,
        day,
        t_max_c,
        RANK() OVER (
            PARTITION BY location_id, date_trunc('month', day)
            ORDER BY t_max_c DESC
        ) AS heat_rank
    FROM raw.weather_daily
)
SELECT location_id, month, heat_rank, day, t_max_c
FROM ranked
WHERE heat_rank <= 3
ORDER BY location_id, month, heat_rank, day;
