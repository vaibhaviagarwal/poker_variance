"""Generate the tiny fixture dataset the tests load into Postgres.

    python tests/fixtures/make_fixtures.py

Five events, each built to exercise one rule, with answers that can be
worked out by hand:

  A  $1,000, 100 entrants, 15 cashers, pool 92,000  -> kept, entrants 100
  B  "$10K", 200 entrants, 30 cashers, pool 1.84M   -> kept, buy-in 10,000
  C  online series                                  -> excluded: online
  D  no dollar figure in the name                   -> excluded: unparsed
  E  $500, 15 cashers, pool doubled                 -> excluded: disagree

Plus an exact duplicate row and a junk payout in A (both must be dropped),
and one player spelled three ways across A, B and E (must be one player).
The CSVs are committed; this script exists so the fixture is reviewable
and reproducible rather than hand-edited.
"""

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE = "2024 55th Annual World Series of Poker"
ONLINE = "WSOP Online 2020 - US & International"

EVENTS = [
    # tid, series, name, cashers, prize pool, player-1 spelling
    (1, LIVE, "Event #1: $1,000 Fixture Hold'em", 15, 92_000, "Phil  Ivey"),
    (2, LIVE, "Event #2: $10K Fixture Championship", 30, 1_840_000, "phil ivey"),
    (3, ONLINE, "Event #3: $500 Online Fixture", 15, 6_900, None),
    (4, LIVE, "Event #4: Seniors Fixture", 15, 50_000, None),
    (5, LIVE, "Event #5: $500 Doubled Fixture", 15, 92_000, "Phil Ivey."),
]


def payouts(pool: int, n: int) -> list[int]:
    """Decreasing integer payouts summing exactly to pool."""
    weights = list(range(n, 0, -1))
    total = sum(weights)
    out = [pool * w // total for w in weights]
    out[0] += pool - sum(out)
    return out


def money(x: int) -> str:
    return f"${x:,}"


def main() -> None:
    results, pays, meta = [], [], []
    row = 0
    for tid, series, name, n, pool, ivey in EVENTS:
        meta.append([tid - 1, 2, "WSOP", 1, series, tid, name])
        amounts = payouts(pool, n)
        for place, amt in enumerate(amounts, start=1):
            player = ivey if (place == 1 and ivey) else f"Player {tid}-{place:02d}"
            results.append([row, 2, "WSOP", 1, series, tid, name, place,
                            player, money(amt), "Las Vegas", "NV", "United States"])
            pays.append([row, 2, "WSOP", 1, series, tid, name, f"{place}th", money(amt)])
            row += 1
        if tid == 1:
            dup = list(results[-n + 1])  # place 2 of event A, exactly repeated
            dup[0] = row; results.append(dup); row += 1
            results.append([row, 2, "WSOP", 1, series, tid, name, 16,
                            "Junk Payout", "N/A", "", "", ""]); row += 1

    head_r = ["", "aid", "competition", "grid", "tournament", "tid", "event",
              "place", "player", "award", "city", "state", "country"]
    head_p = ["", "aid", "competition", "grid", "tournament", "tid", "event",
              "place", "award"]
    head_m = ["", "aid", "competition", "grid", "tournament", "tid", "event"]
    for fname, head, rows in (("results.csv", head_r, results),
                              ("payouts.csv", head_p, pays),
                              ("metadata.csv", head_m, meta)):
        with open(HERE / fname, "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(head); w.writerows(rows)
    print(f"results {len(results)} rows, payouts {len(pays)}, metadata {len(meta)}")


if __name__ == "__main__":
    main()
