"""The derivatives history store: `.athena/derivs.duckdb`, its own file so a long load never holds up the market database.

Tables: `derivs_days` (each day loaded, and whether the exchange had a file), `fut_bars` (the nearest futures by day), `opt_totals` (open interest by option kind and expiry) and `opt_strikes`
(open interest by strike for the nearest expiry, kept for recent days only). A day already loaded is never loaded twice."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_PATH = Path(".athena") / "derivs.duckdb"
TABLES = (
    "CREATE TABLE IF NOT EXISTS derivs_days (day DATE PRIMARY KEY, has_file BOOLEAN, futures INTEGER, totals INTEGER, strikes INTEGER)",
    "CREATE TABLE IF NOT EXISTS fut_bars (day DATE, symbol TEXT, instrument TEXT, expiry DATE, close DOUBLE, settle DOUBLE, volume DOUBLE, oi DOUBLE, chg_oi DOUBLE)",
    "CREATE TABLE IF NOT EXISTS opt_totals (day DATE, symbol TEXT, instrument TEXT, expiry DATE, option TEXT, oi DOUBLE, volume DOUBLE)",
    "CREATE TABLE IF NOT EXISTS opt_strikes (day DATE, symbol TEXT, expiry DATE, strike DOUBLE, option TEXT, oi DOUBLE, close DOUBLE)",
)


class DerivsStore:
    def __init__(self, path: str | Path = DEFAULT_PATH, read_only: bool = False):
        path = Path(path)
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path), read_only=read_only)
        if not read_only:
            for statement in TABLES:
                self._con.execute(statement)

    def __enter__(self) -> DerivsStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self._con.close()

    def days_done(self) -> set[date]:
        return {r[0] for r in self._con.execute("SELECT day FROM derivs_days").fetchall()}

    def put_day(self, day: date, reduced: dict[str, list[tuple]] | None) -> None:
        """Record a day: its reduced rows, or `None` when the exchange had no file (a holiday). The day's rows are written together, so a run stopped half way leaves nothing of it."""
        self._con.execute("BEGIN")
        try:
            if reduced is None:
                self._con.execute("INSERT OR REPLACE INTO derivs_days VALUES (?, FALSE, 0, 0, 0)", [day])
            else:
                for table, key in (("fut_bars", "futures"), ("opt_totals", "totals"), ("opt_strikes", "strikes")):
                    self._con.execute(f"DELETE FROM {table} WHERE day = ?", [day])
                    if reduced[key]:
                        width = len(reduced[key][0]) + 1
                        self._con.executemany(f"INSERT INTO {table} VALUES ({', '.join(['?'] * width)})", [(day, *row) for row in reduced[key]])
                self._con.execute("INSERT OR REPLACE INTO derivs_days VALUES (?, TRUE, ?, ?, ?)", [day, len(reduced["futures"]), len(reduced["totals"]), len(reduced["strikes"])])
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise

    def futures(self, symbol: str, instrument: str = "FUTSTK") -> list[tuple[date, float, float, float]]:
        """The symbol's futures by day as one continuous series: `(day, close, oi, volume)`, oldest first. Each day-to-day change is measured on the contracts held on both days (see
        `continuous`), so a roll from one expiry to the next is not a move; the last day's close is the nearest expiry's own and its open interest the total over the held expiries."""
        rows = self._con.execute(
            "SELECT day, expiry, close, oi, volume FROM fut_bars WHERE symbol = ? AND instrument = ? ORDER BY day, expiry", [symbol, instrument]
        ).fetchall()
        return continuous(rows)

    def futures_all(self, instrument: str = "FUTSTK", since: date | None = None) -> dict[str, list[tuple[date, float, float, float]]]:
        """Every symbol's continuous futures series from one scan of the table (`futures` once per symbol scans it again each time). With `since`, only the days from then on: a caller that
        reads the last few weeks (the board) does not chain six years of history. The last days of the series come out the same either way, since each day's change is a ratio and the chain is
        scaled to the last day."""
        grouped: dict[str, list[tuple]] = {}
        for symbol, day, expiry, close, oi, volume in self._con.execute(
            "SELECT symbol, day, expiry, close, oi, volume FROM fut_bars WHERE instrument = ? AND day >= ? ORDER BY symbol, day, expiry", [instrument, since or date.min]
        ).fetchall():
            grouped.setdefault(symbol, []).append((day, expiry, close, oi, volume))
        return {symbol: continuous(rows) for symbol, rows in grouped.items()}

    def option_rows_all(self, since: date) -> dict[str, list[tuple[date, date, float, str, float]]]:
        """Every symbol's nearest-expiry strikes since a day, from one scan of the table."""
        grouped: dict[str, list[tuple]] = {}
        for symbol, day, expiry, strike, option, oi in self._con.execute("SELECT symbol, day, expiry, strike, option, oi FROM opt_strikes WHERE day >= ? ORDER BY symbol, day", [since]).fetchall():
            grouped.setdefault(symbol, []).append((day, expiry, strike, option, oi))
        return grouped

    def option_rows(self, symbol: str, since: date) -> list[tuple[date, date, float, str, float]]:
        """`(day, expiry, strike, option, oi)` for the nearest-expiry strikes held since a day."""
        return self._con.execute("SELECT day, expiry, strike, option, oi FROM opt_strikes WHERE symbol = ? AND day >= ? ORDER BY day", [symbol, since]).fetchall()

    def latest_day(self) -> date | None:
        """The last day with futures rows (a holiday recorded after it does not count)."""
        return self._con.execute("SELECT max(day) FROM fut_bars").fetchone()[0]

    def coverage(self) -> dict[str, Any]:
        row = self._con.execute("SELECT count(*), min(day), max(day), sum(CASE WHEN has_file THEN 1 ELSE 0 END) FROM derivs_days").fetchone()
        return {"days": row[0], "first": row[1], "last": row[2], "with_file": row[3]}

    def symbols(self, instrument: str = "FUTSTK") -> Sequence[str]:
        return [r[0] for r in self._con.execute("SELECT DISTINCT symbol FROM fut_bars WHERE instrument = ? ORDER BY symbol", [instrument]).fetchall()]


def continuous(rows: Sequence[tuple[date, date, float, float, float]]) -> list[tuple[date, float, float, float]]:
    """A roll-free series from `(day, expiry, close, oi, volume)` rows, oldest first.

    The exchange file holds the nearest two expiries. Taking the nearest one each day makes the series jump at every roll (open interest 83M, 58M, 26M, 11M over the expiry week, then
    145M when the next month becomes the nearest), and a detector reads that as a move. Instead each day's change is taken over the expiries present on both that day and the one
    before: the close's change on the nearest of them, the open interest's change over their total. The changes are chained into a level and scaled so the last day matches what the
    exchange reported (the nearest expiry's close, the total open interest). A day with nothing in common with the one before it carries no change."""
    days: dict[date, dict[date, tuple[float, float, float]]] = {}
    for day, expiry, close, oi, volume in rows:
        days.setdefault(day, {})[expiry] = (close, oi, volume)
    order = sorted(days)
    if not order:
        return []
    price, held = [1.0], [1.0]
    for before, today in zip(order, order[1:]):
        common = sorted(set(days[before]) & set(days[today]))
        p = o = 1.0
        if common:
            first = common[0]
            if days[before][first][0] > 0:
                p = days[today][first][0] / days[before][first][0]
            base = sum(days[before][e][1] for e in common)
            if base > 0:
                o = sum(days[today][e][1] for e in common) / base
        price.append(price[-1] * p)
        held.append(held[-1] * o)
    last = days[order[-1]]
    nearest = min(last)
    price_scale = last[nearest][0] / price[-1]
    total = sum(v[1] for v in last.values())
    oi_scale = total / held[-1] if held[-1] > 0 else 0.0
    return [(day, price[i] * price_scale, held[i] * oi_scale, days[day][min(days[day])][2]) for i, day in enumerate(order)]
