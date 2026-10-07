#!/usr/bin/env bash
# Airflow 3 for this repo, sized for an 8 GB laptop.
#
#   source scripts/airflow-env.sh   # set the environment in this shell
#   airflow_install                 # once: separate venv, metadata database, dbt pool
#   airflow standalone              # scheduler + UI on http://localhost:8080 + DAG processor + triggerer
#
# Or through make: make airflow-install, make airflow, make backfill, make test-dags.
#
# Why a separate venv (.airflow/venv): Airflow pins hundreds of packages through its
# constraints file. Keeping it out of the project venv means dbt, Kafka and the AI
# libraries never fight Airflow over versions. DAG tasks import the repo's own code
# (ingest/...) through PYTHONPATH, and call dbt from the project venv by full path.
#
# On 8 GB: stop Kafka first (make down, then make up). Never run both stacks at once.

AGRI_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AIRFLOW_VERSION="3.3.2"
AIRFLOW_PYTHON="3.12"

export AIRFLOW_HOME="$AGRI_REPO/.airflow"
export AIRFLOW_VENV="$AIRFLOW_HOME/venv"
export PATH="$AIRFLOW_VENV/bin:$PATH"
export PYTHONPATH="$AGRI_REPO${PYTHONPATH:+:$PYTHONPATH}"

export AIRFLOW__CORE__DAGS_FOLDER="$AGRI_REPO/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES="False"
export AIRFLOW__CORE__EXECUTOR="LocalExecutor"
export AIRFLOW__CORE__PARALLELISM="4"  # at most 4 tasks at once on a small laptop
# Airflow's own metadata lives in a separate "airflow" database on the same Postgres.
export AIRFLOW__DATABASE__SQL_ALCHEMY_CONN="postgresql+psycopg2://agri:agri@localhost:5432/airflow"
# The Airflow 3 interval trap: by default a cron string such as "@daily" no longer
# creates data intervals (data_interval_start would be the run time itself). The DAG
# sets CronDataIntervalTimetable explicitly; this makes plain cron strings safe too.
export AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS="True"

# dbt runs from the project venv; the dbt_build task calls it by absolute path.
export AGRI_DBT_BIN="$AGRI_REPO/.venv/bin/dbt"
export DBT_PROJECT_DIR="$AGRI_REPO/transform"
export DBT_PROFILES_DIR="$AGRI_REPO/transform"

airflow_install() {
    set -e
    (cd "$AGRI_REPO" && uv run python -m ingest.db --create-database airflow)
    local constraints
    constraints="$(mktemp)"
    curl -sSfL -o "$constraints" \
        "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${AIRFLOW_PYTHON}.txt"
    uv venv --python "$AIRFLOW_PYTHON" "$AIRFLOW_VENV"
    # Airflow itself, plus what the DAG tasks import from ingest/, plus pytest for DAG tests.
    uv pip install --python "$AIRFLOW_VENV/bin/python" --constraint "$constraints" \
        "apache-airflow[postgres]==${AIRFLOW_VERSION}" httpx pydantic "psycopg[binary]" pytest
    rm -f "$constraints"
    airflow db migrate
    # One dbt build at a time: during a backfill, parallel builds would fight over
    # the same tables. Extract and load still run three days in parallel.
    airflow pools set dbt 1 "One dbt build at a time"
    set +e
    echo "Airflow ${AIRFLOW_VERSION} ready. Start it with: make airflow"
}
