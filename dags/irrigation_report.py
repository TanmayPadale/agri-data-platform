"""Day 4: the irrigation report, scheduled on data instead of a clock.

This DAG has no cron schedule. It runs whenever the agg_irrigation_signal asset is
updated, which agri_daily does when its quality check passes. It writes the list
of fields to water on the latest decided day to reports/irrigation_<day>.md.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pendulum
from airflow.sdk import Asset, dag, task

log = logging.getLogger(__name__)

IRRIGATION_SIGNAL = Asset("agg_irrigation_signal")  # same name as in agri_daily.py
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


@dag(
    dag_id="irrigation_report",
    schedule=[IRRIGATION_SIGNAL],  # data-aware scheduling: wait for the data, not the clock
    start_date=pendulum.datetime(2026, 9, 1, tz="UTC"),
    catchup=False,
    default_args={"retries": 2},
    tags=["agri", "day-4"],
    doc_md=__doc__,
)
def irrigation_report() -> None:
    @task
    def build_report() -> str:
        from ingest.db import connect

        with connect() as conn:
            day = conn.execute(
                "SELECT max(day) FROM marts.agg_irrigation_signal WHERE irrigate IS NOT NULL"
            ).fetchone()[0]
            if day is None:
                raise ValueError("no decided days in marts.agg_irrigation_signal yet")
            fields = conn.execute(
                """
                SELECT field_id, field_name, crop, moisture_3d_avg_pct, rain_mm, reason
                FROM marts.agg_irrigation_signal
                WHERE day = %s AND irrigate
                ORDER BY field_id
                """,
                (day,),
            ).fetchall()

        lines = [f"# Irrigation report for {day}", "", "Fields to water:", ""]
        if fields:
            lines += [
                f"- **{fid}** {name} ({crop}): {reason}" for fid, name, crop, _, _, reason in fields
            ]
        else:
            lines.append("- none today")
        # Named after the data day, not the day the report happens to run: rerunning it for the
        # same data replaces the same file instead of creating a new one.
        REPORTS_DIR.mkdir(exist_ok=True)
        path = REPORTS_DIR / f"irrigation_{day}.md"
        path.write_text("\n".join(lines) + "\n")
        log.info("wrote %s (%d fields to water)", path, len(fields))
        return str(path)

    build_report()


irrigation_report()
