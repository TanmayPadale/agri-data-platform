-- SLI 1 (batch freshness): the share of days whose weather landed on time.
-- SLO: on 99% of days over 30 days, BOTH farms' rows for day D are first loaded by
-- 01:00 UTC on D+1 (the daily run starts at 00:00 UTC). Error budget: about 0.3 days.
--
-- first_loaded_at, not loaded_at: an upsert rewrites loaded_at every time a day is
-- reloaded (a backfill, a retry), so only the first arrival says whether it was on time.
-- Days that were backfilled weeks later count as misses, which is the truth.
WITH days AS (
    SELECT
        day,
        count(*)               AS farms_loaded,
        max(first_loaded_at)   AS last_farm_first_loaded_at
    FROM raw.weather_daily
    WHERE day >= current_date - 30 AND day < current_date
    GROUP BY day
)
SELECT
    count(*)                                                       AS days_measured,
    count(*) FILTER (
        WHERE farms_loaded = 2
          AND last_farm_first_loaded_at <= (day + 1)::timestamp AT TIME ZONE 'UTC' + interval '1 hour'
    )                                                              AS days_on_time,
    round(100.0 * count(*) FILTER (
        WHERE farms_loaded = 2
          AND last_farm_first_loaded_at <= (day + 1)::timestamp AT TIME ZONE 'UTC' + interval '1 hour'
    ) / nullif(count(*), 0), 2)                                    AS sli_pct,
    99.0                                                           AS slo_pct
FROM days;
