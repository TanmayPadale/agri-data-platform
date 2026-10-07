-- Staging for raw.weather_daily: rename and type, nothing clever.
-- Grain: one row per farm per local calendar day (inherited from raw's primary key).
--
-- This is the only model that knows raw's column names. If the ingest code renames
-- a column, the build breaks here, loudly, and nowhere else needs to change.

with source as (

    select * from {{ source('raw', 'weather_daily') }}

)

select
    location_id,
    day                       as weather_date,
    t_max_c::numeric(5, 2)    as temp_max_c,
    t_min_c::numeric(5, 2)    as temp_min_c,
    rain_mm::numeric(7, 2)    as rain_mm,
    et0_mm::numeric(6, 2)     as et0_mm,
    source                    as weather_source,
    loaded_at
from source
