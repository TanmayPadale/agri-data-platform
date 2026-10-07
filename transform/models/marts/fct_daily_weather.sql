-- Daily weather fact. Grain: one row per location per local day.
--
-- rain_3d_mm uses a RANGE frame over dates, so it is always the last 3 calendar
-- days. A ROWS frame would count rows and quietly stretch over a missing day.

with weather as (

    select * from {{ ref('stg_weather') }}

)

select
    w.location_id,
    w.weather_date,
    d.month_start,
    l.location_name,
    l.region,
    w.temp_max_c,
    w.temp_min_c,
    w.rain_mm,
    w.et0_mm,
    sum(w.rain_mm) over (
        partition by w.location_id
        order by w.weather_date
        range between interval '2 days' preceding and current row
    )                       as rain_3d_mm,
    w.weather_source,
    w.loaded_at
from weather as w
left join {{ ref('dim_date') }} as d
    on d.date_day = w.weather_date
left join {{ ref('dim_location') }} as l
    on l.location_id = w.location_id
