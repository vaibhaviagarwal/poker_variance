"""Statistical properties of the bootstrap and testing machinery."""

import numpy as np
import pytest

from variance import (
    Career,
    benjamini_hochberg,
    edge_is_detectable,
    roi_interval,
    sample_size_for_edge,
)


def skewed_pool(n=60_000, cash_rate=0.15, seed=0):
    """A tournament-like outcome pool with mean exactly 1 (break-even)."""
    rng = np.random.default_rng(seed)
    out = np.zeros(n)
    k = int(n * cash_rate)
    out[:k] = rng.exponential(1.0, k)
    out *= 1.0 / out.mean()
    return out


def test_career_rejects_bad_input():
    with pytest.raises(ValueError):
        Career(np.ones(3), np.ones(2))
    with pytest.raises(ValueError):
        Career(np.array([1.0, 0.0]), np.ones(2))


def test_roi_is_profit_over_spend_not_mean_of_ratios():
    # Spent 1 + 9 = 10, got back 0 + 15 = 15 -> ROI +50%.
    # Averaging per-event ratios would give (-100% + 67%) / 2 = -17%.
    c = Career(np.array([1.0, 9.0]), np.array([0.0, 15.0]))
    assert c.roi == pytest.approx(0.5)


def test_interval_coverage_is_close_to_nominal():
    """95% intervals should contain the truth about 95% of the time.

    Checked on moderately skewed data where the percentile bootstrap is
    known to behave. (On real tournament payouts it undercovers at small
    samples, which is why the analysis reports power, not single CIs.)
    """
    rng = np.random.default_rng(1)
    true_roi, hits, trials = 0.0, 0, 120
    for i in range(trials):
        returns = rng.exponential(1.0, 400)
        c = Career(np.ones(400), returns)
        lo, hi = roi_interval(c, iterations=1500, seed=i)
        hits += lo <= true_roi <= hi
    assert 0.88 <= hits / trials <= 0.99


def test_false_alarm_rate_on_break_even_players():
    """Break-even players must rarely be called winners.

    The one-sided test at 95% confidence promises roughly a 2.5% false
    alarm rate; allow up to 6% for simulation noise and skew.
    """
    pool = skewed_pool()
    rng = np.random.default_rng(2)
    flagged, trials = 0, 250
    for i in range(trials):
        c = Career(np.ones(500), rng.choice(pool, 500))
        flagged += edge_is_detectable(c, iterations=1000, seed=i)
    assert flagged / trials <= 0.06


def test_short_unlucky_record_is_not_called_a_winner():
    """Regression test for the two-sided detection bug.

    A short sample with no deep run looks like a heavy loser with a tight
    interval. The two-sided version counted that as "detected", which
    produced impossibly high power at small sample sizes. Only an interval
    clearing zero on the winning side counts.
    """
    returns = np.zeros(200)
    returns[:20] = 2.0                      # twenty min-cashes, nothing deep
    c = Career(np.ones(200), returns)       # ROI = 40/200 - 1 = -80%
    assert edge_is_detectable(c, iterations=1000) is False
    assert edge_is_detectable(c, iterations=1000, direction="negative") is True
    assert edge_is_detectable(c, iterations=1000, direction="both") is True


def test_power_rises_with_sample_size():
    pool = skewed_pool(seed=3)
    curve = sample_size_for_edge(
        1.0, pool, candidates=(50, 400, 3000), trials=60, iterations=400,
        seed=4, power=1.01,
    )
    a, b, c = (curve[n] for n in (50, 400, 3000))
    assert a <= b <= c
    assert c >= 0.9


@pytest.mark.parametrize(
    "pvalues, expected",
    [
        ([0.01, 0.02, 0.03, 0.50], [True, True, True, False]),
        ([0.005, 0.04, 0.03, 0.01], [True, True, True, True]),
        ([0.20, 0.30, 0.40], [False, False, False]),
        # Step-up: neither 0.04 passes its own rank threshold alone (0.025),
        # but the larger one passes 0.05, which carries the smaller with it.
        ([0.04, 0.04], [True, True]),
    ],
)
def test_benjamini_hochberg(pvalues, expected):
    assert benjamini_hochberg(np.array(pvalues), fdr=0.05).tolist() == expected
