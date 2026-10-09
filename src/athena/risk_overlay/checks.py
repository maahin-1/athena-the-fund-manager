from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from athena.contracts import Bar, InsufficientData
from athena.metrics import stats
from athena.risk_overlay.model import (
    BREACH, LABELS, MEASURES, OK, UNCHECKED, WARN, Finding, Limit, Overlay,
)

WINDOW = stats.TRADING_DAYS + 1  # 253 closes: 252 daily returns, the same window as the metrics packet
LIQUIDITY_DAYS = 20
MONEY_MEASURES = ("position", "concentration", "liquidity")


@dataclass(frozen=True)
class Figures:
    """What the stock's own price history says about its risk. A figure that cannot be computed is None, with the reason."""

    volatility: float | None = None
    drawdown: float | None = None  # a positive size
    var_95: float | None = None
    cvar_95: float | None = None
    traded_value: float | None = None  # median of close x volume over the last 20 bars, in rupees
    reasons: dict[str, str] = field(default_factory=dict)
    window: str = ""


def figures_from_bars(bars: Sequence[Bar]) -> Figures:
    closes = [bar.close for bar in bars][-WINDOW:]
    reasons: dict[str, str] = {}
    values: dict[str, float] = {}
    returns = [later / earlier - 1.0 for earlier, later in zip(closes, closes[1:])]
    for name, compute, data in (
        ("volatility", stats.annualized_volatility, returns),
        ("var_95", stats.value_at_risk, returns),
        ("cvar_95", stats.expected_shortfall, returns),
        ("drawdown", lambda levels: -stats.max_drawdown(levels), closes),
    ):
        try:
            values[name] = compute(data)
        except InsufficientData as exc:
            reasons[name] = str(exc)
    recent = [(bar.close, bar.volume) for bar in bars][-LIQUIDITY_DAYS:]
    traded = statistics.median(close * volume for close, volume in recent) if recent else 0.0
    if traded > 0:
        values["traded_value"] = traded
    else:
        reasons["liquidity"] = "there is no volume data"
    return Figures(window=f"last {len(returns)} daily returns", reasons=reasons, **values)


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _index(value: float) -> str:
    return f"{value:.3f}"


FORMAT = {"concentration": _index}


def _status(value: float, limit: Limit) -> str:
    return BREACH if value >= limit.hard else WARN if value >= limit.warn else OK


def _finding(check: str, value: float | None, limit: Limit, reason: str = "", detail: str = "") -> Finding:
    label, show = LABELS[check], FORMAT.get(check, _percent)
    if value is None:
        return Finding(check, UNCHECKED, None, limit.warn, limit.hard, f"{label} not checked: {reason}.")
    status = _status(value, limit)
    if status == BREACH:
        text = f"{label} {show(value)} is at or above your hard limit of {show(limit.hard)}."
    elif status == WARN:
        text = f"{label} {show(value)} is at or above your warning level of {show(limit.warn)} (hard limit {show(limit.hard)})."
    else:
        text = f"{label} {show(value)} is within your limits (warning at {show(limit.warn)})."
    return Finding(check, status, value, limit.warn, limit.hard, text + (f" {detail}" if detail else ""))


def _weights(overlay: Overlay, symbol: str) -> dict[str, float]:
    """Portfolio weights after the intended buy."""
    held: dict[str, float] = {}
    for holding in overlay.holdings:
        held[holding.symbol] = held.get(holding.symbol, 0.0) + holding.value
    held[symbol] = held.get(symbol, 0.0) + (overlay.amount or 0.0)
    total = sum(held.values())
    return {name: value / total for name, value in held.items()}


def run_checks(figures: Figures, overlay: Overlay, symbol: str) -> tuple[Finding, ...]:
    """One finding for every check the profile switches on, in the order of MEASURES."""
    findings: list[Finding] = []
    for check in MEASURES:
        limit = overlay.profile.limits.get(check)
        if limit is None:
            continue
        value: float | None
        reason = detail = ""
        if check in ("volatility", "drawdown", "var_95", "cvar_95"):
            value = getattr(figures, check)
            reason = figures.reasons.get(check, "")
        elif not overlay.amount:
            value, reason = None, "no amount to invest was given"
        elif check == "liquidity":
            value = overlay.amount / figures.traded_value if figures.traded_value else None
            reason = figures.reasons.get("liquidity", "")
        elif not overlay.holdings:
            value, reason = None, "no holdings were given"
        else:
            weights = _weights(overlay, symbol)
            if check == "position":
                value = weights[symbol]
            else:
                value = sum(weight**2 for weight in weights.values())
                largest = max(weights, key=lambda name: weights[name])
                detail = f"The largest holding would be {largest} at {_percent(weights[largest])}."
        findings.append(_finding(check, value, limit, reason, detail))
    return tuple(findings)
