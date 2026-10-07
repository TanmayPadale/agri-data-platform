-- The fields, as they are today. Grain: one row per field.
-- Built from the current rows of the SCD2 snapshot, so it always reflects the
-- latest sensors.csv, while history stays in int_sensor_field_history.

with current_placements as (

    select *
    from {{ ref('int_sensor_field_history') }}
    where is_current

)

select
    p.field_id,
    min(p.field_name)                              as field_name,
    min(p.crop)                                    as crop,
    min(p.location_id)                             as location_id,
    min(l.location_name)                           as location_name,
    string_agg(p.sensor_id, ', ' order by p.sensor_id) as sensor_ids,
    count(*)                                       as sensor_count
from current_placements as p
join {{ ref('dim_location') }} as l
    on l.location_id = p.location_id
group by p.field_id
