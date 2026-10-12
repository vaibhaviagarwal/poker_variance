"""Shared test setup.

The SQL tests need a real Postgres: the parsing rules and views are the
thing under test, so mocking the database would test nothing. A separate
database (poker_test by default) is created and rebuilt from the fixture
CSVs once per run, using the same build() the real load uses.

Point at a different server with TEST_DATABASE_URL. If no Postgres is
reachable, the database tests are skipped and the pure-Python ones run.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = ROOT / "tests" / "fixtures"
TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql:///poker_test")


def _ensure_database(url: str) -> None:
    params = conninfo_to_dict(url)
    name = params.get("dbname", "poker_test")
    admin = make_conninfo(url, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session")
def db():
    """A connection to the fixture database, built once per test run."""
    from load import build  # noqa: E402

    try:
        _ensure_database(TEST_URL)
        conn = psycopg.connect(TEST_URL, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no Postgres at {TEST_URL}: {exc}")
    build(conn, FIXTURES)
    yield conn
    conn.close()


def scalar(conn: psycopg.Connection, sql: str, *params):
    return conn.execute(sql, params).fetchone()[0]
