"""The reference database: industry statistics by source, region and date, the company-to-industry map, country premiums and dated inputs.

One DuckDB file. A loader writes in short bursts; the valuation code and the terminal's page builder open it read-only. Every write is an upsert on its key, so loading
the same workbook twice leaves the same rows."""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import AthenaError

DEFAULT_PATH = Path(".athena") / "reference.duckdb"
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS ref_industry_stat (source TEXT, region TEXT, dataset TEXT, industry TEXT, metric TEXT, value DOUBLE, n_firms INTEGER,"
    " as_of DATE, file TEXT, PRIMARY KEY (source, region, dataset, industry, metric, as_of))",
    "CREATE TABLE IF NOT EXISTS ref_company_industry (symbol TEXT PRIMARY KEY, industry TEXT, method TEXT, confidence DOUBLE, as_of DATE)",
    "CREATE TABLE IF NOT EXISTS ref_country_premium (country TEXT, region TEXT, rating TEXT, default_spread DOUBLE, total_erp DOUBLE, crp DOUBLE, as_of DATE,"
    " PRIMARY KEY (country, as_of))",
    "CREATE TABLE IF NOT EXISTS ref_rating_spread (rating TEXT, spread DOUBLE, as_of DATE, PRIMARY KEY (rating, as_of))",
    "CREATE TABLE IF NOT EXISTS ref_input (key TEXT, as_of DATE, value DOUBLE, source TEXT, note TEXT, PRIMARY KEY (key, as_of))",
)


@dataclass(frozen=True)
class InputValue:
    key: str
    as_of: date
    value: float
    source: str
    note: str


@dataclass(frozen=True)
class StatRow:
    source: str
    region: str
    dataset: str
    industry: str
    metric: str
    value: float
    n_firms: int | None
    as_of: date
    file: str


class ReferenceStore:
    def __init__(
        self, path: str | Path = DEFAULT_PATH, read_only: bool = False, tries: int = 8, pause: float = 0.5, sleep: Callable[[float], None] = time.sleep
    ):
        """A locked file (another process writing) is retried `tries` times, `pause` seconds apart, before it is reported as busy."""
        if str(path) != ":memory:" and not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(max(tries, 1)):
            try:
                self._con = duckdb.connect(str(path), read_only=read_only)
                break
            except duckdb.IOException as exc:
                if attempt >= tries - 1:
                    raise AthenaError(f"the reference store is busy: {exc}") from exc
                sleep(pause)
        if not read_only:
            for statement in SCHEMA:
                self._con.execute(statement)

    def __enter__(self) -> "ReferenceStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._con.close()

    def _bulk(self, table: str, columns: list[str], rows: Sequence[Sequence[Any]]) -> int:
        """Upsert many rows in one statement (a row-by-row insert into DuckDB takes about a millisecond each, which is a minute for the industry tables)."""
        if not rows:
            return 0
        import pandas

        frame = pandas.DataFrame(list(rows), columns=columns)
        self._con.register("incoming", frame)
        try:
            self._con.execute(f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) SELECT {', '.join(columns)} FROM incoming")
        finally:
            self._con.unregister("incoming")
        return len(rows)

    # ---- industry statistics
    def put_stats(self, rows: Iterable[StatRow]) -> int:
        data = [[r.source, r.region, r.dataset, r.industry, r.metric, r.value, r.n_firms, r.as_of, r.file] for r in rows]
        return self._bulk("ref_industry_stat", ["source", "region", "dataset", "industry", "metric", "value", "n_firms", "as_of", "file"], data)

    def latest_stat_date(self, source: str, region: str, on_or_before: date | None = None) -> date | None:
        sql = "SELECT max(as_of) FROM ref_industry_stat WHERE source = ? AND region = ?"
        args: list[Any] = [source, region]
        if on_or_before is not None:
            sql += " AND as_of <= ?"
            args.append(on_or_before)
        return self._con.execute(sql, args).fetchone()[0]

    def industry_stats(
        self, industry: str, source: str = "damodaran", region: str = "India", on_or_before: date | None = None
    ) -> dict[str, tuple[float, int | None]]:
        """`dataset.metric` -> (value, n_firms) for one industry, from the newest dataset published on or before the date; empty when there is none."""
        as_of = self.latest_stat_date(source, region, on_or_before)
        if as_of is None:
            return {}
        rows = self._con.execute(
            "SELECT dataset, metric, value, n_firms FROM ref_industry_stat WHERE source = ? AND region = ? AND industry = ? AND as_of = ?",
            [source, region, industry, as_of],
        ).fetchall()
        return {f"{dataset}.{metric}": (value, n) for dataset, metric, value, n in rows}

    def industries(self, source: str = "damodaran", region: str = "India") -> list[str]:
        rows = self._con.execute("SELECT DISTINCT industry FROM ref_industry_stat WHERE source = ? AND region = ? ORDER BY 1", [source, region]).fetchall()
        return [r[0] for r in rows]

    # ---- company map
    def put_company_industries(self, rows: Sequence[tuple[str, str, str, float, date]]) -> int:
        """Rows are (symbol, industry, method, confidence, as_of); an existing symbol is replaced."""
        return self._bulk("ref_company_industry", ["symbol", "industry", "method", "confidence", "as_of"], [list(r) for r in rows])

    def company_industry(self, symbol: str) -> tuple[str, str, float] | None:
        row = self._con.execute("SELECT industry, method, confidence FROM ref_company_industry WHERE symbol = ?", [symbol]).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def company_industries(self) -> dict[str, tuple[str, str, float]]:
        return {s: (i, m, c) for s, i, m, c in self._con.execute("SELECT symbol, industry, method, confidence FROM ref_company_industry").fetchall()}

    def company_industry_counts(self) -> dict[str, int]:
        return dict(self._con.execute("SELECT method, count(*) FROM ref_company_industry GROUP BY 1").fetchall())

    # ---- country premiums
    def put_country_premiums(self, rows: Sequence[tuple[str, str, str, float, float, float, date]]) -> int:
        return self._bulk("ref_country_premium", ["country", "region", "rating", "default_spread", "total_erp", "crp", "as_of"], [list(r) for r in rows])

    def country_premium(self, country: str, on_or_before: date | None = None) -> tuple[str, float, float, float, date] | None:
        """(rating, default spread, total equity risk premium, country risk premium, as_of) from the newest row on or before the date."""
        sql = "SELECT rating, default_spread, total_erp, crp, as_of FROM ref_country_premium WHERE country = ?"
        args: list[Any] = [country]
        if on_or_before is not None:
            sql += " AND as_of <= ?"
            args.append(on_or_before)
        row = self._con.execute(sql + " ORDER BY as_of DESC LIMIT 1", args).fetchone()
        return tuple(row) if row else None  # type: ignore[return-value]

    # ---- dated inputs
    def put_input(self, key: str, as_of: date, value: float, source: str, note: str = "") -> None:
        self._con.execute("INSERT OR REPLACE INTO ref_input VALUES (?, ?, ?, ?, ?)", [key, as_of, value, source, note])

    def input(self, key: str, on_or_before: date) -> InputValue | None:
        """The newest value of an input dated on or before the date, or None: a missing input is reported, never defaulted."""
        row = self._con.execute(
            "SELECT key, as_of, value, source, note FROM ref_input WHERE key = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1", [key, on_or_before]
        ).fetchone()
        return InputValue(*row) if row else None


def open_read(path: str | Path = DEFAULT_PATH) -> ReferenceStore:
    if str(path) != ":memory:" and not Path(path).exists():
        raise AthenaError(f"no reference store at {path}; run python -m athena.reference load first")
    return ReferenceStore(path, read_only=True)
