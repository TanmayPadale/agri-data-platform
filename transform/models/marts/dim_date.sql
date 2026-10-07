-- The calendar. Grain: one row per day, from var('calendar_start') to 16 days ahead
-- (the forecast horizon). Facts join to it, and the irrigation signal uses it to
-- give every field a row for every day, readings or not.

with days as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ var('calendar_start') ~ "' as date)",
        end_date="current_date + 17"
    ) }}

)

select
    date_day::date                                  as date_day,
    date_trunc('month', date_day)::date             as month_start,
    date_trunc('week', date_day)::date              as week_start,
    extract(isodow from date_day)::int              as iso_day_of_week,
    extract(isodow from date_day) in (6, 7)         as is_weekend
from days
