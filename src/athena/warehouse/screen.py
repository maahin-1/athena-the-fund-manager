"""One flat row per stock and ETF for screens and tables: returns and the 52-week position from adjusted prices, trend and RSI from the
technical packet, valuation and quality from the fundamentals packet. A figure that cannot be computed is None, never zero."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from athena.contracts import Bar
from athena.trading_calendar import ist_date

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
    # added later (a table made before them gets them from `Warehouse`): the industry and the stock against it, then the written-rule valuation range as last recorded
    "industry_group": "VARCHAR", "pe_vs_industry": "DOUBLE", "pb_vs_industry": "DOUBLE", "roe_vs_industry": "DOUBLE",
    "value_base": "DOUBLE", "price_vs_value": "DOUBLE", "value_position": "INTEGER", "implied_return": "DOUBLE", "valued_on": "DATE",
}
FUNDAMENTAL_METRICS = {
    "market_cap_cr": "market_cap", "pe": "pe_trailing", "pb": "pb", "roe": "roe_latest", "net_margin": "net_margin_latest",
    "debt_to_equity": "debt_to_equity", "dividend_yield": "dividend_yield", "earnings_yield": "earnings_yield",
    "revenue_growth": "revenue_growth_yoy_quarter", "pe_vs_industry": "pe_vs_industry", "pb_vs_industry": "pb_vs_industry", "roe_vs_industry": "roe_vs_industry",
}
SHARE_BASIS_LIMITS = (0.01, 100.0)  # a split or bonus factor outside this is not believed


def valuation_columns(bars: Sequence[Bar], last: float, recorded: Mapping[str, Any] | None) -> dict[str, Any]:
    """The last recorded valuation range of a stock against today's close. The range was recorded in rupees per share on the day of its snapshot; a split or bonus since then is
    undone by the ratio of that day's close on today's share basis (the adjusted bars) to the close recorded with the range. Position is -1 below the range, 0 inside, 1 above.
    A range whose base is not above zero is left out. The implied return is the one recorded: it was worked out at the recorded price, not today's."""
    out: dict[str, Any] = {"value_base": None, "price_vs_value": None, "value_position": None, "implied_return": None, "valued_on": None}
    if not recorded or not recorded.get("price") or not recorded.get("base"):
        return out
    on = recorded["price_date"]
    bar = next((b for b in reversed(bars) if ist_date(b.timestamp) == on), None)
    if bar is None:
        return out
    factor = bar.close / recorded["price"]
    if not SHARE_BASIS_LIMITS[0] <= factor <= SHARE_BASIS_LIMITS[1]:
        return out
    low, base, high = (recorded[k] * factor for k in ("low", "base", "high"))
    if base <= 0:  # a share cannot be worth less than nothing: the model had no value to give, so nothing is shown
        return out
    out.update(value_base=base, price_vs_value=last / base - 1 if base > 0 else None, value_position=-1 if last < low else 1 if last > high else 0, valued_on=recorded["snap_date"])
    out["implied_return"] = recorded.get("implied_return")
    return out



def _metric(packet: Mapping[str, Any] | None, name: str) -> Any:
    return ((packet or {}).get("metrics", {}).get(name) or {}).get("value")


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def screen_row(
    symbol: str, kind: str, name: str, day: date, bars: Sequence[Bar], technical: Mapping[str, Any] | None,
    fundamentals: Mapping[str, Any] | None, flags: int, statements_age_days: int | None, recorded_value: Mapping[str, Any] | None = None,
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
    industry = (fundamentals or {}).get("industry")
    row["industry_group"] = industry.get("industry") if isinstance(industry, Mapping) else None
    row.update(valuation_columns(bars, last, recorded_value))
    return {column: row.get(column) for column in SCREEN_COLUMNS}
