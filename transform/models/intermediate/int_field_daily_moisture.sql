-- Daily soil moisture per field, on the farm's local calendar.
-- Grain: one row per field per local day that has readings.
--
-- Two things happen here that are easy to get wrong:
--   1. Each hourly reading is credited to the field its sensor was on AT THAT HOUR
--      (the validity window from the snapshot), not the field it is on today.
--   2. Hours are converted to the farm's local date before grouping. Weather days
--      are local days, so moisture days must be too, or the join is off by one.

with hourly as (

    select * from {{ ref('fct_sensor_hourly') }}

),

placements as (

    select * from {{ ref('int_sensor_field_history') }}

),

locations as (

    select * from {{ ref('dim_location') }}

)

select
    p.field_id,
    p.location_id,
    (h.hour at time zone l.timezone)::date   as day,
    round(avg(h.avg_moisture_pct), 2)        as moisture_pct,
    sum(h.readings)                          as readings,
    count(distinct h.sensor_id)              as sensors_reporting
from hourly as h
join placements as p
    on  p.sensor_id = h.sensor_id
    and h.hour >= p.valid_from
    and h.hour <  p.valid_to
join locations as l
    on l.location_id = p.location_id
group by 1, 2, 3
