# One entry point for every routine command. `make` on its own lists them.
#
# Values from .env (if present) are loaded and exported to every command below.

SHELL := /bin/bash
.DEFAULT_GOAL := help
-include .env
export

PSQL := docker compose exec -T postgres psql -U agri -d agri

# dbt reads these, so `uv run dbt ...` works from the repo root.
export DBT_PROJECT_DIR := $(CURDIR)/transform
export DBT_PROFILES_DIR := $(CURDIR)/transform

.PHONY: help
help: ## List the targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- Day 1

.PHONY: up down reset ddl ingest queries psql fake-weather test lint fmt

up: ## Start Postgres (waits until it is healthy)
	docker compose up -d --wait postgres

down: ## Stop every container (data volumes are kept)
	docker compose --profile stream --profile monitoring down

reset: ## Stop everything AND delete the data volumes (asks first)
	@read -p "Delete all local data volumes? [y/N] " ok && [ "$$ok" = "y" ] && docker compose --profile stream --profile monitoring down -v

ddl: ## Create schemas and tables (safe to rerun)
	uv run python -m ingest.db

ingest: ddl ## Load the last 90 days of weather for both farms
	uv run python -m ingest.weather --days 90

queries: ## Run the five Day 1 analytical queries
	@for f in sql/queries/*.sql; do echo "== $$f"; $(PSQL) -f - < $$f | head -12; done

psql: ## Open a SQL prompt on the local database
	docker compose exec postgres psql -U agri -d agri

fake-weather: ## Serve recorded Open-Meteo responses on :8090 (offline or out of quota)
	uv run python -m tests.fakes.open_meteo --port 8090

test: ## Run the test suite (Postgres tests skip if it is not running)
	uv run pytest -q -m "not slow"

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-fix lint issues and format
	uv run ruff check . --fix
	uv run ruff format .

# ---------------------------------------------------------------- Day 2

.PHONY: up-stream topics produce history poison consume group dlq

KAFKA_BIN := docker compose exec -T kafka /opt/kafka/bin
BOOTSTRAP := --bootstrap-server localhost:9092

up-stream: ## Start Postgres, Kafka and Redis, then create the topics
	docker compose --profile stream up -d --wait postgres kafka redis
	$(MAKE) --no-print-directory topics ddl

topics: ## Create sensor.readings (6 partitions) and its DLQ (safe to rerun)
	$(KAFKA_BIN)/kafka-topics.sh $(BOOTSTRAP) --create --if-not-exists --topic sensor.readings --partitions 6 --replication-factor 1
	$(KAFKA_BIN)/kafka-topics.sh $(BOOTSTRAP) --create --if-not-exists --topic sensor.readings.dlq --partitions 1 --replication-factor 1

produce: ## Stream live readings from 20 sensors every 2 s (Ctrl+C stops)
	uv run python -m ingest.sensor_producer

history: ## Send 21 days of past readings, so dbt has days to model
	uv run python -m ingest.sensor_producer --history-days 21 --seed 42

poison: ## Send one malformed message (it should land in the DLQ)
	uv run python -m ingest.sensor_producer --poison

consume: ## Run the consumer (Ctrl+C stops)
	uv run python -m ingest.sensor_consumer

group: ## Show the consumer group: who owns which partition, and the lag
	$(KAFKA_BIN)/kafka-consumer-groups.sh $(BOOTSTRAP) --describe --group agri-loader

dlq: ## Print what is parked in the dead-letter topic, with its headers
	$(KAFKA_BIN)/kafka-console-consumer.sh $(BOOTSTRAP) --topic sensor.readings.dlq --from-beginning --timeout-ms 5000 --formatter-property print.key=true --formatter-property print.headers=true

# ---------------------------------------------------------------- Day 3

.PHONY: dbt-deps dbt-build dbt-docs freshness signal

dbt-deps: ## Install dbt packages (dbt_utils, pinned in transform/packages.yml)
	uv run dbt deps

dbt-build: dbt-deps ## Seeds, snapshot, models and every test, in dependency order
	uv run dbt build

dbt-docs: ## Build the dbt docs site (lineage graph) and serve it on :8081
	uv run dbt docs generate
	uv run dbt docs serve --port 8081

freshness: ## Is ingestion still arriving? Warns/errors on stale raw tables
	uv run dbt source freshness

signal: ## Latest irrigation decision for every field
	@$(PSQL) -c "select field_id, crop, day, moisture_3d_avg_pct as moisture_3d, rain_mm, irrigate, reason from marts.agg_irrigation_signal where day = (select max(day) from marts.agg_irrigation_signal where irrigate is not null) order by field_id"
