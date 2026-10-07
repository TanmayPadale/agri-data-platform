"""Postgres helpers: one way to connect, one way to create the tables.

Run `uv run python -m ingest.db` (or `make ddl`) to apply every SQL file in
sql/init, sql/ddl and sql/roles, in that order. Each file only uses
CREATE ... IF NOT EXISTS style statements, so applying them again is harmless.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import psycopg

from ingest.config import REPO_ROOT, agri_dsn

# Order matters: schemas first, then tables inside them, then roles that are
# granted access to those tables.
SQL_DIRS = ("sql/init", "sql/ddl", "sql/roles")


def connect(dsn: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    """Open a connection. Used as `with connect() as conn:` it is one transaction:
    psycopg commits when the block ends normally and rolls back on an exception."""
    return psycopg.connect(dsn or agri_dsn(), autocommit=autocommit)


def sql_files(root: Path = REPO_ROOT) -> list[Path]:
    files: list[Path] = []
    for folder in SQL_DIRS:
        files += sorted((root / folder).glob("*.sql"))
    return files


def apply_sql(conn: psycopg.Connection, files: Iterable[Path]) -> list[Path]:
    applied = []
    for path in files:
        # No parameters are passed, so psycopg sends the file as one simple
        # query and Postgres runs every statement in it.
        conn.execute(path.read_text())
        applied.append(path)
    conn.commit()
    return applied


def main() -> None:
    with connect() as conn:
        for path in apply_sql(conn, sql_files()):
            print(f"applied {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
