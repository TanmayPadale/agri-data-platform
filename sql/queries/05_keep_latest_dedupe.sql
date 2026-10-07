-- 5. Keep only the latest row per key (ROW_NUMBER dedupe).
--
-- The pattern: number the rows inside each key from newest to oldest, keep rn = 1.
-- Here it answers "what is the most recent day we hold for each farm, and when was
-- it loaded?". On Day 2 the same shape keeps the latest reading per sensor from an
-- at-least-once stream, and the dbt staging models use it to drop duplicates.
--
-- ORDER BY ... LIMIT 1 would return one row for the whole table, not one per farm.
WITH numbered AS (
    SELECT
        w.*,
        ROW_NUMBER() OVER (PARTITION BY location_id ORDER BY day DESC, loaded_at DESC) AS rn
    FROM raw.weather_daily w
)
SELECT location_id, day AS latest_day, t_max_c, rain_mm, source, loaded_at
FROM numbered
WHERE rn = 1
ORDER BY location_id;
