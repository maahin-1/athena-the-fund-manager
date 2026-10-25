"""A point-in-time ledger of the value ranges, and the scoring of them once time has passed.

The backtest cannot say whether the valuation rules are any good (see `docs/valuation-calibration-2026-10-22.md`), so each snapshot records what the model said on a day, from what had
been filed and the price of that day, and nothing is ever rewritten. After 12 and 24 months `score` sets the recorded ranges against what happened: the rank correlation of
ln(base / price) with the split-adjusted price return, and how often the later price sat inside, above or below the recorded range.

    python -m athena.valuation snapshot [--date YYYY-MM-DD]     value every stored company and record the ranges (one snapshot per date; a repeat is refused)
    python -m athena.valuation ledger-score [--months 12]       score the snapshots old enough"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from athena.contracts import AthenaError
from athena.valuation import calibrate

DEFAULT_PATH = Path(".athena") / "valuation_ledger.duckdb"
STALE_PRICE_DAYS = 7  # a stored close older than this before the snapshot date is not that day's price
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS snapshots (snap_date DATE, symbol TEXT, basis TEXT, model TEXT, price DOUBLE, price_date DATE, low DOUBLE, base DOUBLE, high DOUBLE,"
    " implied_return DOUBLE, required_return DOUBLE, industry TEXT, cautions INTEGER, PRIMARY KEY (snap_date, symbol))",
    "CREATE TABLE IF NOT EXISTS runs (snap_date DATE PRIMARY KEY, created TIMESTAMP, valued INTEGER, total INTEGER, skipped_stale_price INTEGER, note TEXT)",
)
COLUMNS = ("symbol", "basis", "model", "price", "price_date", "low", "base", "high", "implied_return", "required_return", "industry", "cautions")


class Ledger:
    def __init__(self, path: str | Path = DEFAULT_PATH, read_only: bool = False):
        if str(path) != ":memory:" and not read_only:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        if str(path) != ":memory:" and read_only and not Path(path).exists():
            raise AthenaError(f"no valuation ledger at {path}; take a snapshot first")
        self._con = duckdb.connect(str(path), read_only=read_only)
        if not read_only:
            for statement in SCHEMA:
                self._con.execute(statement)

    def __enter__(self) -> "Ledger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self._con.close()

    def has(self, snap_date: date) -> bool:
        return self._con.execute("SELECT 1 FROM runs WHERE snap_date = ?", [snap_date]).fetchone() is not None

    def record(self, snap_date: date, rows: Sequence[dict[str, Any]], total: int, skipped: int, note: str = "") -> int:
        """Record a snapshot. A date already recorded is refused: the ledger is append-only."""
        if self.has(snap_date):
            raise AthenaError(f"a snapshot for {snap_date} is already recorded; the ledger is never rewritten")
        if not rows:
            raise AthenaError(f"nothing to record for {snap_date} ({total} companies, {skipped} left out for a missing or stale price); nothing was written, so it can be retried")
        self._con.executemany(
            f"INSERT INTO snapshots VALUES (?, {', '.join('?' for _ in COLUMNS)})", [(snap_date, *(row[c] for c in COLUMNS)) for row in rows]
        )
        self._con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)", [snap_date, datetime.now(timezone.utc).replace(tzinfo=None), len(rows), total, skipped, note])
        return len(rows)

    def snapshot_dates(self) -> list[date]:
        return [r[0] for r in self._con.execute("SELECT snap_date FROM runs ORDER BY snap_date").fetchall()]

    def rows(self, snap_date: date) -> list[dict[str, Any]]:
        got = self._con.execute(f"SELECT {', '.join(COLUMNS)} FROM snapshots WHERE snap_date = ? ORDER BY symbol", [snap_date]).fetchall()
        return [dict(zip(COLUMNS, r)) for r in got]


def latest_snapshots(path: str | Path = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    """Each symbol's range from the most recent snapshot, with its date as `snap_date`; empty when there is no ledger, no snapshot, or the file is busy."""
    if not Path(path).exists():
        return {}
    try:
        with Ledger(path, read_only=True) as ledger:
            days = ledger.snapshot_dates()
            return {r["symbol"]: {**r, "snap_date": days[-1]} for r in ledger.rows(days[-1])} if days else {}
    except duckdb.Error:
        return {}


def stored_prices(warehouse: Path) -> dict[str, tuple[float, date]]:
    """Each stock's stored close and the day it is for."""
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        return {s: (c, d) for s, c, d in con.execute("SELECT symbol, close, data_as_of FROM screen WHERE kind = 'stock' AND close IS NOT NULL AND data_as_of IS NOT NULL").fetchall()}
    finally:
        con.close()


def take_snapshot(
    ledger: Ledger, value_one: Callable[[str, float], Any], symbols: Sequence[str], prices: dict[str, tuple[float, date]], snap_date: date, note: str = "",
) -> tuple[int, int, int]:
    """Value each symbol at its stored close and record the ranges. Returns (recorded, total, left out for a stale or missing price). `value_one(symbol, price)` returns a
    `ValuationRange` and the industry label, or None."""
    rows: list[dict[str, Any]] = []
    stale = 0
    for symbol in symbols:
        got = prices.get(symbol)
        if got is None or (snap_date - got[1]).days > STALE_PRICE_DAYS or got[1] > snap_date:
            stale += 1
            continue
        out = value_one(symbol, got[0])
        if out is None:
            continue
        vr, industry, cautions, basis = out
        if not vr.ok or vr.low is None or vr.base is None or vr.high is None:
            continue
        required = vr.scenarios[1].cost_of_capital if len(vr.scenarios) > 1 else None
        rows.append({
            "symbol": symbol, "basis": basis, "model": vr.model, "price": got[0], "price_date": got[1], "low": vr.low, "base": vr.base, "high": vr.high,
            "implied_return": vr.implied_return, "required_return": required, "industry": industry, "cautions": cautions,
        })
    recorded = ledger.record(snap_date, rows, len(symbols), stale, note)
    return recorded, len(symbols), stale


@dataclass(frozen=True)
class Score:
    snap_date: date
    months: int
    n: int
    ic: float | None
    inside: float | None
    above: float | None
    below: float | None
    median_gap_before: float | None  # median |ln(price / base)| at the snapshot
    median_gap_after: float | None  # and at the later date, with the later price on the snapshot's share basis


def score_rows(rows: Sequence[dict[str, float]]) -> dict[str, float | None]:
    """Score recorded ranges against later prices: each row has `price`, `low`, `base`, `high` and `later` (the later price on the snapshot's share basis)."""
    import math

    valid = [r for r in rows if r["price"] > 0 and r["base"] > 0 and r["later"] > 0]
    if not valid:
        return {"n": 0, "ic": None, "inside": None, "above": None, "below": None, "gap_before": None, "gap_after": None}
    signal = [math.log(r["base"] / r["price"]) for r in valid]
    forward = [r["later"] / r["price"] - 1.0 for r in valid]
    return {
        "n": len(valid), "ic": calibrate.spearman(signal, forward),
        "inside": sum(1 for r in valid if r["low"] <= r["later"] <= r["high"]) / len(valid),
        "above": sum(1 for r in valid if r["later"] > r["high"]) / len(valid),
        "below": sum(1 for r in valid if r["later"] < r["low"]) / len(valid),
        "gap_before": calibrate.median([abs(math.log(r["price"] / r["base"])) for r in valid]),
        "gap_after": calibrate.median([abs(math.log(r["later"] / r["base"])) for r in valid]),
    }


def score(ledger: Ledger, warehouse: Path, months: int = 12) -> list[Score]:
    """Every snapshot at least `months` old, scored against the split-adjusted price `months` later."""
    actions = calibrate.load_actions(warehouse)
    last = calibrate._last_bar_day(warehouse)
    out: list[Score] = []
    for snap in ledger.snapshot_dates():
        end = calibrate.add_months(snap, months)
        if end > last:
            continue
        later = calibrate.closes_on(warehouse, end)
        rows = []
        for r in ledger.rows(snap):
            adjust = calibrate.adjustment(actions.get(r["symbol"], ()), snap, end)
            if adjust is None or r["symbol"] not in later:
                continue
            rows.append({"price": r["price"], "low": r["low"], "base": r["base"], "high": r["high"], "later": later[r["symbol"]] * adjust})
        got = score_rows(rows)
        out.append(Score(snap, months, int(got["n"] or 0), got["ic"], got["inside"], got["above"], got["below"], got["gap_before"], got["gap_after"]))
    return out
