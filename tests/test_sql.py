"""The SQL layer, run against the fixture database.

Every expected number here can be worked out by hand from
tests/fixtures/make_fixtures.py.
"""

import pytest

from conftest import scalar


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$408,468", 408468),
        ("$1,234.50", 1234.50),
        ("$0", 0),
        ("N/A", None),
        ("", None),
    ],
)
def test_parse_money(db, text, expected):
    assert scalar(db, "SELECT core.parse_money(%s)", text) == expected


@pytest.mark.parametrize(
    "raw, key",
    [
        ("Phil Ivey", "phil ivey"),
        ("Phil  Ivey", "phil ivey"),
        ("  phil ivey ", "phil ivey"),
        ("Phil Ivey.", "phil ivey"),
        ("Phil Ivey", "phil ivey"),   # non-breaking space
        ("   ", None),
    ],
)
def test_name_key(db, raw, key):
    assert scalar(db, "SELECT core.name_key(%s)", raw) == key


def test_k_suffix_buy_in_reads_as_thousands(db):
    row = db.execute(
        "SELECT buy_in, buy_in_parse FROM core.events WHERE event_id = 2"
    ).fetchone()
    assert row == (10000, "thousands")


def test_missing_buy_in_is_null_not_guessed(db):
    row = db.execute(
        "SELECT buy_in, buy_in_parse FROM core.events WHERE event_id = 4"
    ).fetchone()
    assert row == (None, "unparsed")


def test_one_player_three_spellings(db):
    n = scalar(db, "SELECT count(*) FROM core.players WHERE name_key = 'phil ivey'")
    assert n == 1
    events = scalar(db, """
        SELECT count(DISTINCT r.event_id) FROM core.results r
        JOIN core.players p USING (player_id) WHERE p.name_key = 'phil ivey'""")
    assert events == 3


def test_duplicate_and_junk_rows_dropped(db):
    assert scalar(db, "SELECT count(*) FROM raw.wsop_results") == 92
    assert scalar(db, "SELECT count(*) FROM core.results") == 90
    assert scalar(db, "SELECT count(*) FROM core.results WHERE event_id = 1") == 15


def test_field_reconstruction_on_known_events(db):
    rows = dict(db.execute(
        "SELECT event_id, entrants FROM analysis.trusted_events"
    ).fetchall())
    assert rows == {1: 100, 2: 200}


def test_every_exclusion_reason_fires_once(db):
    reasons = dict(db.execute(
        "SELECT outcome, events FROM analysis.exclusion_reasons"
    ).fetchall())
    assert reasons == {
        "kept": 2,
        "online event": 1,
        "buy-in not in event name": 1,
        "estimates disagree": 1,
    }


def test_outcome_summary(db):
    entries, cashes, cash_rate, field_roi = db.execute(
        "SELECT entries, cashes, cash_rate, field_roi FROM analysis.outcome_summary"
    ).fetchone()
    assert (entries, cashes) == (300, 45)
    assert float(cash_rate) == pytest.approx(0.15)
    # Paid 1,932,000 on 2,100,000 spent.
    assert float(field_roi) == pytest.approx(-0.08)


def test_careers_only_count_trusted_events(db):
    # Ivey cashed in A, B and E, but E fails the cross-check.
    assert scalar(db, """
        SELECT cashes FROM analysis.careers c
        JOIN core.players p USING (player_id) WHERE p.name_key = 'phil ivey'""") == 2


def test_winner_has_depth_zero_and_min_cash_depth_one(db):
    lo, hi = db.execute(
        "SELECT min(depth), max(depth) FROM analysis.player_cashes"
    ).fetchone()
    assert (float(lo), float(hi)) == (0.0, 1.0)
