from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from athena.contracts import InsufficientData
from athena.metrics import stats
from athena.metrics.series import (
    Series,
    common_dates,
    levels_on,
    returns_from_levels,
    riskfree_returns,
)

DEFAULT_WINDOW = stats.TRADING_DAYS + 1  # 253 levels = 252 daily returns
RISKFREE_NAME = "NIFTY 1D RATE INDEX"
TRACKING_NOTE = (
    "ETF exchange closing prices against the index total-return series; includes premium/discount "
    "noise, so it overstates NAV-based tracking error (free NAV is not available)"
)


def _window_text(days: list[date]) -> str:
    return f"{len(days) - 1} returns {days[0]}..{days[-1]}"


def build_packet(
    instrument: str,
    as_of: datetime,
    asset: Series,
    benchmark: Series | None = None,
    benchmark_name: str | None = None,
    riskfree: Series | None = None,
    tracking_index: Series | None = None,
    tracking_index_name: str | None = None,
    window: int = DEFAULT_WINDOW,
) -> dict[str, Any]:
    """Metrics packet (TRD section 3): every figure a specialist may cite, computed in code.

    `asset` and `tracking_index` are price/NAV and total-return levels; `benchmark` should be a price
    index for stocks; `riskfree` is an accrual index (Nifty 1D Rate Index). Each metric uses the
    last `window` dates its own inputs share. A metric that cannot be computed is listed in
    `missing` with a reason, never silently dropped.
    """
    metrics: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}

    def attempt(
        name: str, unit: str, inputs: list[str], needed: list[Series | None], compute: Callable, note: str | None = None
    ) -> None:
        if any(series is None for series in needed):
            reasons[name] = "required series not provided"
            return
        days = common_dates(*needed)[-window:]  # type: ignore[arg-type]
        try:
            value = compute(days)
        except InsufficientData as exc:
            reasons[name] = str(exc)
            return
        metrics[name] = {
            "value": round(float(value), 6),
            "unit": unit,
            "inputs": inputs,
            "window": _window_text(days) if len(days) > 1 else "0 returns",
            "source": "computed from daily closing levels",
        }
        if note:
            metrics[name]["note"] = note

    def asset_returns(days: list[date]) -> list[float]:
        return returns_from_levels(levels_on(asset, days))

    attempt(
        "volatility_annualized", "fraction", ["asset"], [asset],
        lambda days: stats.annualized_volatility(asset_returns(days)),
    )
    attempt(
        "max_drawdown", "fraction", ["asset"], [asset],
        lambda days: stats.max_drawdown(levels_on(asset, days)),
    )
    attempt(
        "beta", "ratio", ["asset", benchmark_name or "benchmark"], [asset, benchmark],
        lambda days: stats.beta(asset_returns(days), returns_from_levels(levels_on(benchmark, days))),
    )

    def alpha(days: list[date]) -> float:
        return stats.jensen_alpha(
            asset_returns(days),
            returns_from_levels(levels_on(benchmark, days)),  # type: ignore[arg-type]
            riskfree_returns(riskfree, days),  # type: ignore[arg-type]
        )

    attempt(
        "alpha_annualized", "fraction", ["asset", benchmark_name or "benchmark", RISKFREE_NAME],
        [asset, benchmark, riskfree], alpha,
    )

    def sharpe(days: list[date]) -> float:
        return stats.sharpe(asset_returns(days), riskfree_returns(riskfree, days))  # type: ignore[arg-type]

    attempt("sharpe", "ratio", ["asset", RISKFREE_NAME], [asset, riskfree], sharpe)
    attempt(
        "tracking_error", "fraction", ["asset", tracking_index_name or "tracking_index"],
        [asset, tracking_index],
        lambda days: stats.tracking_error(
            asset_returns(days), returns_from_levels(levels_on(tracking_index, days))  # type: ignore[arg-type]
        ),
        note=TRACKING_NOTE,
    )
    attempt(
        "tracking_difference", "fraction", ["asset", tracking_index_name or "tracking_index"],
        [asset, tracking_index],
        lambda days: stats.tracking_difference(
            levels_on(asset, days), levels_on(tracking_index, days)  # type: ignore[arg-type]
        ),
        note=TRACKING_NOTE,
    )

    return {
        "instrument": instrument,
        "as_of": as_of.isoformat(),
        "metrics": metrics,
        "missing": list(reasons),
        "missing_reasons": reasons,
    }
