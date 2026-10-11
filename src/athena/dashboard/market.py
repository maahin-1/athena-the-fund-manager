"""The market screen: every stock and ETF in the warehouse in one table to filter and sort, with a chart for the one you pick. It reads
only the warehouse (no model call, no download), says how old its data is, and never shows a missing figure as zero."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

# column -> (label, how to show it); "pct" columns are stored as fractions and shown as percent
COLUMNS: dict[str, tuple[str, str]] = {
    "symbol": ("Symbol", "text"), "name": ("Name", "text"), "kind": ("Type", "text"), "sector": ("Sector", "text"),
    "close": ("Close", "price"), "ret_1d": ("1D", "pct"), "ret_1m": ("1M", "pct"), "ret_3m": ("3M", "pct"), "ret_6m": ("6M", "pct"),
    "ret_1y": ("1Y", "pct"), "from_52w_high": ("From 52w high", "pct"), "from_52w_low": ("Above 52w low", "pct"), "rsi_14": ("RSI", "number"),
    "trend": ("Trend", "text"), "sma50_vs_200": ("50d vs 200d", "pct"), "market_cap_cr": ("Mkt cap (Rs cr)", "number0"),
    "traded_value_cr": ("Traded (Rs cr/day)", "number"), "pe": ("P/E", "number"), "pb": ("P/B", "number"), "roe": ("ROE", "pct"),
    "net_margin": ("Net margin", "pct"), "debt_to_equity": ("Debt/Equity", "number"), "dividend_yield": ("Dividend yield", "pct"),
    "earnings_yield": ("Earnings yield", "pct"), "revenue_growth": ("Sales growth (qtr, yoy)", "pct"), "flags": ("Flags", "number0"),
}
DEFAULT_COLUMNS = ("symbol", "name", "close", "ret_1m", "ret_1y", "from_52w_high", "pe", "pb", "roe", "market_cap_cr", "trend", "flags")
# the numeric filters: column -> (label, unit shown, factor from shown unit to stored unit)
RANGE_FILTERS: dict[str, tuple[str, str, float]] = {
    "pe": ("P/E", "x", 1.0), "pb": ("P/B", "x", 1.0), "roe": ("ROE", "%", 0.01), "ret_1y": ("1-year return", "%", 0.01),
    "from_52w_high": ("From 52-week high", "%", 0.01), "market_cap_cr": ("Market cap", "Rs cr", 1.0), "traded_value_cr": ("Traded per day", "Rs cr", 1.0),
    "dividend_yield": ("Dividend yield", "%", 0.01), "debt_to_equity": ("Debt/Equity", "x", 1.0),
}
UP_TRENDS = ("aligned_up",)


@dataclass(frozen=True)
class Preset:
    label: str
    note: str
    ranges: Mapping[str, tuple[float | None, float | None]]  # in stored units
    kinds: tuple[str, ...] = ("stock",)
    trends: tuple[str, ...] = ()


PRESETS = {
    "Value": Preset("Value", "Cheap against earnings and book, and earning well: P/E under 15, P/B under 3, ROE over 15%.",
                    {"pe": (0.0, 15.0), "pb": (0.0, 3.0), "roe": (0.15, None)}),
    "Quality compounders": Preset("Quality compounders", "ROE over 20%, low debt (Debt/Equity under 0.5), market cap over Rs 5,000 cr.",
                                  {"roe": (0.20, None), "debt_to_equity": (0.0, 0.5), "market_cap_cr": (5000.0, None)}),
    "Momentum": Preset("Momentum", "Up over 20% in a year and within 10% of the 52-week high, with the trend aligned up.",
                       {"ret_1y": (0.20, None), "from_52w_high": (-0.10, None)}, trends=UP_TRENDS),
    "Beaten down": Preset("Beaten down", "More than 40% below the 52-week high. A fall is not a reason to buy; look at why.",
                          {"from_52w_high": (None, -0.40), "traded_value_cr": (1.0, None)}),
    "Dividend payers": Preset("Dividend payers", "Dividend yield over 3% on stocks that trade at least Rs 1 cr a day.",
                              {"dividend_yield": (0.03, None), "traded_value_cr": (1.0, None)}),
    "Liquid large caps": Preset("Liquid large caps", "Market cap over Rs 50,000 cr.", {"market_cap_cr": (50000.0, None)}),
}


def filter_rows(
    rows: Sequence[Mapping[str, Any]],
    kinds: Sequence[str] | None = None,
    text: str = "",
    ranges: Mapping[str, tuple[float | None, float | None]] | None = None,
    trends: Sequence[str] = (),
    sectors: Sequence[str] = (),
    clean_only: bool = False,
) -> list[Mapping[str, Any]]:
    """The rows that pass every filter. A row with no figure in a column that has a limit does not pass: a missing P/E is not a P/E
    of zero, and it is not shown as if it met the limit."""
    needle = text.strip().lower()
    out = []
    for row in rows:
        if kinds is not None and row["kind"] not in kinds:
            continue
        if needle and needle not in str(row["symbol"]).lower() and needle not in str(row.get("name") or "").lower():
            continue
        if trends and row.get("trend") not in trends:
            continue
        if sectors and row.get("sector") not in sectors:
            continue
        if clean_only and row.get("flags"):
            continue
        if any(not _within(row.get(column), low, high) for column, (low, high) in (ranges or {}).items()):
            continue
        out.append(row)
    return out


def _within(value: Any, low: float | None, high: float | None) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def sort_rows(rows: Sequence[Mapping[str, Any]], column: str, descending: bool = True) -> list[Mapping[str, Any]]:
    """Sorted by `column`, rows with no figure last whichever way it runs."""
    have = [row for row in rows if row.get(column) is not None]
    lacking = [row for row in rows if row.get(column) is None]
    return sorted(have, key=lambda row: row[column], reverse=descending) + list(lacking)


def data_note(last_day: date | None, today: date, statements: int, stocks: int, flagged: int) -> str:
    """The sentence at the top of the screen saying how old the data is and what is missing."""
    if last_day is None:
        return "No prices in the database yet. Run `python -m athena.warehouse.build`."
    age = (today - last_day).days
    when = "today" if age == 0 else f"{age} day{'s' if age != 1 else ''} old"
    share = f"{statements:,} of {stocks:,} stocks have statements, so P/E, P/B and ROE are blank for the rest" if stocks else "no stocks loaded"
    return f"Prices to {last_day:%d %b %Y} ({when}). {share}. {flagged:,} symbols carry a data flag."


def display_value(value: Any, style: str) -> Any:
    """A cell for the table: numbers kept numeric (so sorting works) and scaled for display, a missing figure left empty."""
    if value is None:
        return None
    if style == "pct":
        return value * 100
    return value


def column_format(style: str) -> str | None:
    return {"pct": "%.1f%%", "price": "%.2f", "number": "%.1f", "number0": "%,.0f"}.get(style)


def load(path: Path, wait: float = 30.0) -> dict[str, Any]:
    """Everything the screen needs, read in one short read-only visit to the warehouse so a running job's writes are not blocked."""
    from athena.warehouse.warehouse import Warehouse

    with Warehouse(path, read_only=True, wait_seconds=wait) as warehouse:
        rows = warehouse.screen_rows()
        status = warehouse.status()
        stocks = len(warehouse.universe("stock"))
    return {"rows": rows, "status": status, "stocks": stocks}


def chart_for(path: Path, symbol: str, days: int = 400):
    """The chart and the facts for one symbol: adjusted candles with the 50 and 200-day averages, and the flags on its packets."""
    from athena.dashboard.charts import build_chart
    from athena.technicals.candles import candles_from_bars
    from athena.technicals.indicators import Selected, default_params
    from athena.warehouse.history import adjusted_history
    from athena.warehouse.warehouse import Warehouse

    with Warehouse(path, read_only=True, wait_seconds=30) as warehouse:
        history = adjusted_history(warehouse)
        bars = history(symbol)
        notes = history.notes(symbol)
        flags: list[str] = []
        for kind in ("technical", "fundamentals"):
            held = warehouse.packet(symbol, kind)
            if held:
                flags += [f for f in held["flags"] if f not in flags]
    candles = candles_from_bars(bars)[-days:]
    selection = [Selected(f"sma-{n}", "sma", {**default_params("sma"), "length": float(n)}) for n in (50, 200)]
    return build_chart(candles, selection, symbol), flags, notes


# ---- the Streamlit page
SEARCH_KEY, KINDS_KEY, PRESET_KEY, SECTORS_KEY, TREND_KEY, CLEAN_KEY = "mkt_search", "mkt_kinds", "mkt_preset", "mkt_sectors", "mkt_trend", "mkt_clean"
SORT_KEY, DESC_KEY, COLS_KEY, TABLE_KEY = "mkt_sort", "mkt_desc", "mkt_cols", "mkt_table"
NO_PRESET = "(none)"
SHOWN_ROWS = 600


def _limit_key(column: str, side: str) -> str:
    return f"mkt_{side}_{column}"


def _shown(value: float, factor: float) -> str:
    shown = value / factor
    return f"{shown:g}"


def _apply_preset() -> None:
    chosen = st.session_state.get(PRESET_KEY, NO_PRESET)
    for column in RANGE_FILTERS:
        for side in ("min", "max"):
            st.session_state[_limit_key(column, side)] = ""
    if chosen == NO_PRESET:
        st.session_state[TREND_KEY] = False
        return
    preset = PRESETS[chosen]
    st.session_state[KINDS_KEY] = list(preset.kinds)
    st.session_state[TREND_KEY] = bool(preset.trends)
    for column, (low, high) in preset.ranges.items():
        factor = RANGE_FILTERS[column][2]
        if low is not None:
            st.session_state[_limit_key(column, "min")] = _shown(low, factor)
        if high is not None:
            st.session_state[_limit_key(column, "max")] = _shown(high, factor)


def _read_ranges() -> tuple[dict[str, tuple[float | None, float | None]], list[str]]:
    ranges: dict[str, tuple[float | None, float | None]] = {}
    problems: list[str] = []
    for column, (label, _unit, factor) in RANGE_FILTERS.items():
        limits: list[float | None] = []
        for side in ("min", "max"):
            text = str(st.session_state.get(_limit_key(column, side), "")).strip()
            if not text:
                limits.append(None)
                continue
            try:
                limits.append(float(text) * factor)
            except ValueError:
                problems.append(f"{label} {side}: '{text}' is not a number")
                limits.append(None)
        if limits != [None, None]:
            ranges[column] = (limits[0], limits[1])
    return ranges, problems


def _table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]):
    import pandas as pd
    frame = pd.DataFrame([{COLUMNS[c][0]: display_value(row.get(c), COLUMNS[c][1]) for c in columns} for row in rows])
    config = {}
    for column in columns:
        label, style = COLUMNS[column]
        if style in ("pct", "price", "number", "number0"):
            config[label] = st.column_config.NumberColumn(label, format=column_format(style))
    return frame, config


def render_market(path: Path | str) -> None:
    from athena.contracts import AthenaError

    path = Path(path)
    if not path.exists():
        st.info("The market database has not been built yet. Run `python -m athena.warehouse.build`.")
        return
    try:
        data = _cached_load(str(path), path.stat().st_mtime)
    except Exception as exc:  # the file may be busy with a long job's write for a moment
        st.warning(f"The market database is busy or unreadable right now ({type(exc).__name__}). Try again in a minute.")
        return
    rows, status = data["rows"], data["status"]
    flagged = sum(1 for row in rows if row["flags"])
    st.caption(data_note(status["last_loaded_day"], date.today(), status["statements"].get("ok", 0), data["stocks"], flagged))
    if not rows:
        st.info("No screen rows yet. Run `python -m athena.warehouse.build` to compute them.")
        return

    sectors = sorted({row["sector"] for row in rows if row.get("sector")})
    with st.expander("Filters", expanded=True):
        top = st.columns([2, 2, 2])
        top[0].selectbox("Preset", [NO_PRESET, *PRESETS], key=PRESET_KEY, on_change=_apply_preset)
        top[1].multiselect("Type", ["stock", "etf"], default=["stock"], key=KINDS_KEY)
        top[2].text_input("Search symbol or name", key=SEARCH_KEY)
        chosen = st.session_state.get(PRESET_KEY, NO_PRESET)
        if chosen != NO_PRESET:
            st.caption(PRESETS[chosen].note)
        grid = st.columns(3)
        for i, (column, (label, unit, _)) in enumerate(RANGE_FILTERS.items()):
            with grid[i % 3]:
                low, high = st.columns(2)
                low.text_input(f"{label} min ({unit})", key=_limit_key(column, "min"))
                high.text_input(f"{label} max ({unit})", key=_limit_key(column, "max"))
        more = st.columns(3)
        more[0].checkbox("Trend aligned up only", key=TREND_KEY)
        more[1].checkbox("Hide symbols with a data flag", key=CLEAN_KEY)
        more[2].multiselect("Sector", sectors, key=SECTORS_KEY)
    ranges, problems = _read_ranges()
    for problem in problems:
        st.warning(problem)

    kinds = st.session_state.get(KINDS_KEY, ["stock"]) or ["stock", "etf"]
    matched = filter_rows(
        rows, kinds, st.session_state.get(SEARCH_KEY, ""), ranges, UP_TRENDS if st.session_state.get(TREND_KEY) else (),
        st.session_state.get(SECTORS_KEY, []), bool(st.session_state.get(CLEAN_KEY)),
    )
    options = [column for column in COLUMNS if COLUMNS[column][1] != "text" or column in ("symbol", "name")]
    sort_cols = st.columns([2, 1, 4])
    sort_by = sort_cols[0].selectbox("Sort by", options, index=options.index("market_cap_cr"), format_func=lambda c: COLUMNS[c][0], key=SORT_KEY)
    descending = sort_cols[1].checkbox("High to low", value=True, key=DESC_KEY)
    columns = sort_cols[2].multiselect("Columns", list(COLUMNS), default=list(DEFAULT_COLUMNS), format_func=lambda c: COLUMNS[c][0], key=COLS_KEY)
    columns = columns or list(DEFAULT_COLUMNS)
    ordered = sort_rows(matched, sort_by, descending)
    st.write(f"**{len(matched):,}** of {len(rows):,} symbols match" + (f" (showing the first {SHOWN_ROWS})" if len(ordered) > SHOWN_ROWS else ""))
    shown = ordered[:SHOWN_ROWS]
    if not shown:
        st.info("Nothing matches. Loosen a limit: a stock with no figure for a limited column is left out.")
        return
    frame, config = _table(shown, columns)
    event = st.dataframe(frame, hide_index=True, column_config=config, on_select="rerun", selection_mode="single-row", key=TABLE_KEY, height=420)
    selected = event.selection.rows if event and event.selection else []
    if selected:
        symbol = shown[selected[0]]["symbol"]
        try:
            figure, flags, notes = chart_for(path, symbol)
        except (AthenaError, ValueError) as exc:
            st.warning(f"Cannot chart {symbol}: {exc}")
            return
        st.subheader(f"{symbol}, {shown[selected[0]].get('name') or ''}")
        st.plotly_chart(figure, use_container_width=True)
        for line in [*flags, *notes]:
            st.caption(line)
    else:
        st.caption("Click a row to see its chart.")


@st.cache_data(ttl=300, show_spinner=False)
def _cached_load(path: str, mtime: float) -> dict[str, Any]:
    """The screen's data, kept for five minutes or until the database file changes (`mtime` is part of the key)."""
    return load(Path(path))
