-- 1. Rolling 7-day rainfall per farm (window function: running total over neighbours).
--
-- GROUP BY would collapse each farm into one row. A window keeps every day and adds
-- a column computed from the days around it.
--
-- Two frames are shown on purpose:
--   ROWS  BETWEEN 6 PRECEDING ...  counts 7 physical rows. If a day is missing, the
--                                  "7-day" total quietly reaches back 8 or more days.
--   RANGE BETWEEN '6 days' ...     counts by the value of `day`, so it is always the
--                                  last 7 calendar days, gaps or not.
SELECT
    location_id,
    day,
    rain_mm,
    SUM(rain_mm) OVER (
        PARTITION BY location_id
        ORDER BY day
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS rain_7_rows_mm,
    SUM(rain_mm) OVER (
        PARTITION BY location_id
        ORDER BY day
        RANGE BETWEEN INTERVAL '6 days' PRECEDING AND CURRENT ROW
    ) AS rain_7_days_mm
FROM raw.weather_daily
ORDER BY location_id, day;
