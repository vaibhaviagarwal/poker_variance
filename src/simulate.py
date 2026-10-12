"""Produce the simulation results the dashboard draws from.

    python src/simulate.py

Reads the real WSOP outcome distribution out of Postgres, then writes two
files to data/processed/:

  power_curves.json   for each true edge, the share of simulated careers
                      whose record clears break-even at 95% confidence,
                      by number of events played
  kelly_bins.json     the outcome distribution compressed into a few
                      hundred bins, small enough to ship inside the
                      dashboard so the Kelly calculator runs in the browser

Slow by design (a few minutes): each point on a power curve is hundreds
of bootstrapped careers.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from variance import sample_size_for_edge  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql:///poker")

EDGES = (0.10, 0.20, 0.50, 1.00)
SIZES = (100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000)


def load_outcomes(conn: psycopg.Connection) -> np.ndarray:
    """Every entry in the trusted events, in buy-ins: cashes plus zeros.

    Non-cashing entries do not exist as rows, so they are added back as
    zeros using each event's reconstructed field size.
    """
    cashes = np.array(
        [r[0] for r in conn.execute(
            "SELECT multiple::float8 FROM analysis.cash_multiples"
        )],
        dtype=float,
    )
    entries = conn.execute(
        "SELECT sum(entrants) FROM analysis.trusted_events"
    ).fetchone()[0]
    zeros = int(entries) - cashes.size
    return np.concatenate([cashes, np.zeros(max(zeros, 0))])


def compress(pool: np.ndarray, bins: int = 240) -> dict:
    """Outcome distribution as zero-mass plus log-spaced cash bins.

    Log spacing keeps resolution in the deep tail, which is where all of
    the Kelly calculation's sensitivity lives. Each bin stores the mean
    of the outcomes inside it, so the compressed distribution has exactly
    the same mean as the full one.
    """
    cash = pool[pool > 0]
    edges = np.geomspace(cash.min(), cash.max() * 1.000001, bins + 1)
    idx = np.clip(np.digitize(cash, edges) - 1, 0, bins - 1)
    values, weights = [], []
    for b in range(bins):
        m = idx == b
        if m.any():
            values.append(float(cash[m].mean()))
            weights.append(float(m.sum() / pool.size))
    return {
        "zero_mass": float((pool == 0).mean()),
        "values": values,
        "weights": weights,
        "mean": float(pool.mean()),
        "variance": float(pool.var()),
        "entries": int(pool.size),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DATABASE_URL) as conn:
        pool = load_outcomes(conn)
    print(f"outcome pool: {pool.size:,} entries, cash rate {(pool > 0).mean():.1%}")

    (OUT / "kelly_bins.json").write_text(json.dumps(compress(pool)))
    print("wrote kelly_bins.json")

    rng = np.random.default_rng(1)
    sub = rng.choice(pool, size=min(150_000, pool.size), replace=False)

    curves = {}
    for edge in EDGES:
        t0 = time.time()
        c = sample_size_for_edge(
            edge, sub, candidates=SIZES, trials=120, iterations=800, seed=5,
            power=0.99,  # stop once detection is near-certain
        )
        curves[f"{edge:.2f}"] = {str(k): v for k, v in sorted(c.items())}
        print(f"  edge {edge:+.0%}: " + "  ".join(
            f"{k}:{v:.0%}" for k, v in sorted(c.items())
        ) + f"   [{time.time() - t0:.0f}s]")

    (OUT / "power_curves.json").write_text(json.dumps(
        {"sizes": list(SIZES), "curves": curves}, indent=2
    ))
    print("wrote power_curves.json")


if __name__ == "__main__":
    main()
