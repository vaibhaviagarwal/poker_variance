"""What buy-in should a player with a given bankroll actually enter?

The Kelly criterion picks the stake that maximises the long-run growth
rate of a bankroll: the expected log of wealth after each bet. For a
tournament entry costing a fraction f of the bankroll, which pays back R
buy-ins, wealth moves by a factor (1 - f + f*R). Kelly chooses f to
maximise E[log(1 - f + f*R)].

Two things make tournaments a hard case:

1. R is mostly zero. With a ~14% cash rate, every entry loses the full
   stake about six times in seven, and the growth rate is driven by a
   rare tail. Optimal fractions come out far smaller than intuition says.

2. The edge is not known. A player only has an estimate of their ROI,
   and variance.py shows how wide that estimate is. Sizing to the point
   estimate overbets whenever the estimate is high by luck, which is
   precisely when players feel most confident. So the robust version
   averages growth over the whole set of plausible edges (the bootstrap
   draws) rather than plugging in one number.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def rescale_pool(pool: np.ndarray, roi: float) -> np.ndarray:
    """Shift an outcome distribution (in buy-ins) to a target ROI.

    Shape is kept, level moves: every outcome is multiplied by the same
    factor so the mean return becomes 1 + roi.
    """
    pool = np.asarray(pool, dtype=float)
    m = pool.mean()
    if m <= 0:
        raise ValueError("outcome pool must have a positive mean")
    return pool * ((1.0 + roi) / m)


def log_growth(f: float, outcomes: np.ndarray) -> float:
    """Expected log growth per entry when staking fraction f.

    -inf if any outcome would leave the bankroll at or below zero, which
    for a pure buy-in never happens below f=1 but is checked anyway.
    """
    wealth = 1.0 - f + f * outcomes
    if np.any(wealth <= 0):
        return float("-inf")
    return float(np.mean(np.log(wealth)))


def kelly_fraction(
    outcomes: np.ndarray,
    grid: np.ndarray | None = None,
) -> tuple[float, float]:
    """Growth-maximising stake for a known outcome distribution.

    Grid search rather than a closed form: with an arbitrary empirical
    payout distribution there is none, and a log-spaced grid over
    realistic fractions is both exact enough and transparent.
    Returns (fraction, growth per entry).
    """
    if grid is None:
        grid = np.geomspace(1e-5, 0.5, 240)
    outcomes = np.asarray(outcomes, dtype=float)
    if outcomes.size > 20_000:
        outcomes = np.random.default_rng(0).choice(outcomes, 20_000, replace=False)
    wealth = 1.0 - grid[:, None] + grid[:, None] * outcomes[None, :]
    with np.errstate(divide="ignore"):
        growth = np.log(np.clip(wealth, 1e-300, None)).mean(axis=1)
    best = int(np.argmax(growth))
    if growth[best] <= 0:
        return 0.0, 0.0  # no positive-growth stake exists: don't play
    return float(grid[best]), float(growth[best])


def robust_kelly(
    pool: np.ndarray,
    roi_draws: np.ndarray,
    grid: np.ndarray | None = None,
    max_draws: int = 60,
    max_pool: int = 6_000,
    seed: int | None = 0,
) -> tuple[float, float]:
    """Stake that maximises growth averaged over edge uncertainty.

    Each draw is a plausible true edge. For every candidate fraction,
    growth is computed under each draw and averaged.

    Averaging alone does NOT reliably shrink the stake. When uncertainty
    is symmetric around the estimate, the averaged optimum can sit
    slightly above the point optimum (tests/test_kelly.py checks this
    rather than assuming the opposite). What protects a player who ran
    hot is shrinking the estimate toward the population first — see
    shrink_roi and size_buyin. Averaging then accounts for the spread
    that remains.
    """
    rng = np.random.default_rng(seed)
    draws = np.asarray(roi_draws, dtype=float)
    if draws.size > max_draws:
        draws = rng.choice(draws, size=max_draws, replace=False)
    if grid is None:
        grid = np.geomspace(1e-5, 0.5, 120)

    base = np.asarray(pool, dtype=float)
    if base.size > max_pool:
        # A random subsample keeps the payout shape; the tail is what
        # matters and 6,000 draws still contains several hundred cashes.
        base = rng.choice(base, size=max_pool, replace=False)
    # Scale by the mean of the outcomes actually used. Scaling a subsample
    # by the full pool's mean leaves its effective ROI off target by
    # however far the subsample drifted (caught by test_kelly.py).
    base_mean = base.mean()
    total = np.zeros(grid.size)
    for roi in draws:
        scaled = base * ((1.0 + roi) / base_mean)
        wealth = 1.0 - grid[:, None] + grid[:, None] * scaled[None, :]
        # A draw implying a losing edge still contributes: it is what
        # drags the averaged optimum down.
        with np.errstate(divide="ignore"):
            total += np.log(np.clip(wealth, 1e-300, None)).mean(axis=1)
    avg = total / draws.size
    best = int(np.argmax(avg))
    if avg[best] <= 0:
        return 0.0, 0.0
    return float(grid[best]), float(avg[best])


def shrink_roi(
    observed: float,
    sampling_var: float,
    prior_mean: float,
    prior_var: float,
) -> tuple[float, float]:
    """Pull a noisy ROI estimate toward the population it came from.

    The bootstrap tells you how wide a player's uncertainty is, but it is
    centred on their observed result — so a player who ran hot gets an
    interval centred on a lucky number. Sizing to that overbets exactly
    when overbetting is most dangerous.

    Normal-normal empirical Bayes: the posterior mean is a precision-
    weighted average of the player's record and the population mean. A
    short, noisy record is mostly prior; a long one is mostly record. The
    prior comes from the whole dataset, which is the one thing a single
    player cannot know about themselves.

    Returns (posterior mean, posterior variance).
    """
    if sampling_var <= 0 or prior_var <= 0:
        raise ValueError("variances must be positive")
    w = prior_var / (prior_var + sampling_var)  # weight on the record
    mean = w * observed + (1.0 - w) * prior_mean
    var = 1.0 / (1.0 / prior_var + 1.0 / sampling_var)
    return float(mean), float(var)


@dataclass(frozen=True)
class Sizing:
    """Everything a player needs to pick a game."""

    point_fraction: float     # Kelly at the point-estimate edge
    robust_fraction: float    # Kelly averaged over edge uncertainty
    bankroll: float

    @property
    def point_buyin(self) -> float:
        return self.point_fraction * self.bankroll

    @property
    def robust_buyin(self) -> float:
        return self.robust_fraction * self.bankroll

    @property
    def buyins_in_roll(self) -> float:
        """Bankroll expressed in buy-ins at the robust stake."""
        return 1.0 / self.robust_fraction if self.robust_fraction else float("inf")


def size_buyin(
    bankroll: float,
    pool: np.ndarray,
    roi_estimate: float,
    roi_draws: np.ndarray,
    prior_mean: float | None = None,
    prior_var: float | None = None,
    seed: int | None = 0,
) -> Sizing:
    """Naive versus robust sizing for one player.

    Naive trusts the observed ROI. Robust shrinks it toward the
    population (when a prior is given) and then averages growth over the
    posterior, so it accounts for both the likely bias and the spread.
    """
    point_f, _ = kelly_fraction(rescale_pool(pool, roi_estimate))

    draws = np.asarray(roi_draws, dtype=float)
    if prior_mean is not None and prior_var is not None:
        post_mean, post_var = shrink_roi(
            roi_estimate, float(draws.var()), prior_mean, prior_var
        )
        rng = np.random.default_rng(seed)
        # Floor at -0.99: an ROI below -100% is not a possible edge.
        draws = np.maximum(
            rng.normal(post_mean, np.sqrt(post_var), size=draws.size), -0.99
        )
    robust_f, _ = robust_kelly(pool, draws)
    return Sizing(point_f, robust_f, bankroll)


def drawdown_profile(
    pool: np.ndarray,
    roi: float,
    fraction: float,
    entries: int = 1_000,
    paths: int = 2_000,
    seed: int | None = 0,
) -> dict[str, float]:
    """What staking `fraction` per entry feels like over a run of events.

    Kelly maximises growth but says nothing about the ride. This
    simulates bankroll paths and reports the drawdowns a player should
    expect to sit through, which is usually the number that changes
    their mind about how aggressively to play.
    """
    rng = np.random.default_rng(seed)
    scaled = rescale_pool(pool, roi)
    draws = rng.choice(scaled, size=(paths, entries), replace=True)
    factors = 1.0 - fraction + fraction * draws
    wealth = np.cumprod(factors, axis=1)
    peak = np.maximum.accumulate(wealth, axis=1)
    max_dd = (1.0 - wealth / peak).max(axis=1)
    return {
        "median_final": float(np.median(wealth[:, -1])),
        "p_finish_below_start": float((wealth[:, -1] < 1.0).mean()),
        "median_max_drawdown": float(np.median(max_dd)),
        "p90_max_drawdown": float(np.quantile(max_dd, 0.90)),
        "p_halved_at_some_point": float((wealth.min(axis=1) <= 0.5).mean()),
    }
