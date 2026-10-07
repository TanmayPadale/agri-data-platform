-- The answer: should this field be irrigated on this day?
-- Grain: one row per field per local day.
--
-- Rule: irrigate when the 3-day average soil moisture is below
-- var('moisture_threshold_pct') AND that day's rain is below var('rain_threshold_mm').
--
-- Three decisions keep it honest:
--   * Every field gets a row for every day (a date spine), so "3-day" means three
--     calendar days even when a day has no readings.
--   * Weather is joined on the field's own farm (location_id), not one farm for all.
--   * A missing weather row or missing readings make irrigate NULL ("unknown"),
--     never FALSE or TRUE. Treating missing rain as 0 mm would water fields on rain
--     we have not seen yet.

{% set moisture_threshold = var('moisture_threshold_pct') %}
{% set rain_threshold = var('rain_threshold_mm') %}

with moisture as (

    select * from {{ ref('int_field_daily_moisture') }}

),

spine as (

    select
        f.field_id,
        f.field_name,
        f.crop,
        f.location_id,
        d.date_day as day
    from {{ ref('dim_field') }} as f
    cross join {{ ref('dim_date') }} as d
    where d.date_day between (select min(day) from moisture) and (select max(day) from moisture)

),

daily as (

    select
        s.*,
        m.moisture_pct,
        m.readings
    from spine as s
    left join moisture as m
        on  m.field_id = s.field_id
        and m.day = s.day

),

rolled as (

    select
        daily.*,
        round(avg(moisture_pct) over three_days, 2)  as moisture_3d_avg_pct,
        count(moisture_pct) over three_days          as moisture_days_in_window
    from daily
    window three_days as (
        partition by field_id
        order by day
        range between interval '2 days' preceding and current row
    )

),

decided as (

    select
        r.field_id,
        r.field_name,
        r.crop,
        r.location_id,
        r.day,
        r.moisture_pct,
        r.moisture_3d_avg_pct,
        r.moisture_days_in_window,
        r.readings,
        w.rain_mm,
        w.temp_max_c,
        w.et0_mm,
        {{ moisture_threshold }}::numeric  as moisture_threshold_pct,
        {{ rain_threshold }}::numeric      as rain_threshold_mm,
        case
            when r.moisture_3d_avg_pct is null or w.rain_mm is null then null
            when r.moisture_3d_avg_pct < {{ moisture_threshold }}
             and w.rain_mm < {{ rain_threshold }} then true
            else false
        end                                as irrigate
    from rolled as r
    left join {{ ref('fct_daily_weather') }} as w
        on  w.location_id = r.location_id
        and w.weather_date = r.day

)

select
    *,
    case
        when moisture_3d_avg_pct is null then 'unknown: no soil readings in the last 3 days'
        when rain_mm is null then 'unknown: no weather for this day yet'
        when irrigate then format(
            '3-day moisture %s%% is below %s%% and rain %s mm is below %s mm',
            moisture_3d_avg_pct, moisture_threshold_pct, rain_mm, rain_threshold_mm)
        when moisture_3d_avg_pct >= moisture_threshold_pct then format(
            '3-day moisture %s%% is at or above %s%%', moisture_3d_avg_pct, moisture_threshold_pct)
        else format('%s mm of rain is enough (%s mm or more)', rain_mm, rain_threshold_mm)
    end as reason
from decided
