"""DAG integrity tests: catch broken DAGs before the scheduler does.

Airflow lives in its own venv, so these run with `make test-dags` and are skipped
in the project venv (and in the main CI job, which has a separate DAG job).
"""

import re
from pathlib import Path

import pytest

pytest.importorskip("airflow", reason="Airflow is in its own venv: run `make test-dags`")

from airflow.dag_processing.dagbag import DagBag  # noqa: E402
from airflow.sdk import CronDataIntervalTimetable  # noqa: E402

DAGS = Path(__file__).resolve().parent.parent / "dags"


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    return DagBag(dag_folder=str(DAGS))  # examples are off via AIRFLOW__CORE__LOAD_EXAMPLES


def test_dags_import_without_errors(dagbag):
    assert dagbag.import_errors == {}
    assert {"agri_daily", "irrigation_report"} <= set(dagbag.dag_ids)


def test_agri_daily_shape(dagbag):
    dag = dagbag.get_dag("agri_daily")
    assert {"extract", "load", "dbt_build", "quality_check", "summary"} == set(dag.task_ids)
    assert dag.get_task("dbt_build").upstream_task_ids == {"load"}
    assert dag.get_task("quality_check").upstream_task_ids == {"load", "dbt_build"}
    assert dag.max_active_runs == 3


def test_every_task_retries(dagbag):
    for dag_id in ("agri_daily", "irrigation_report"):
        for task in dagbag.get_dag(dag_id).tasks:
            assert task.retries >= 1, f"{dag_id}.{task.task_id} has no retries"


def test_daily_runs_get_a_real_data_interval(dagbag):
    """The Airflow 3 trap: only a data-interval timetable makes `day` mean yesterday."""
    assert isinstance(dagbag.get_dag("agri_daily").timetable, CronDataIntervalTimetable)


def test_dbt_runs_one_at_a_time(dagbag):
    assert dagbag.get_dag("agri_daily").get_task("dbt_build").pool == "dbt"


def test_report_waits_for_the_quality_check(dagbag):
    """The asset is emitted by quality_check, so a failed check never triggers a report."""
    tasks = {t.task_id: t for t in dagbag.get_dag("agri_daily").tasks}
    assert [a.name for a in tasks["quality_check"].outlets] == ["agg_irrigation_signal"]
    assert tasks["dbt_build"].outlets == []


@pytest.mark.parametrize("path", sorted(DAGS.glob("*.py")), ids=lambda p: p.name)
def test_tasks_never_read_the_wall_clock(path):
    """datetime.now() or date.today() inside a task breaks backfills: every run
    would process the same "today" instead of its own interval."""
    code = path.read_text()
    assert not re.search(r"datetime\.now\(|date\.today\(|pendulum\.now\(", code)
