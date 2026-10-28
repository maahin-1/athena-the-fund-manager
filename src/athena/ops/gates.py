"""Automatic checks on the data stores, each a gate that says ok, warn or bad and why. They replace looking at logs and counting rows by hand.

Every gate opens a store read-only for a moment and closes it; a file a long job is writing is reported as `warn: busy` (not a failure), so checking never disturbs a run. No gate uses the
network. Gates read the stores' own tables:

  prices_fresh          the warehouse has the latest trading weekday (weekdays after the last loaded one must all be recorded as no-session)
  prices_no_holes       no weekday in the last 400 days is missing from the day table, and no weekday recorded as no-session has a derivatives file or index rows (so it was not a holiday),
                        and no year has an implausible number of no-session weekdays
  prices_breadth        the last loaded day has about as many symbols as the days before it
  derivs_in_step        the derivatives history reaches the warehouse's last day
  shareholding_cover    once the filing deadline for a quarter has passed, most companies that had the quarter before have this one
  fundamentals_health   no filing is recorded as failed, no company's last run failed or partial (counted), no duplicate statement keys
A gate that cannot run says so (`warn`); it is never silently ok."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

OK, WARN, BAD = "ok", "warn", "bad"
WAREHOUSE = Path(".athena") / "warehouse.duckdb"
DERIVS = Path(".athena") / "derivs.duckdb"
FUNDAMENTALS = Path(".athena") / "fundamentals.duckdb"
IST = timezone(timedelta(hours=5, minutes=30))
MAX_NO_SESSION_WEEKDAYS_A_YEAR = 22  # the exchange has about 15 weekday holidays a year
BREADTH_FLOOR = 0.9
SHAREHOLDING_FLOOR = 0.95
SHAREHOLDING_DEADLINE_DAYS = 45  # after the quarter end: the 21-day filing deadline and a grace period


@dataclass(frozen=True)
class Gate:
    name: str
    level: str
    detail: str


class Busy(Exception):
    pass


def _open(path: Path, tries: int = 4, pause: float = 0.5) -> duckdb.DuckDBPyConnection:
    if not Path(path).exists():
        raise FileNotFoundError(str(path))
    for attempt in range(tries):
        try:
            return duckdb.connect(str(path), read_only=True)
        except duckdb.IOException:
            if attempt == tries - 1:
                raise Busy(Path(path).name) from None
            time.sleep(pause)
    raise Busy(Path(path).name)


def _run(name: str, fn: Callable[[], Gate]) -> Gate:
    try:
        return fn()
    except Busy as exc:
        return Gate(name, WARN, f"could not check: {exc} is busy (a long job is writing it)")
    except FileNotFoundError as exc:
        return Gate(name, WARN, f"could not check: {exc} does not exist")
    except Exception as exc:  # noqa: BLE001 - a gate that breaks must say so, and the others still run
        return Gate(name, WARN, f"could not check: {type(exc).__name__}: {str(exc)[:120]}")


def weekdays_between(first: date, last: date) -> list[date]:
    """The weekdays after `first` up to and including `last`."""
    out, day = [], first + timedelta(days=1)
    while day <= last:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def expected_last_day(now: datetime) -> date:
    """The most recent weekday whose end-of-day file should exist: today after 21:00 India time, else the weekday before today."""
    local = now.astimezone(IST)
    day = local.date() if local.hour >= 21 else local.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def prices_fresh(now: datetime, path: Path = WAREHOUSE) -> Gate:
    def go() -> Gate:
        con = _open(path)
        try:
            row = con.execute("SELECT max(day) FROM days WHERE status = 'loaded'").fetchone()
            if not row or row[0] is None:
                return Gate("prices_fresh", BAD, "no loaded price day at all")
            last = row[0]
            expected = expected_last_day(now)
            if last >= expected:
                return Gate("prices_fresh", OK, f"last loaded day {last}")
            missing = [d for d in weekdays_between(last, expected)]
            recorded = {r[0] for r in con.execute("SELECT day FROM days WHERE status = 'no_session' AND day > ?", [last]).fetchall()}
            late = [d for d in missing if d not in recorded]
            if not late:
                return Gate("prices_fresh", OK, f"last loaded day {last}; the weekdays after it are recorded as no session")
            return Gate("prices_fresh", BAD if len(late) > 1 else WARN, f"last loaded day {last}, expected {expected}: {len(late)} weekday(s) not loaded ({late[0]}..{late[-1]})")
        finally:
            con.close()

    return _run("prices_fresh", go)


def prices_no_holes(now: datetime, path: Path = WAREHOUSE, derivs_path: Path = DERIVS) -> Gate:
    def go() -> Gate:
        con = _open(path)
        try:
            first = now.astimezone(IST).date() - timedelta(days=400)
            last = expected_last_day(now) - timedelta(days=3)  # the last few days may only be late
            have = {r[0]: r[1] for r in con.execute("SELECT day, status FROM days WHERE day >= ?", [first]).fetchall()}
            missing = [d for d in weekdays_between(first, last) if d not in have]
            no_session = sorted(d for d, s in have.items() if s == "no_session" and d.weekday() < 5)
            index_days = {r[0] for r in con.execute("SELECT day FROM index_days WHERE day >= ?", [first]).fetchall()}
        finally:
            con.close()
        problems, warns = [], []
        if missing:
            problems.append(f"{len(missing)} weekday(s) have no entry at all (first {missing[0]})")
        contradicted = [d for d in no_session if d in index_days]
        try:
            dcon = _open(derivs_path)
            try:
                with_file = {r[0] for r in dcon.execute("SELECT day FROM derivs_days WHERE has_file AND day >= ?", [first]).fetchall()}
            finally:
                dcon.close()
            contradicted += [d for d in no_session if d in with_file and d not in contradicted]
        except (Busy, FileNotFoundError):
            warns.append("derivatives history not readable, so the holiday cross-check used index rows only")
        if contradicted:
            problems.append(f"{len(contradicted)} weekday(s) recorded as no-session have data elsewhere, so they were not holidays (first {sorted(contradicted)[0]})")
        by_year: dict[int, int] = {}
        for d in no_session:
            by_year[d.year] = by_year.get(d.year, 0) + 1
        for year, n in by_year.items():
            if n > MAX_NO_SESSION_WEEKDAYS_A_YEAR:
                problems.append(f"{n} no-session weekdays in {year} is more than any exchange year")
        if problems:
            return Gate("prices_no_holes", BAD, "; ".join(problems + warns))
        return Gate("prices_no_holes", WARN if warns else OK, "; ".join(warns) or f"{len(no_session)} no-session weekday(s) in the last 400 days, none contradicted")

    return _run("prices_no_holes", go)


def prices_breadth(path: Path = WAREHOUSE) -> Gate:
    def go() -> Gate:
        con = _open(path)
        try:
            rows = con.execute(
                "SELECT b.day, count(*) FROM bars b JOIN (SELECT day FROM days WHERE status = 'loaded' ORDER BY day DESC LIMIT 22) d ON d.day = b.day GROUP BY b.day ORDER BY b.day DESC"
            ).fetchall()
        finally:
            con.close()
        if len(rows) < 6:
            return Gate("prices_breadth", WARN, "too few days to compare")
        last_day, last_n = rows[0]
        before = sorted(n for _, n in rows[1:])
        median = before[len(before) // 2]
        if last_n < BREADTH_FLOOR * median:
            return Gate("prices_breadth", BAD, f"{last_day}: {last_n} symbols against a usual {median}: a partial file?")
        return Gate("prices_breadth", OK, f"{last_day}: {last_n} symbols (usual {median})")

    return _run("prices_breadth", go)


def derivs_in_step(path: Path = DERIVS, warehouse: Path = WAREHOUSE, now: datetime | None = None) -> Gate:
    def go() -> Gate:
        wcon = _open(warehouse)
        try:
            w = wcon.execute("SELECT max(day) FROM days WHERE status = 'loaded'").fetchone()[0]
        finally:
            wcon.close()
        dcon = _open(path)
        try:
            d = dcon.execute("SELECT max(day) FROM derivs_days WHERE has_file").fetchone()[0]
        finally:
            dcon.close()
        if w is None or d is None:
            return Gate("derivs_in_step", WARN, "one of the stores has no loaded day")
        if d >= w:
            return Gate("derivs_in_step", OK, f"derivatives reach {d} (prices {w})")
        behind = len(weekdays_between(d, w))
        return Gate("derivs_in_step", WARN if behind == 1 else BAD, f"derivatives end {d}, prices end {w}: {behind} weekday(s) behind")

    return _run("derivs_in_step", go)


def shareholding_cover(now: datetime, path: Path = FUNDAMENTALS) -> Gate:
    def go() -> Gate:
        con = _open(path)
        try:
            # only real quarter ends count: a filing dated some other day is a quirk of the source, reported below, not a quarter
            quarter_end = "month(period_end) IN (3, 6, 9, 12) AND period_end = last_day(period_end)"
            quarters = [r[0] for r in con.execute(f"SELECT DISTINCT period_end FROM shareholding WHERE {quarter_end} ORDER BY period_end DESC LIMIT 2").fetchall()]
            off = con.execute(f"SELECT count(*) FROM shareholding WHERE NOT ({quarter_end})").fetchone()[0]
            if len(quarters) < 2:
                return Gate("shareholding_cover", WARN, "fewer than two quarter ends stored")
            newest, previous = quarters
            n_prev = con.execute("SELECT count(DISTINCT symbol) FROM shareholding WHERE period_end = ?", [previous]).fetchone()[0]
            both = con.execute("SELECT count(DISTINCT a.symbol) FROM shareholding a JOIN shareholding b ON a.symbol = b.symbol AND a.period_end = ? AND b.period_end = ?", [newest, previous]).fetchone()[0]
        finally:
            con.close()
        share = both / n_prev if n_prev else 0.0
        due = newest + timedelta(days=SHAREHOLDING_DEADLINE_DAYS)
        text = f"{newest}: {both} of the {n_prev} companies that had {previous} have it ({share:.0%})" + (f"; {off} row(s) are dated on no quarter end" if off else "")
        if now.astimezone(IST).date() < due:
            return Gate("shareholding_cover", OK if not off else WARN, text + f"; the deadline plus grace is {due}, so coverage is not judged yet")
        return Gate("shareholding_cover", (OK if share >= SHAREHOLDING_FLOOR else BAD) if not off else (WARN if share >= SHAREHOLDING_FLOOR else BAD), text)

    return _run("shareholding_cover", go)


def fundamentals_health(path: Path = FUNDAMENTALS) -> Gate:
    def go() -> Gate:
        con = _open(path)
        try:
            failed = con.execute("SELECT count(*) FROM filings WHERE status = 'failed'").fetchone()[0]
            runs = dict(con.execute("SELECT status, count(*) FROM ingest_runs WHERE status IN ('failed', 'partial') GROUP BY status").fetchall())
            dup = con.execute(
                "SELECT count(*) FROM (SELECT 1 FROM statement_lines GROUP BY symbol, basis, statement, period_start, period_end, key HAVING count(*) > 1)"
            ).fetchone()[0]
        finally:
            con.close()
        text = f"{failed} filing(s) failed, {runs.get('failed', 0)} company run(s) failed, {runs.get('partial', 0)} partial, {dup} duplicate statement key(s)"
        if dup:
            return Gate("fundamentals_health", BAD, text)
        return Gate("fundamentals_health", WARN if failed or runs else OK, text)

    return _run("fundamentals_health", go)


def all_gates(now: datetime | None = None) -> list[Gate]:
    now = now or datetime.now(timezone.utc)
    return [prices_fresh(now), prices_no_holes(now), prices_breadth(), derivs_in_step(), shareholding_cover(now), fundamentals_health()]


def worst(items: list[Any]) -> str:
    levels = {g.level for g in items}
    return BAD if BAD in levels else WARN if WARN in levels else OK
