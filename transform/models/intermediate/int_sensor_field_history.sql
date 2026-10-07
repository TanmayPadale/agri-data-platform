-- Which field each sensor was on, and when: the SCD2 snapshot as validity windows.
-- Grain: one row per sensor per placement.
--
-- One subtlety: the snapshot only starts recording the first time it runs, so the
-- earliest version's dbt_valid_from is "the day we first ran dbt", not the day the
-- sensor was installed. Readings from before that still belong to the first field,
-- so the first version of each sensor is stretched back to -infinity. Without
-- this, every historical reading would silently drop out of the join downstream.

with versions as (

    select
        sensor_id,
        field_id,
        field_name,
        crop,
        location_id,
        dbt_valid_from,
        dbt_valid_to,
        row_number() over (partition by sensor_id order by dbt_valid_from) as version_number
    from {{ ref('sensors_snapshot') }}

)

select
    sensor_id,
    field_id,
    field_name,
    crop,
    location_id,
    version_number,
    case
        when version_number = 1 then '-infinity'::timestamptz
        else dbt_valid_from::timestamptz
    end                                                   as valid_from,
    coalesce(dbt_valid_to::timestamptz, 'infinity')       as valid_to,
    dbt_valid_to is null                                  as is_current
from versions
