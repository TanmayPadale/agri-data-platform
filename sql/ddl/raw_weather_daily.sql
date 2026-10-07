-- raw.weather_daily: one row per farm per local calendar day, exactly as Open-Meteo
-- reported it after the row passed the WeatherDay contract (ingest/models.py).
--
-- The primary key is the natural key (location_id, day). That is what makes every
-- reload safe: the loader upserts on it, so loading the same day ten times leaves
-- one row, not ten.
CREATE SCHEMA IF NOT EXISTS raw;

-- One-off: an early draft of this table (location_id TEXT, temp_max_c, ingested_at)
-- may exist in a dev volume created before 7 Oct 2026. Raw weather can be rebuilt
-- from the API in seconds, so the draft is dropped rather than migrated.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'raw' AND table_name = 'weather_daily' AND column_name = 'ingested_at'
    ) THEN
        DROP TABLE raw.weather_daily;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS raw.weather_daily (
    location_id  INT          NOT NULL,                  -- seeds/locations.csv
    day          DATE         NOT NULL,                  -- local calendar day at the farm
    t_max_c      NUMERIC(5,2),
    t_min_c      NUMERIC(5,2),
    rain_mm      NUMERIC(7,2),
    et0_mm       NUMERIC(6,2),                           -- FAO reference evapotranspiration
    source       TEXT         NOT NULL DEFAULT 'open-meteo',
    loaded_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),    -- last write: dbt source freshness uses this
    first_loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- first arrival, never updated (Day 7 SLO)
    PRIMARY KEY (location_id, day)
);

-- Day 7: tables created before first_loaded_at existed get it here. Older rows take
-- their loaded_at as the best guess. The upsert in ingest/weather.py never sets this
-- column, so after the first insert it keeps the original arrival time forever.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'raw' AND table_name = 'weather_daily'
          AND column_name = 'first_loaded_at'
    ) THEN
        ALTER TABLE raw.weather_daily ADD COLUMN first_loaded_at TIMESTAMPTZ;
        UPDATE raw.weather_daily SET first_loaded_at = loaded_at;
        ALTER TABLE raw.weather_daily
            ALTER COLUMN first_loaded_at SET DEFAULT now(),
            ALTER COLUMN first_loaded_at SET NOT NULL;
    END IF;
END $$;
