# agri-data-platform

Weather and soil-moisture data in, one decision out: should this field be irrigated today?

This is the capstone of my Stack Refresh Sprint, built one layer a day.

- **Day 1:** daily weather for two farms (Jawali in Satara, India, and Griffith in the
  Riverina, NSW) loads from Open-Meteo into Postgres, validated and idempotent.
- **Day 2:** twenty simulated soil-moisture sensors stream through Kafka. The consumer
  validates every message, parks bad ones in a dead-letter topic, writes with an
  idempotent insert and commits the offset only after the write, so a crash causes
  reprocessing but never a duplicate row.
- **Day 3:** dbt models the raw tables into staging views and marts with a stated grain,
  a field-by-day irrigation signal, 34 tests and an SCD2 snapshot of sensor placements.

## Quickstart

```bash
cp .env.example .env     # optional, every setting has a default
make up                  # Postgres 17 + pgvector in Docker
make ingest              # last 90 days of weather for both farms
make queries             # five analytical queries over the result

make up-stream           # + Kafka 4 (KRaft) and Redis, topics created
make history             # 21 days of past sensor readings into Kafka
make consume             # Kafka -> raw.sensor_readings (Ctrl+C stops)

make dbt-build           # staging, marts, snapshot and every test
make signal              # latest irrigation decision for every field
make test
```

Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
