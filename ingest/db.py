"""Postgres helpers: one way to connect, one way to create the tables.

    uv run python -m ingest.db                            # apply every SQL file (make ddl)
    uv run python -m ingest.db --create-database airflow  # an extra database on the same server

The SQL files in sql/init, sql/ddl and sql/roles are applied in that order. Each
uses CREATE ... IF NOT EXISTS style statements, so applying them again is harmless.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

import psycopg
from psycopg import sql

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


def ensure_database(name: str, dsn: str | None = None) -> bool:
    """Create database `name` on the same server if it is missing. Returns True if created.

    CREATE DATABASE cannot run inside a transaction, hence autocommit. The name is
    passed through sql.Identifier, which quotes it safely instead of pasting it in.
    """
    with connect(dsn, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists:
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Apply the SQL files, or create a database.")
    parser.add_argument("--create-database", metavar="NAME", help="create this database if missing")
    args = parser.parse_args(argv)
    if args.create_database:
        created = ensure_database(args.create_database)
        print(f"database {args.create_database}: {'created' if created else 'already exists'}")
        return
    with connect() as conn:
        for path in apply_sql(conn, sql_files()):
            print(f"applied {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
