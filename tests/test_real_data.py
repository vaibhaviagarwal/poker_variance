"""Data-quality checks against the full loaded dataset.

These need the real 573 MB dataset loaded by `python src/load.py`, so they
run locally only and skip themselves in CI.

    pytest tests/test_real_data.py     # after load.py

The Main Event check is the important one: it is the only place the
reconstruction is compared to an externally known truth, so any change
to the parsing or reconstruction rules that breaks it fails here.
"""

import os

import psycopg
import pytest

REAL_URL = os.environ.get("DATABASE_URL", "postgresql:///poker")


@pytest.fixture(scope="module")
def real():
    try:
        conn = psycopg.connect(REAL_URL, autocommit=True)
        n = conn.execute("SELECT count(*) FROM core.results").fetchone()[0]
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable) as exc:
        pytest.skip(f"real dataset not loaded at {REAL_URL}: {exc}")
    if n < 600_000:
        pytest.skip(f"database at {REAL_URL} holds {n} results, not the full set")
    yield conn
    conn.close()


def one(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_load_is_complete(real):
    assert one(real, "SELECT count(*) FROM raw.wsop_results") == 699_350
    assert one(real, "SELECT count(*) FROM core.results") == 699_211


@pytest.mark.parametrize("year, actual", [(2024, 10_112), (2023, 10_043), (2022, 8_663)])
def test_main_event_reconstruction(real, year, actual):
    est = one(real, f"""
        SELECT est_from_paid FROM analysis.event_fields
        WHERE name ILIKE '%10,000 MAIN EVENT%' AND year = {year} AND NOT is_online""")
    assert abs(float(est) / actual - 1) < 0.02


def test_no_impossible_values(real):
    assert one(real, "SELECT count(*) FROM core.results WHERE payout < 0") == 0
    assert one(real, """SELECT count(*) FROM analysis.trusted_events
                        WHERE entrants < cashers""") == 0
    assert one(real, """SELECT count(*) FROM analysis.player_cashes
                        WHERE depth < 0 OR depth > 1""") == 0


def test_field_return_is_about_the_rake(real):
    # Nobody told the reconstruction what the house takes; it should land
    # near the WSOP's actual cut of roughly 6-12%.
    roi = float(one(real, "SELECT field_roi FROM analysis.outcome_summary"))
    assert -0.12 < roi < -0.05


def test_skill_signal_strengthens_with_more_data(real):
    rs = [float(r) for (r,) in real.execute(
        "SELECT split_half_r FROM analysis.depth_split_half ORDER BY min_cashes")]
    assert all(r > 0.05 for r in rs)
    assert rs == sorted(rs)
