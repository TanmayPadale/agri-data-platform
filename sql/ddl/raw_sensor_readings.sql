-- raw.sensor_readings: one row per soil-moisture reading, written by
-- ingest/sensor_consumer.py from the Kafka topic sensor.readings.
--
-- PRIMARY KEY (sensor_id, ts) is the real duplicate guard. Kafka delivers at least
-- once, so the same reading can arrive twice (a producer retry, a consumer crash
-- before its offset commit, a replay). The consumer inserts with
-- ON CONFLICT (sensor_id, ts) DO NOTHING, so a second copy changes nothing.
CREATE TABLE IF NOT EXISTS raw.sensor_readings (
    sensor_id          TEXT          NOT NULL,
    ts                 TIMESTAMPTZ   NOT NULL,              -- event time: when the sensor measured
    soil_moisture_pct  NUMERIC(5,2)  NOT NULL,
    event_id           TEXT          NOT NULL,
    kafka_partition    INT,                                 -- where it came from, for debugging and lineage
    kafka_offset       BIGINT,
    loaded_at          TIMESTAMPTZ   NOT NULL DEFAULT now(), -- processing time: loaded_at - ts is the latency SLI
    PRIMARY KEY (sensor_id, ts)
);

-- Freshness checks and the latency SLI filter on loaded_at.
CREATE INDEX IF NOT EXISTS sensor_readings_loaded_at_idx ON raw.sensor_readings (loaded_at);
