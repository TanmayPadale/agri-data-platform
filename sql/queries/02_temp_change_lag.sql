-- 2. Day-over-day change in maximum temperature (LAG looks one row back).
--
-- PARTITION BY location_id restarts the window for each farm, so Griffith's first
-- day is never compared with Jawali's last day. The first day of each farm has no
-- previous row, so LAG returns NULL and so does the difference.
SELECT
    location_id,
    day,
    t_max_c,
    LAG(t_max_c) OVER (PARTITION BY location_id ORDER BY day)            AS prev_t_max_c,
    t_max_c - LAG(t_max_c) OVER (PARTITION BY location_id ORDER BY day)  AS t_max_change_c
FROM raw.weather_daily
ORDER BY location_id, day;
