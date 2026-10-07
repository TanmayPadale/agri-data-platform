"""Shared pytest fixtures.

Tests that need Postgres use the `db` fixture. It never touches your real `agri`
database: it creates a separate `agri_test` database next to it, applies the same
SQL files, and empties the tables before each test. If Postgres is not running,
those tests are skipped with a hint instead of failing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ingest.config import agri_dsn
from ingest.db import apply_sql, sql_files

TEST_DB = "agri_test"
FIXTURES = Path(__file__).parent / "fixtures"

# Every table a test may write to. TRUNCATE before each test keeps tests independent.
RAW_TABLES = ["raw.weather_daily", "raw.sensor_readings"]


@pytest.fixture(scope="session")
def test_dsn() -> str:
    params = conninfo_to_dict(agri_dsn())
    try:
        with psycopg.connect(agri_dsn(), autocommit=True, connect_timeout=3) as admin:
            exists = admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
            ).fetchone()
            if not exists:
                admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres is not reachable ({exc.__class__.__name__}); run `make up` first")
    params["dbname"] = TEST_DB
    dsn = make_conninfo(**params)
    with psycopg.connect(dsn) as conn:
        apply_sql(conn, sql_files())
    return dsn


@pytest.fixture
def db(test_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(test_dsn) as conn:
        existing = [t for t in RAW_TABLES if _table_exists(conn, t)]
        if existing:
            conn.execute(f"TRUNCATE {', '.join(existing)}")
        conn.commit()
        yield conn


def _table_exists(conn: psycopg.Connection, qualified: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (qualified,)).fetchone()[0]


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Landed files go to a temp folder, never to the repo's data/ folder."""
    monkeypatch.setenv("AGRI_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"
