-- 4. Days with no weather row (generate_series + LEFT JOIN anti-join).
--
-- You cannot find missing rows by looking at the rows you have. So build the full
-- calendar each farm should have, LEFT JOIN what actually arrived, and keep the
-- calendar days where the join found nothing.
WITH bounds AS (
    SELECT location_id, MIN(day) AS first_day, MAX(day) AS last_day
    FROM raw.weather_daily
    GROUP BY location_id
),
calendar AS (
    SELECT b.location_id, g::date AS day
    FROM bounds b
    CROSS JOIN LATERAL generate_series(b.first_day, b.last_day, INTERVAL '1 day') AS g
)
SELECT c.location_id, c.day AS missing_day
FROM calendar c
LEFT JOIN raw.weather_daily w
       ON w.location_id = c.location_id
      AND w.day = c.day
WHERE w.day IS NULL
ORDER BY c.location_id, c.day;
