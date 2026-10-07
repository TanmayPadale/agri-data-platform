-- Hourly soil moisture per sensor. Grain: one row per sensor per UTC hour.
--
-- Incremental: after the first full build, each run only reprocesses recent hours
-- instead of every reading ever received. The lookback window (3 hours before the
-- newest hour already in the table) catches readings that arrive late: their hour
-- is deleted and rebuilt from all of its readings (delete+insert on unique_key).
-- A reading later than the window needs `dbt build --full-refresh`.

{{
    config(
        materialized='incremental',
        unique_key=['sensor_id', 'hour'],
        incremental_strategy='delete+insert'
    )
}}

select
    sensor_id,
    date_trunc('hour', reading_ts)      as hour,
    round(avg(soil_moisture_pct), 2)    as avg_moisture_pct,
    min(soil_moisture_pct)              as min_moisture_pct,
    max(soil_moisture_pct)              as max_moisture_pct,
    count(*)                            as readings,
    max(loaded_at)                      as last_loaded_at
from {{ ref('stg_sensor_readings') }}

{% if is_incremental() %}
-- {{ this }} is this model's own table, as it was before this run.
where reading_ts >= (select max(hour) - interval '3 hours' from {{ this }})
{% endif %}

group by 1, 2
