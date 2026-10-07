-- SLI 2 (streaming latency): the share of readings stored within 60 seconds of being measured.
-- SLO: 99.5% over 30 days. Error budget: 0.5% of readings may be slower.
--
-- loaded_at - ts is processing time minus event time: everything between the sensor
-- and the table (producer, broker, consumer, database). Replayed history (make history)
-- is old on purpose and fails this check; the SLO is about live readings.
SELECT
    count(*)                                                              AS readings,
    count(*) FILTER (WHERE loaded_at - ts < interval '60 seconds')        AS within_60s,
    round(100.0 * count(*) FILTER (WHERE loaded_at - ts < interval '60 seconds')
          / nullif(count(*), 0), 3)                                       AS sli_pct,
    99.5                                                                  AS slo_pct,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM loaded_at - ts))
                                                                          AS p95_latency_s
FROM raw.sensor_readings
WHERE loaded_at >= now() - interval '30 days'
  AND ts >= now() - interval '30 days';
