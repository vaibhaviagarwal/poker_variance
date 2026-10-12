"""Kelly sizing checked against answers with known closed forms."""

import numpy as np
import pytest

from kelly import (
    drawdown_profile,
    kelly_fraction,
    rescale_pool,
    robust_kelly,
    shrink_roi,
    size_buyin,
)


def binary_bet(p: float, b: float, n: int = 10_000) -> np.ndarray:
    """Outcomes of a bet paying b-to-1 with win probability p.

    In this module's units an outcome is what one unit of stake returns:
    1 + b on a win (stake back plus winnings), 0 on a loss.
    """
    wins = int(round(p * n))
    return np.concatenate([np.full(wins, 1.0 + b), np.zeros(n - wins)])


@pytest.mark.parametrize(
    "p, b, expected",
    [
        (0.60, 1.0, 0.20),   # even money: f* = p - q = 0.2
        (0.55, 1.0, 0.10),
        (0.40, 3.0, 0.20),   # 3-to-1: f* = p - q/b = 0.4 - 0.6/3 = 0.2
        (0.25, 4.0, 0.0625), # 0.25 - 0.75/4
    ],
)
def test_kelly_matches_textbook_formula(p, b, expected):
    f, growth = kelly_fraction(binary_bet(p, b))
    # The grid is log-spaced at ~2.3% steps, so allow 5%.
    assert f == pytest.approx(expected, rel=0.05)
    assert growth > 0


@pytest.mark.parametrize("p, b", [(0.50, 1.0), (0.45, 1.0), (0.20, 3.0)])
def test_no_edge_means_do_not_play(p, b):
    # Fair or losing bets have no stake with positive growth.
    f, growth = kelly_fraction(binary_bet(p, b))
    assert f == 0.0
    assert growth == 0.0


def test_rescale_hits_target_mean_and_keeps_shape():
    pool = np.array([0, 0, 0, 2.0, 5.0, 13.0])
    out = rescale_pool(pool, roi=0.25)
    assert out.mean() == pytest.approx(1.25)
    assert np.all(out[pool == 0] == 0)              # losses stay losses
    nz = pool > 0
    assert np.allclose(out[nz] / pool[nz], out[nz][0] / pool[nz][0])  # one factor


def test_shrinkage_extremes():
    prior_mean, prior_var = -0.08, 0.0625
    # A near-infinitely noisy record is ignored: you get the prior.
    mean, _ = shrink_roi(0.80, 1e9, prior_mean, prior_var)
    assert mean == pytest.approx(prior_mean, abs=1e-6)
    # A near-noiseless record is trusted: you get the record.
    mean, _ = shrink_roi(0.80, 1e-9, prior_mean, prior_var)
    assert mean == pytest.approx(0.80, abs=1e-6)


def test_shrinkage_equal_weights_is_midpoint():
    mean, var = shrink_roi(0.40, 0.04, 0.00, 0.04)
    assert mean == pytest.approx(0.20)
    assert var == pytest.approx(0.02)   # posterior tighter than either


def test_averaging_alone_does_not_guarantee_a_smaller_stake():
    """Documents a property that is easy to assume wrongly.

    Averaging growth over a symmetric spread of edges can land slightly
    ABOVE the point-estimate stake. The original version of this test
    asserted the opposite and failed; the protection comes from
    shrinkage (next test), not from averaging.
    """
    pool = binary_bet(0.55, 1.0)
    point, _ = kelly_fraction(rescale_pool(pool, 0.10))
    spread = np.random.default_rng(0).normal(0.10, 0.15, 200)
    robust, _ = robust_kelly(pool, spread)
    assert robust == pytest.approx(point, rel=0.15)


def test_shrinkage_cuts_the_stake_for_a_hot_record():
    """The property the calculator relies on.

    A player who observed +60% on a short, noisy record, from a field
    that averages -8%, should be told to stake much less than their
    results alone suggest.
    """
    pool = binary_bet(0.55, 1.0)
    draws = np.random.default_rng(1).normal(0.60, 0.30, 300)
    s = size_buyin(10_000, pool, 0.60, draws, prior_mean=-0.08, prior_var=0.0625)
    # Record weight = 0.0625 / (0.0625 + 0.09) = 0.41, so +60% shrinks to
    # about +20%. On this bet that roughly halves the stake.
    assert s.point_fraction > 0
    assert s.robust_fraction < 0.6 * s.point_fraction


def test_robust_kelly_with_no_uncertainty_matches_point():
    pool = binary_bet(0.55, 1.0)
    point, _ = kelly_fraction(rescale_pool(pool, 0.10))
    robust, _ = robust_kelly(pool, np.full(50, 0.10))
    assert robust == pytest.approx(point, rel=0.06)


def test_zero_stake_has_no_drawdown():
    pool = binary_bet(0.55, 1.0)
    d = drawdown_profile(pool, roi=0.10, fraction=0.0, entries=200, paths=50)
    assert d["median_final"] == pytest.approx(1.0)
    assert d["median_max_drawdown"] == pytest.approx(0.0)
