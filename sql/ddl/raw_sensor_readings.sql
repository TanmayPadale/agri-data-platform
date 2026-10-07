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
    replayed           BOOLEAN       NOT NULL DEFAULT false, -- sent as history (make history), not live
    PRIMARY KEY (sensor_id, ts)
);

-- Day 7: tables created before `replayed` existed get it here. For older rows the
-- best guess is that anything stored more than an hour after it was measured came
-- from a history replay. From now on the producer says so explicitly (a Kafka header).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'raw' AND table_name = 'sensor_readings'
          AND column_name = 'replayed'
    ) THEN
        ALTER TABLE raw.sensor_readings ADD COLUMN replayed BOOLEAN NOT NULL DEFAULT false;
        UPDATE raw.sensor_readings SET replayed = true WHERE loaded_at - ts > interval '1 hour';
    END IF;
END $$;

-- Freshness checks and the latency SLI filter on loaded_at.
CREATE INDEX IF NOT EXISTS sensor_readings_loaded_at_idx ON raw.sensor_readings (loaded_at);
