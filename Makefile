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

# ---------------------------------------------------------------- Day 4

.PHONY: airflow-install airflow backfill test-dags

AIRFLOW_ENV := source scripts/airflow-env.sh &&
FROM ?= $(shell python3 -c "import datetime as d; print(d.date.today() - d.timedelta(days=15))")
TO ?= $(shell python3 -c "import datetime as d; print(d.date.today() - d.timedelta(days=2))")

airflow-install: ## Once: Airflow 3 in its own venv (.airflow/), metadata DB, dbt pool
	$(AIRFLOW_ENV) airflow_install

airflow: ## Airflow standalone, UI on http://localhost:8080 (on 8 GB, stop Kafka first)
	$(AIRFLOW_ENV) airflow standalone

backfill: ## Backfill agri_daily (Airflow running): make backfill FROM=2026-09-21 TO=2026-10-04
	$(AIRFLOW_ENV) airflow backfill create --dag-id agri_daily --from-date $(FROM) --to-date $(TO) \
		--max-active-runs 3 --reprocess-behavior completed

test-dags: ## DAG integrity tests, run inside the Airflow venv
	$(AIRFLOW_ENV) python -m pytest -q tests/test_dags.py -p no:cacheprovider

# ---------------------------------------------------------------- Day 5

.PHONY: image tf-validate emulator tf-emulator-apply tf-emulator-run tf-emulator-destroy

IMAGE ?= agri/sensor-consumer:0.1.0
TF := terraform -chdir=infra
# The emulator accepts any credentials; these dummy values keep real ones out of it.
EMULATOR_ENV := AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=ap-southeast-2 AWS_DEFAULT_REGION=ap-southeast-2

image: ## Build the ingest image (consumer + weather job) and smoke-test it
	docker build -f ingest/Dockerfile -t $(IMAGE) .
	docker run --rm $(IMAGE) python -c "import ingest.sensor_consumer, ingest.weather; print('image ok')"

tf-validate: ## terraform fmt check + validate: no state, no credentials (what CI runs)
	$(TF) fmt -check -recursive
	$(TF) init -backend=false -input=false
	$(TF) validate

emulator: ## Start LocalStack, a free local AWS, on :4566
	docker compose --profile aws up -d --wait localstack

tf-emulator-apply: emulator ## State bucket, raw bucket, Lambda, IAM and schedule, all in the emulator
	$(EMULATOR_ENV) terraform -chdir=infra/bootstrap init -input=false
	$(EMULATOR_ENV) terraform -chdir=infra/bootstrap apply -auto-approve -var-file=../emulator.tfvars
	$(EMULATOR_ENV) $(TF) init -input=false -reconfigure -backend-config=backend/emulator.s3.tfbackend
	$(EMULATOR_ENV) $(TF) apply -auto-approve -var-file=emulator.tfvars

tf-emulator-run: ## Run the Lambda handler locally against the emulator, then list the bucket
	$(EMULATOR_ENV) AWS_ENDPOINT_URL=http://localhost:4566 BUCKET=agri-raw-local \
		uv run python infra/lambda/weather_to_s3/handler.py
	$(EMULATOR_ENV) AWS_ENDPOINT_URL=http://localhost:4566 uv run python -c "import boto3; \
		[print(o['Key'], o['Size']) for o in boto3.client('s3').list_objects_v2(Bucket='agri-raw-local').get('Contents', [])]"

tf-emulator-destroy: ## Remove everything from the emulator again
	$(EMULATOR_ENV) $(TF) destroy -auto-approve -var-file=emulator.tfvars
	$(EMULATOR_ENV) terraform -chdir=infra/bootstrap destroy -auto-approve -var-file=../emulator.tfvars

# ---------------------------------------------------------------- Day 6

.PHONY: docs-fetch docs-ingest ask evals

docs-fetch: ## Download the agronomy references into ai/docs (not committed)
	uv run python -m ai.fetch_docs

docs-ingest: ddl ## Chunk, embed (Ollama) and store the documents in pgvector
	uv run python -m ai.ingest_docs

ask: ## Ask the assistant: make ask Q="Should field F-03 be irrigated this week?"
	uv run python -m ai.ask "$(Q)"

evals: ## Score the assistant on the 15 golden questions
	uv run python -m ai.evals.run_evals

# ---------------------------------------------------------------- Day 7

.PHONY: up-monitoring metrics slo alerts-check k8s-validate verify-mac

PROMETHEUS_IMAGE := prom/prometheus:v3.15.0
KUBECONFORM_IMAGE := ghcr.io/yannh/kubeconform:v0.7.0

up-monitoring: ## Prometheus :9090, Grafana :3000 and kafka-exporter (after make up-stream)
	docker compose --profile stream --profile monitoring up -d --wait prometheus grafana kafka-exporter

metrics: ## What the running consumer exposes: health, readiness and its counters
	@curl -s -o /dev/null -w "/healthz %{http_code}\n" localhost:8000/healthz
	@curl -s -o /dev/null -w "/ready   %{http_code}\n" localhost:8000/ready
	@curl -s localhost:8000/metrics | grep -E '^(messages_processed_total|dlq_messages_total|processing_seconds_count)'

slo: ## Measure the two SLIs against their SLOs: weather freshness, sensor latency
	@for f in sql/slo/*.sql; do echo "== $$f"; $(PSQL) -f - < $$f; done

alerts-check: ## promtool: validate prometheus.yml and alerts.yml, then run the alert unit tests
	docker run --rm -v $(CURDIR)/monitoring:/etc/prometheus:ro --entrypoint promtool \
		$(PROMETHEUS_IMAGE) check config /etc/prometheus/prometheus.yml
	docker run --rm -v $(CURDIR)/monitoring:/etc/prometheus:ro -w /etc/prometheus --entrypoint promtool \
		$(PROMETHEUS_IMAGE) test rules alerts_test.yml

k8s-validate: ## Render the kustomization and check it against the Kubernetes schemas
	kubectl kustomize k8s | docker run --rm -i $(KUBECONFORM_IMAGE) -strict -summary -kubernetes-version 1.37.0 -

verify-mac: ## Day 7 on your Mac: build, load into kind, deploy, check (needs make up-stream)
	bash scripts/verify_mac.sh

# ---------------------------------------------------------------- Day 8

.PHONY: mcp-inspect mcp-check

mcp-inspect: ## Try the MCP server in the MCP Inspector, in your browser (needs Node)
	npx -y @modelcontextprotocol/inspector uv run --quiet python -m ai.mcp_server

mcp-check: ddl ## The MCP server tests, including the read-only role and a real stdio session
	uv run pytest -q tests/test_mcp_server.py
