"""One flat row per stock and ETF for screens and tables: returns and the 52-week position from adjusted prices, trend and RSI from the
technical packet, valuation and quality from the fundamentals packet. A figure that cannot be computed is None, never zero."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from athena.contracts import Bar

RETURN_BARS = {"ret_1d": 1, "ret_1m": 21, "ret_3m": 63, "ret_6m": 126, "ret_1y": 252}  # trading days back from the last close
WINDOW = 252  # bars in the 52-week range
VOLUME_BARS = 20
CRORE = 1e7

# column -> SQL type; the order is the order of the table
SCREEN_COLUMNS: dict[str, str] = {
    "symbol": "VARCHAR", "kind": "VARCHAR", "name": "VARCHAR", "sector": "VARCHAR", "data_as_of": "DATE", "close": "DOUBLE",
    **{name: "DOUBLE" for name in RETURN_BARS},
    "from_52w_high": "DOUBLE", "from_52w_low": "DOUBLE", "traded_value_cr": "DOUBLE", "rsi_14": "DOUBLE", "trend": "VARCHAR",
    "sma50_vs_200": "DOUBLE", "market_cap_cr": "DOUBLE", "pe": "DOUBLE", "pb": "DOUBLE", "roe": "DOUBLE", "net_margin": "DOUBLE",
    "debt_to_equity": "DOUBLE", "dividend_yield": "DOUBLE", "earnings_yield": "DOUBLE", "revenue_growth": "DOUBLE",
    "flags": "INTEGER", "statements_age_days": "INTEGER",
}
FUNDAMENTAL_METRICS = {
    "market_cap_cr": "market_cap", "pe": "pe_trailing", "pb": "pb", "roe": "roe_latest", "net_margin": "net_margin_latest",
    "debt_to_equity": "debt_to_equity", "dividend_yield": "dividend_yield", "earnings_yield": "earnings_yield",
    "revenue_growth": "revenue_growth_yoy_quarter",
}


def _metric(packet: Mapping[str, Any] | None, name: str) -> Any:
    return ((packet or {}).get("metrics", {}).get(name) or {}).get("value")


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def screen_row(
    symbol: str, kind: str, name: str, day: date, bars: Sequence[Bar], technical: Mapping[str, Any] | None,
    fundamentals: Mapping[str, Any] | None, flags: int, statements_age_days: int | None,
) -> dict[str, Any]:
    """The row for one symbol. `bars` are the adjusted bars, oldest first, the last on `day`."""
    closes = [bar.close for bar in bars]
    last = closes[-1]
    row: dict[str, Any] = {"symbol": symbol, "kind": kind, "name": name, "sector": (fundamentals or {}).get("sector"), "data_as_of": day, "close": last}
    for column, back in RETURN_BARS.items():
        row[column] = last / closes[-1 - back] - 1 if len(closes) > back else None
    year = bars[-WINDOW:]
    if len(year) >= 60:
        row["from_52w_high"] = last / max(bar.high for bar in year) - 1
        row["from_52w_low"] = last / min(bar.low for bar in year) - 1
    recent = bars[-VOLUME_BARS:]
    row["traded_value_cr"] = sum(bar.close * bar.volume for bar in recent) / len(recent) / CRORE
    row["rsi_14"] = _number(_metric(technical, "rsi_14"))
    trend = _metric(technical, "trend_alignment")
    row["trend"] = trend if isinstance(trend, str) else None
    fast, slow = _number(_metric(technical, "sma_50")), _number(_metric(technical, "sma_200"))
    row["sma50_vs_200"] = fast / slow - 1 if fast and slow else None
    for column, metric in FUNDAMENTAL_METRICS.items():
        row[column] = _number(_metric(fundamentals, metric))
    row["flags"] = flags
    row["statements_age_days"] = statements_age_days
    return {column: row.get(column) for column in SCREEN_COLUMNS}
