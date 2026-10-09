from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from athena.contracts import InsufficientData

TRADING_DAYS = 252
MIN_OBS = 20


def _array(values: Sequence[float], name: str, min_obs: int) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or len(array) < min_obs:
        raise InsufficientData(f"{name} needs at least {min_obs} observations, got {len(array)}")
    return array


def _paired(a: Sequence[float], b: Sequence[float], name: str, min_obs: int) -> tuple[np.ndarray, np.ndarray]:
    first, second = _array(a, name, min_obs), _array(b, name, min_obs)
    if len(first) != len(second):
        raise ValueError(f"{name} needs equal-length series, got {len(first)} and {len(second)}")
    return first, second


def annualized_volatility(returns: Sequence[float], periods: int = TRADING_DAYS, min_obs: int = MIN_OBS) -> float:
    values = _array(returns, "volatility", min_obs)
    return float(np.std(values, ddof=1) * math.sqrt(periods))


def max_drawdown(levels: Sequence[float], min_obs: int = 2) -> float:
    """Worst peak-to-trough fall as a negative fraction (0.0 if the series never falls)."""
    values = _array(levels, "max drawdown", min_obs)
    peaks = np.maximum.accumulate(values)
    return float(np.min(values / peaks - 1.0))


def beta(asset: Sequence[float], benchmark: Sequence[float], min_obs: int = MIN_OBS) -> float:
    a, b = _paired(asset, benchmark, "beta", min_obs)
    variance = float(np.var(b, ddof=1))
    if variance == 0.0:
        raise InsufficientData("beta is undefined: the benchmark has zero variance")
    return float(np.cov(a, b, ddof=1)[0, 1] / variance)


def jensen_alpha(
    asset: Sequence[float],
    benchmark: Sequence[float],
    riskfree: Sequence[float],
    periods: int = TRADING_DAYS,
    min_obs: int = MIN_OBS,
) -> float:
    """Annualised Jensen's alpha from per-period returns (beta fitted on excess returns)."""
    a, b = _paired(asset, benchmark, "alpha", min_obs)
    _, r = _paired(asset, riskfree, "alpha", min_obs)
    excess_a, excess_b = a - r, b - r
    slope = beta(excess_a, excess_b, min_obs)
    return float((np.mean(excess_a) - slope * np.mean(excess_b)) * periods)


def sharpe(
    asset: Sequence[float], riskfree: Sequence[float], periods: int = TRADING_DAYS, min_obs: int = MIN_OBS
) -> float:
    a, r = _paired(asset, riskfree, "sharpe", min_obs)
    excess = a - r
    deviation = float(np.std(excess, ddof=1))
    if deviation == 0.0:
        raise InsufficientData("sharpe is undefined: excess returns have zero variance")
    return float(np.mean(excess) / deviation * math.sqrt(periods))


def tracking_error(
    asset: Sequence[float], index: Sequence[float], periods: int = TRADING_DAYS, min_obs: int = MIN_OBS
) -> float:
    a, i = _paired(asset, index, "tracking error", min_obs)
    return float(np.std(a - i, ddof=1) * math.sqrt(periods))


def tracking_difference(asset_levels: Sequence[float], index_levels: Sequence[float]) -> float:
    """Cumulative return of the asset minus cumulative return of the index over the same window."""
    a, i = _paired(asset_levels, index_levels, "tracking difference", 2)
    return float((a[-1] / a[0] - 1.0) - (i[-1] / i[0] - 1.0))


def value_at_risk(returns: Sequence[float], confidence: float = 0.95, min_obs: int = MIN_OBS) -> float:
    """Historical one-period VaR: the loss, as a positive fraction, that the worst `1 - confidence` of the returns
    reach (the percentile, linearly interpolated). 0.0 when even that tail was a gain; nan when a return is missing
    (nan), never a zero loss."""
    values = _array(returns, "value at risk", min_obs)
    if np.isnan(values).any():
        return math.nan
    return max(0.0, float(-np.percentile(values, (1.0 - confidence) * 100.0)))


def expected_shortfall(returns: Sequence[float], confidence: float = 0.95, min_obs: int = MIN_OBS) -> float:
    """Historical CVaR: the average loss over the returns at or below the VaR cutoff, as a positive fraction; nan when
    a return is missing (nan)."""
    values = _array(returns, "expected shortfall", min_obs)
    if np.isnan(values).any():
        return math.nan
    cutoff = np.percentile(values, (1.0 - confidence) * 100.0)
    return max(0.0, float(-np.mean(values[values <= cutoff])))
