-- The farms. Grain: one row per location (from seeds/locations.csv).

select
    location_id,
    name        as location_name,
    region,
    latitude,
    longitude,
    timezone
from {{ ref('locations') }}
