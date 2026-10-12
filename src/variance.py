"""How long before a tournament record means anything?

Tournament results are about as badly behaved as financial return series
get. Most entries return nothing at all, a small fraction return a few
buy-ins, and a handful of scores dominate a career outright. Under that
distribution the sample mean converges slowly and the usual normal-theory
confidence interval is simply wrong — it assumes a symmetry the data does
not have.

So everything here is resampling-based. Nothing assumes normality,
nothing uses a t-statistic, and the asymmetry of the resulting intervals
is itself part of the finding: the upper and lower bounds on a player's
true ROI are not the same distance from the estimate.

The module takes a sequence of per-event outcomes and nothing else, so it
does not care how the raw data is shaped.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Career:
    """One player's record, as paired buy-ins and returns.

    `buyins[i]` is what entry i cost, `returns[i]` is what it paid back
    (zero for the overwhelming majority of entries). Keeping them paired
    rather than pre-dividing matters: tournaments have different buy-in
    levels, and ROI over a mixed career is total profit over total spend,
    not the average of per-event ratios.
    """

    buyins: np.ndarray
    returns: np.ndarray

    def __post_init__(self) -> None:
        if len(self.buyins) != len(self.returns):
            raise ValueError("buyins and returns must be the same length")
        if np.any(self.buyins <= 0):
            raise ValueError("every entry must have a positive buy-in")

    @property
    def n(self) -> int:
        return len(self.buyins)

    @property
    def roi(self) -> float:
        """Profit as a fraction of total spend."""
        spend = self.buyins.sum()
        return float((self.returns.sum() - spend) / spend)

    @property
    def cash_rate(self) -> float:
        return float((self.returns > 0).mean())


def bootstrap_roi(
    career: Career,
    iterations: int = 20_000,
    seed: int | None = 0,
) -> np.ndarray:
    """Resample a career with replacement and recompute ROI each time.

    The resampling unit is the entry, not the dollar: each draw picks a
    whole (buy-in, return) pair. That preserves the association between
    stake size and outcome, which matters because a player's big scores
    usually come at particular buy-in levels rather than at random.
    """
    rng = np.random.default_rng(seed)
    n = career.n
    idx = rng.integers(0, n, size=(iterations, n))
    spend = career.buyins[idx].sum(axis=1)
    back = career.returns[idx].sum(axis=1)
    return (back - spend) / spend


def roi_interval(
    career: Career,
    confidence: float = 0.95,
    iterations: int = 20_000,
    seed: int | None = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval for true ROI."""
    draws = bootstrap_roi(career, iterations, seed)
    tail = (1 - confidence) / 2
    lo, hi = np.quantile(draws, [tail, 1 - tail])
    return float(lo), float(hi)


def edge_is_detectable(
    career: Career,
    confidence: float = 0.95,
    iterations: int = 20_000,
    seed: int | None = 0,
    direction: str = "positive",
) -> bool:
    """Whether the record is inconsistent with a break-even player.

    One-sided by default, and that matters more here than usual. With
    payouts this top-heavy, a short sample usually contains no deep run,
    so a genuine winner looks like a heavy loser with a deceptively tight
    interval. A two-sided test counts that as "detected" and reports
    impossibly high power at small samples — the wrong sign, called
    significant. Only an interval clearing zero on the winning side is
    evidence of an edge.
    """
    lo, hi = roi_interval(career, confidence, iterations, seed)
    if direction == "positive":
        return lo > 0
    if direction == "negative":
        return hi < 0
    return lo > 0 or hi < 0


def sample_size_for_edge(
    true_roi: float,
    outcome_pool: np.ndarray,
    buyin: float = 1.0,
    confidence: float = 0.95,
    power: float = 0.80,
    candidates: tuple[int, ...] = (
        50, 100, 250, 500, 1_000, 2_500, 5_000, 10_000, 25_000, 50_000
    ),
    trials: int = 400,
    iterations: int = 2_000,
    seed: int | None = 0,
) -> dict[int, float]:
    """How often an edge of a given size is detected, by sample size.

    The simulation draws synthetic careers from a real outcome
    distribution rescaled to the target ROI, rather than from a fitted
    parametric one. Tournament payout shapes are the whole difficulty of
    this problem, so replacing them with a lognormal would quietly assume
    away the thing being measured.

    Returns {sample size: fraction of simulated careers whose interval
    excluded zero}. The answer to "how many events do I need" is the
    smallest size whose fraction clears `power`.
    """
    rng = np.random.default_rng(seed)

    pool = np.asarray(outcome_pool, dtype=float)
    if pool.ndim != 1 or pool.size == 0:
        raise ValueError("outcome_pool must be a non-empty 1-D array")

    # Rescale the pool so its mean return corresponds to the target ROI.
    # Shape is preserved; only the level moves.
    current = pool.mean() / buyin - 1.0
    if np.isclose(pool.mean(), 0.0):
        raise ValueError("outcome pool has zero mean; cannot rescale")
    scaled = pool * ((1.0 + true_roi) / (1.0 + current))

    out: dict[int, float] = {}
    for n in candidates:
        detected = 0
        for _ in range(trials):
            draw = rng.choice(scaled, size=n, replace=True)
            career = Career(
                buyins=np.full(n, buyin, dtype=float),
                returns=draw,
            )
            if edge_is_detectable(
                career,
                confidence=confidence,
                iterations=iterations,
                seed=int(rng.integers(0, 2**31 - 1)),
            ):
                detected += 1
        out[n] = detected / trials
        # Once power is comfortably cleared, larger samples only cost time.
        if out[n] >= power and n >= 500:
            break
    return out


def events_needed(power_curve: dict[int, float], power: float = 0.80) -> int | None:
    """The smallest tested sample size reaching the target power."""
    for n in sorted(power_curve):
        if power_curve[n] >= power:
            return n
    return None


def false_discovery_count(
    n_players: int, alpha: float = 0.05
) -> float:
    """Expected number of break-even players flagged as winners by chance.

    Testing every player in a dataset for a significant edge is a
    multiple-comparisons problem, and at the scale of these datasets it
    is not a footnote: screening ten thousand break-even players at the
    5% level produces about five hundred false winners. Any claim about
    "how many players beat the game" has to net this out.
    """
    return n_players * alpha / 2  # one-sided, upper tail only


def benjamini_hochberg(pvalues: np.ndarray, fdr: float = 0.05) -> np.ndarray:
    """Which hypotheses survive at a given false discovery rate.

    Bonferroni would be the conservative choice, but with tens of
    thousands of players it is so conservative that nothing survives and
    the analysis says nothing. Benjamini-Hochberg controls the expected
    proportion of false positives among the rejections instead, which is
    the right trade when the question is "roughly how many winners are
    there" rather than "is this specific player a winner".
    """
    p = np.asarray(pvalues, dtype=float)
    m = p.size
    order = np.argsort(p)
    ranked = p[order]
    thresholds = fdr * np.arange(1, m + 1) / m
    passing = ranked <= thresholds
    keep = np.zeros(m, dtype=bool)
    if passing.any():
        cutoff = np.max(np.flatnonzero(passing))
        keep[order[: cutoff + 1]] = True
    return keep
