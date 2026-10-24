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
        """The symbol's nearest-expiry futures by day: `(day, close, oi, volume)`, oldest first. The nearest expiry on each day is the one used."""
        return [
            (d, c, oi, v)
            for d, c, oi, v in self._con.execute(
                "SELECT day, arg_min(close, expiry), arg_min(oi, expiry), arg_min(volume, expiry) FROM fut_bars WHERE symbol = ? AND instrument = ? GROUP BY day ORDER BY day",
                [symbol, instrument],
            ).fetchall()
        ]

    def option_rows(self, symbol: str, since: date) -> list[tuple[date, date, float, str, float]]:
        """`(day, expiry, strike, option, oi)` for the nearest-expiry strikes held since a day."""
        return self._con.execute("SELECT day, expiry, strike, option, oi FROM opt_strikes WHERE symbol = ? AND day >= ? ORDER BY day", [symbol, since]).fetchall()

    def coverage(self) -> dict[str, Any]:
        row = self._con.execute("SELECT count(*), min(day), max(day), sum(CASE WHEN has_file THEN 1 ELSE 0 END) FROM derivs_days").fetchone()
        return {"days": row[0], "first": row[1], "last": row[2], "with_file": row[3]}

    def symbols(self, instrument: str = "FUTSTK") -> Sequence[str]:
        return [r[0] for r in self._con.execute("SELECT DISTINCT symbol FROM fut_bars WHERE instrument = ? ORDER BY symbol", [instrument]).fetchall()]
