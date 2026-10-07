# agri-data-platform

Weather and soil-moisture data in, one decision out: should this field be irrigated today?

This is the capstone of my Stack Refresh Sprint, built one layer a day. Day 1 is in
place: daily weather for two farms (Jawali in Satara, India, and Griffith in the
Riverina, NSW) loads from Open-Meteo into Postgres, validated and idempotent.

## Quickstart

```bash
cp .env.example .env     # optional, every setting has a default
make up                  # Postgres 17 + pgvector in Docker
make ingest              # last 90 days of weather for both farms
make queries             # five analytical queries over the result
make test
```

Weather data by [Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0).
