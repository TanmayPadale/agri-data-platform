"""Loading into Postgres: the idempotency guarantee, end to end.

Needs Postgres (`make up`); skipped otherwise. Uses the separate agri_test database.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from ingest.weather import (
    fetch_range_to_files,
    load_locations,
    upsert_file,
    write_json_atomic,
)
from tests.fakes.open_meteo import FakeOpenMeteo

pytestmark = pytest.mark.db

FIXTURE = Path(__file__).parent / "fixtures" / "open_meteo_archive_jawali.json"
QUERIES = sorted((Path(__file__).parent.parent / "sql" / "queries").glob("*.sql"))


def _day_file(tmp_path: Path, daily: dict, endpoint: str = "archive") -> Path:
    path = tmp_path / "day.json"
    write_json_atomic(
        path,
        {
            "day": daily["time"][0],
            "locations": [{"location_id": 1, "endpoint": endpoint, "daily": daily}],
        },
    )
    return path


def _count(db) -> int:
    return db.execute("SELECT count(*) FROM raw.weather_daily").fetchone()[0]


def test_loading_twice_leaves_the_same_rows(db, test_dsn, tmp_path):
    path = _day_file(tmp_path, json.loads(FIXTURE.read_text())["daily"])
    assert upsert_file(path, dsn=test_dsn) == 5
    assert upsert_file(path, dsn=test_dsn) == 5  # 5 rows written again...
    assert _count(db) == 5  # ...but still 5 rows: ON CONFLICT updated them in place


def test_a_corrected_value_updates_the_row(db, test_dsn, tmp_path):
    daily = json.loads(FIXTURE.read_text())["daily"]
    upsert_file(_day_file(tmp_path, daily), dsn=test_dsn)
    daily["precipitation_sum"][0] = 12.5  # the provider revised its number
    upsert_file(_day_file(tmp_path, daily, endpoint="forecast"), dsn=test_dsn)
    rain, source = db.execute(
        "SELECT rain_mm, source FROM raw.weather_daily WHERE day = '2026-09-24'"
    ).fetchone()
    assert float(rain) == 12.5
    assert source == "open-meteo-forecast"
    assert _count(db) == 5


def test_rejected_days_are_not_loaded(db, test_dsn, tmp_path):
    daily = json.loads(FIXTURE.read_text())["daily"]
    daily["precipitation_sum"][1] = None
    assert upsert_file(_day_file(tmp_path, daily), dsn=test_dsn) == 4
    days = [r[0] for r in db.execute("SELECT day FROM raw.weather_daily ORDER BY day")]
    assert date(2026, 9, 25) not in days


def test_fake_api_to_postgres_both_farms(db, test_dsn, monkeypatch):
    """The whole Day 1 path against the replay server: HTTP, files, validation, upsert."""
    server = FakeOpenMeteo().start_in_thread()
    try:
        monkeypatch.setenv("OPEN_METEO_ARCHIVE_URL", f"{server.base_url}/v1/archive")
        monkeypatch.setenv("OPEN_METEO_FORECAST_URL", f"{server.base_url}/v1/forecast")
        paths = fetch_range_to_files(
            date(2026, 9, 25),
            date(2026, 10, 4),
            locations=load_locations(),
            today=date(2026, 10, 7),
        )
        assert len(paths) == 10
        loaded = sum(upsert_file(p, dsn=test_dsn) for p in paths)
    finally:
        server.shutdown()
    assert loaded == 20  # 10 days x 2 farms
    sources = dict(
        db.execute("SELECT source, count(*) FROM raw.weather_daily GROUP BY source").fetchall()
    )
    assert sources == {"open-meteo-archive": 16, "open-meteo-forecast": 4}  # cutoff is 2 Oct
    assert server.requests_served == 4  # one per farm per endpoint, not one per day


@pytest.mark.parametrize("query", QUERIES, ids=[q.stem for q in QUERIES])
def test_analytical_queries_run(db, test_dsn, tmp_path, query):
    upsert_file(_day_file(tmp_path, json.loads(FIXTURE.read_text())["daily"]), dsn=test_dsn)
    rows = db.execute(query.read_text()).fetchall()
    if query.stem.startswith("04_"):
        assert rows == []  # the fixture has no gaps
    else:
        assert rows, f"{query.name} returned nothing"
