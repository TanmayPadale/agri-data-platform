CREATE SCHEMA IF NOT EXISTS raw;       -- data exactly as it arrived
CREATE SCHEMA IF NOT EXISTS staging;   -- cleaned, renamed, typed (dbt, Day 3)
CREATE SCHEMA IF NOT EXISTS marts;     -- star schemas for consumers (dbt, Day 3)