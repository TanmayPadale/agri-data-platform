# One entry point for every routine command. `make` on its own lists them.
#
# Values from .env (if present) are loaded and exported to every command below.

SHELL := /bin/bash
.DEFAULT_GOAL := help
-include .env
export

PSQL := docker compose exec -T postgres psql -U agri -d agri

.PHONY: help
help: ## List the targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- Day 1

.PHONY: up down reset ddl ingest queries psql fake-weather test lint fmt

up: ## Start Postgres (waits until it is healthy)
	docker compose up -d --wait postgres

down: ## Stop every container (data volumes are kept)
	docker compose down

reset: ## Stop everything AND delete the data volumes (asks first)
	@read -p "Delete all local data volumes? [y/N] " ok && [ "$$ok" = "y" ] && docker compose down -v

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
