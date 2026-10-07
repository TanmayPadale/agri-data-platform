"""Day 4: the daily batch.

    extract -> load -> dbt_build -> quality_check -> summary

Each run owns exactly one data interval, the UTC day [start, end). It fetches that
day's weather for both farms, upserts it, rebuilds the marts and checks the result.
Every step is idempotent, so rerunning a day or backfilling a fortnight is safe:

    make backfill FROM=2026-09-21 TO=2026-10-04

The newspaper model: the run for 1 October starts just after midnight UTC on
2 October, once the day it reports on is over. Tasks read data_interval_start,
never the wall clock, which is what keeps backfills correct.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Asset, CronDataIntervalTimetable, dag, task

log = logging.getLogger(__name__)

# The asset the irrigation_report DAG is scheduled on. It is marked as updated
# when quality_check succeeds, not when dbt finishes, so the report never runs on
# data that failed its checks. (Assets are matched by name across DAG files.)
IRRIGATION_SIGNAL = Asset("agg_irrigation_signal")

DBT_BIN = os.environ.get("AGRI_DBT_BIN", "dbt")


@dag(
    dag_id="agri_daily",
    # Airflow 3 trap: a plain "@daily" resolves to CronTriggerTimetable, whose
    # data_interval_start is the run time itself, so the midnight run would fetch
    # the day that has just begun. CronDataIntervalTimetable gives each run the
    # previous whole day as its interval.
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="UTC"),
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,  # don't create a run for every day since start_date on first unpause
    max_active_runs=3,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(seconds=30),
        "retry_exponential_backoff": True,  # 30 s, 60 s, 120 s ...
        "execution_timeout": timedelta(minutes=10),
    },
    tags=["agri", "day-4"],
    doc_md=__doc__,
)
def agri_daily() -> None:
    @task
    def extract(data_interval_start: pendulum.DateTime | None = None) -> str:
        # Imports inside the task: the scheduler parses this file often, and heavy
        # imports at the top would slow every parse. They run only when the task does.
        from ingest.weather import fetch_day_to_file

        day = data_interval_start.date()
        path = fetch_day_to_file(day)
        log.info("fetched %s to %s", day, path)
        return str(path)  # XCom carries the small path, never the data itself

    @task
    def load(path: str) -> int:
        from ingest.weather import upsert_file

        return upsert_file(path)

    dbt_build = BashOperator(
        task_id="dbt_build",
        # +agg_irrigation_signal = the signal and everything upstream of it, with tests.
        bash_command=f"{DBT_BIN} build --select +agg_irrigation_signal",
        pool="dbt",  # one slot: concurrent dbt builds would collide on the same tables
    )

    @task(outlets=[IRRIGATION_SIGNAL])
    def quality_check(rows: int, data_interval_start: pendulum.DateTime | None = None) -> dict:
        from ingest.db import connect
        from ingest.weather import load_locations

        day = data_interval_start.date()
        if rows == 0:
            raise ValueError(f"no weather rows were loaded for {day}")
        with connect() as conn:
            weather_rows = conn.execute(
                "SELECT count(*) FROM raw.weather_daily WHERE day = %s", (day,)
            ).fetchone()[0]
            signal_rows = conn.execute(
                "SELECT count(*) FROM marts.agg_irrigation_signal WHERE day = %s", (day,)
            ).fetchone()[0]
        expected = len(load_locations())
        if weather_rows != expected:
            raise ValueError(f"{day}: expected {expected} weather rows, found {weather_rows}")
        return {"day": day.isoformat(), "weather_rows": weather_rows, "signal_rows": signal_rows}

    @task
    def summary(check: dict[str, Any]) -> None:
        from ingest.db import connect

        with connect() as conn:
            counts = dict(
                conn.execute(
                    "SELECT coalesce(irrigate::text, 'unknown'), count(*) "
                    "FROM marts.agg_irrigation_signal WHERE day = %s GROUP BY 1",
                    (check["day"],),
                ).fetchall()
            )
        log.info("%s: %s, irrigation decisions %s", check["day"], check, counts)

    rows = load(extract())
    check = quality_check(rows)
    rows >> dbt_build >> check
    summary(check)


agri_daily()
