from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from athena.backtest.adjust import adjust_for_splits, adjustment_note
from athena.contracts import Bar, InsufficientData
from athena.metrics import stats
from athena.risk_overlay.model import (
    BREACH, LABELS, MEASURES, OK, UNCHECKED, WARN, Finding, Limit, Overlay,
)

WINDOW = stats.TRADING_DAYS + 1  # 253 closes: 252 daily returns, the same window as the metrics packet
MIN_RETURNS = 126  # half a year: fewer daily returns than this is not a risk check at all
LIQUIDITY_DAYS = 20
STOCK_MEASURES = ("volatility", "drawdown", "var_95", "cvar_95")
PRICE_MEASURES = (*STOCK_MEASURES, "liquidity")  # what needs the price download
MONEY_MEASURES = ("position", "concentration", "liquidity")
GAPS = "the price history has gaps"


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
    returns: int | None = None  # how many daily returns the stock figures cover; None when not measured from bars
    adjustment_note: str = ""  # the sentence that says the prices were adjusted for splits or bonuses, if they were


def _clean(bars: Sequence[Bar]) -> list[Bar]:
    """Only bars with a finite, positive close and a finite volume: a missing or zero price is a gap, never a price. A
    bad open is replaced by the close so the split detection never trips on it."""
    kept: list[Bar] = []
    for bar in bars:
        if not (math.isfinite(bar.close) and bar.close > 0 and math.isfinite(bar.volume)):
            continue
        if not (math.isfinite(bar.open) and bar.open > 0):
            bar = replace(bar, open=bar.close)
        kept.append(bar)
    return kept


def figures_from_bars(bars: Sequence[Bar]) -> Figures:
    """The figures from clean bars adjusted for splits and bonuses (the main price source is unadjusted, so a 1:1 bonus
    would otherwise read as a fall of half in one day)."""
    adjusted, adjustments = adjust_for_splits(_clean(bars))
    closes = [bar.close for bar in adjusted][-WINDOW:]
    reasons: dict[str, str] = {}
    values: dict[str, float] = {}
    returns = [later / earlier - 1.0 for earlier, later in zip(closes, closes[1:])]
    if len(returns) < MIN_RETURNS:
        for name in STOCK_MEASURES:
            reasons[name] = f"only {len(returns)} daily returns; at least {MIN_RETURNS} are needed for a risk check"
    else:
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
    recent = [(bar.close, bar.volume) for bar in adjusted][-LIQUIDITY_DAYS:]
    traded = statistics.median(close * volume for close, volume in recent) if recent else 0.0
    if 0 < traded < math.inf:
        values["traded_value"] = traded
    else:
        reasons["liquidity"] = "there is no volume data"
    return Figures(
        window=f"last {len(returns)} daily returns", reasons=reasons, returns=len(returns),
        adjustment_note=adjustment_note(adjustments) or "", **values,
    )


def _percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _index(value: float) -> str:
    return f"{value:.3f}"


FORMAT = {"concentration": _index}


def _status(value: float, limit: Limit) -> str:
    return BREACH if value >= limit.hard else WARN if value >= limit.warn else OK


def _finding(check: str, value: float | None, limit: Limit, reason: str = "", detail: str = "", span: str = "") -> Finding:
    """`span` goes inside the first sentence, before its full stop; `detail` is a sentence of its own after it."""
    label, show = LABELS[check], FORMAT.get(check, _percent)
    if value is not None and not math.isfinite(value):
        value, reason = None, GAPS  # a figure that is not a number must never read as within the limits
    if value is None:
        return Finding(check, UNCHECKED, None, limit.warn, limit.hard, f"{label} not checked: {reason}.")
    status = _status(value, limit)
    if status == BREACH:
        text = f"{label} {show(value)} is at or above your hard limit of {show(limit.hard)}"
    elif status == WARN:
        text = f"{label} {show(value)} is at or above your warning level of {show(limit.warn)} (hard limit {show(limit.hard)})"
    else:
        text = f"{label} {show(value)} is within your limits (warning at {show(limit.warn)})"
    return Finding(check, status, value, limit.warn, limit.hard, f"{text}{span}." + (f" {detail}" if detail else ""))


def _span(figures: Figures) -> str:
    """Said on every stock figure measured over less than a year of daily returns."""
    if figures.returns is not None and MIN_RETURNS <= figures.returns < stats.TRADING_DAYS:
        return f" (measured over the {figures.window}, less than a year)"
    return ""


def _weights(overlay: Overlay, symbol: str) -> dict[str, float] | None:
    """Portfolio weights after the intended buy, or None when the values are too large to add up."""
    held: dict[str, float] = {}
    for holding in overlay.holdings:
        held[holding.symbol] = held.get(holding.symbol, 0.0) + holding.value
    held[symbol] = held.get(symbol, 0.0) + (overlay.amount or 0.0)
    total = sum(held.values())
    if not math.isfinite(total) or total <= 0:
        return None
    return {name: value / total for name, value in held.items()}


def run_checks(figures: Figures, overlay: Overlay, symbol: str) -> tuple[Finding, ...]:
    """One finding for every check the profile switches on, in the order of MEASURES."""
    findings: list[Finding] = []
    for check in MEASURES:
        limit = overlay.profile.limits.get(check)
        if limit is None:
            continue
        value: float | None
        reason = detail = span = ""
        if check in STOCK_MEASURES:
            value, span = getattr(figures, check), _span(figures)
            reason = figures.reasons.get(check, "")
        elif not overlay.amount:
            value, reason = None, "no amount to invest was given"
        elif check == "liquidity":
            traded = figures.traded_value
            value = overlay.amount / traded if traded and math.isfinite(traded) else None
            reason = figures.reasons.get("liquidity", "") or "the daily traded value cannot be measured"
        elif not overlay.holdings:
            value, reason = None, "no holdings were given"
        elif (weights := _weights(overlay, symbol)) is None:
            value, reason = None, "the holdings and the amount are too large to add up"
        elif check == "position":
            value = weights[symbol]
        else:
            value = sum(weight**2 for weight in weights.values())
            largest = max(weights, key=lambda name: weights[name])
            detail = f"The largest holding would be {largest} at {_percent(weights[largest])}."
        findings.append(_finding(check, value, limit, reason, detail, span))
    return tuple(findings)
