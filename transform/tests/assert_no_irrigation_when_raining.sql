-- A singular test: a query that must return zero rows.
-- If any field is flagged for irrigation on a day with enough rain, the rule has
-- regressed, and every row returned here is a field that would be wrongly watered.

select field_id, day, rain_mm, moisture_3d_avg_pct, irrigate
from {{ ref('agg_irrigation_signal') }}
where irrigate
  and rain_mm >= {{ var('rain_threshold_mm') }}
