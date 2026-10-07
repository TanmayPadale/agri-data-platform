-- Staging for raw.sensor_readings. Grain: one row per event_id.
--
-- raw's primary key (sensor_id, ts) already absorbs Kafka redeliveries. This
-- ROW_NUMBER dedupe on event_id is the second line of defence: if a device re-sent
-- the same event with a different timestamp, only the first copy to land is kept,
-- so the uniqueness test on event_id below can make a promise and keep it.

with source as (

    select * from {{ source('raw', 'sensor_readings') }}

),

ranked as (

    select
        *,
        row_number() over (partition by event_id order by loaded_at, ts) as copy_number
    from source

)

select
    event_id,
    sensor_id,
    ts                                as reading_ts,
    soil_moisture_pct::numeric(5, 2)  as soil_moisture_pct,
    kafka_partition,
    kafka_offset,
    loaded_at
from ranked
where copy_number = 1
