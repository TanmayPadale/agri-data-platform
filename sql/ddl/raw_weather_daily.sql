CREATE SCHEMA IF NOT EXISTS raw;

CREATE TABLE IF NOT EXISTS raw.weather_daily (
    location_id   TEXT        NOT NULL,
    day           DATE        NOT NULL,
    temp_max_c    NUMERIC,
    temp_min_c    NUMERIC,
    rain_mm       NUMERIC,
    source        TEXT        NOT NULL DEFAULT 'open-meteo',
    ingested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (location_id, day)
);