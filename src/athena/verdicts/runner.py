"""Making verdicts from the warehouse: on demand for one stock, or again for the stocks that already have one and have changed.

The analysts read the stored technical and fundamentals packets, so a verdict costs the model calls and nothing else. The
warehouse file is opened only for each read or write and closed again, since a long job elsewhere may need to write to it."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.orchestrator.blend import participants
from athena.orchestrator.orchestrator import OK, Orchestrator
from athena.resolver import Resolution
from athena.routing import route
from athena.verdicts.calibration import Calibration, dampen
from athena.verdicts.changes import Facts, change_reasons, facts_from
from athena.warehouse.packets import FUNDAMENTALS, TECHNICAL
from athena.warehouse.warehouse import ETF, STOCK, Warehouse

DB_WAIT_SECONDS = 120
STOP_AFTER_FAILURES = 3  # in a row: a bad key or a down provider should not burn the whole budget
BREAK_PREFIX = "price break with no recorded action: "
FUNDAMENTAL_ANALYSTS = ("valuation", "moat_quality", "earnings_intelligence")
CLASS_OF = {STOCK: "equity", ETF: "etf"}

Specialists = Mapping[str, Any]  # name -> something with .analyze(packet)


class NoVerdictPossible(AthenaError):
    """The warehouse holds nothing to make this verdict from."""


class VerdictRunner:
    def __init__(
        self, db_path: Path | str, specialists: Specialists, calibration: Calibration, clock: Callable[[], datetime] = utc_now,
        model: str = "", wait_seconds: float = DB_WAIT_SECONDS,
    ):
        self._path = Path(db_path)
        self._specialists = dict(specialists)
        self._calibration = calibration
        self._clock = clock
        self._model = model
        self._wait = wait_seconds

    @contextmanager
    def _warehouse(self, write: bool = False):
        warehouse = Warehouse(self._path, read_only=not write, wait_seconds=self._wait)
        try:
            yield warehouse
        finally:
            warehouse.close()

    def _inputs(self, symbol: str) -> tuple[dict[str, str], dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        with self._warehouse() as warehouse:
            entry = next((e for e in warehouse.universe() if e["symbol"] == symbol), None)
            if entry is None or entry["kind"] not in CLASS_OF:
                raise NoVerdictPossible(f"{symbol} is not a stock or ETF in the market database")
            technical = warehouse.packet(symbol, TECHNICAL)
            if technical is None:
                raise NoVerdictPossible(f"{symbol} has no price packet: too little recent price history")
            return entry, technical, warehouse.packet(symbol, FUNDAMENTALS), warehouse.latest_verdict(symbol)

    def latest(self, symbol: str) -> dict[str, Any] | None:
        with self._warehouse() as warehouse:
            return warehouse.latest_verdict(symbol)

    def status(self, symbol: str) -> dict[str, Any]:
        """The stored verdict (or None) and the reasons it is out of date (none when it still stands or there is no verdict)."""
        entry, technical, fundamentals, stored = self._inputs(symbol)
        return {"verdict": stored, "reasons": self._reasons(stored, technical, fundamentals)}

    @staticmethod
    def _reasons(stored: dict[str, Any] | None, technical: dict[str, Any], fundamentals: dict[str, Any] | None) -> list[str]:
        if stored is None:
            return []
        now = facts_from(technical, fundamentals)
        then = Facts(stored["data_as_of"], stored["close"], stored["annual_period"], stored["quarter"])
        return change_reasons(then, now) if now else []

    def make(self, symbol: str, why: str = "asked for") -> dict[str, Any]:
        """Run the analysts on the stock's stored packets, store the verdict and return it as `Warehouse.latest_verdict` would."""
        entry, technical, fundamentals, _ = self._inputs(symbol)
        facts = facts_from(technical, fundamentals)
        assert facts is not None
        flags = [flag.removeprefix(BREAK_PREFIX) for flag in technical["flags"] if flag.startswith(BREAK_PREFIX)]

        def statements(_: Resolution) -> dict[str, Any]:
            if fundamentals is None:
                raise AthenaError(f"no statements are held for {symbol}")
            return fundamentals["packet"]

        builders = {"quant_technical": lambda _: technical["packet"], **{name: statements for name in FUNDAMENTAL_ANALYSTS}}
        orchestrator = Orchestrator(None, self._specialists, builders, price_flags=lambda _: flags)  # type: ignore[arg-type]
        asset_class = CLASS_OF[entry["kind"]]
        resolution = Resolution(
            asset_class, "ticker", symbol, entry["name"] or symbol, entry["isin"] or "", "exact", 1.0, (), route(asset_class)
        )
        result = orchestrator.analyze_resolved(resolution)
        assert result.verdict is not None
        taking_part = tuple(participants(result.specialists))
        if not taking_part:  # nothing formed a view (the model down, or too little data): keep the verdict already held
            why_not = "; ".join(f"{name}: {reason}" for name, reason in result.skipped.items()) or "every analyst abstained for lack of data"
            raise NoVerdictPossible(f"no analyst formed a view on {symbol} ({why_not[:300]})")
        verdict = dampen(result.verdict, taking_part, self._calibration)
        now = self._clock()
        payload = {
            "status": result.status, "verdict": verdict, "specialists": result.specialists, "skipped": result.skipped,
            "net": result.net, "notes": list(result.notes), "taking_part": list(taking_part), "model": self._model, "why": why,
            "conflict": {"bullish": list(result.conflict.bullish), "bearish": list(result.conflict.bearish)} if result.conflict else None,
        }
        with self._warehouse(write=True) as warehouse:
            warehouse.put_verdict(symbol, now, facts.data_as_of, facts.close, facts.annual_period, facts.quarter, payload)
            stored = warehouse.latest_verdict(symbol)
        assert stored is not None
        return stored

    def refresh_changed(self, budget: int, on_progress: Callable[[str], None] = lambda text: None) -> dict[str, Any]:
        """Make the verdict again for stocks that have one and have changed, the longest-judged first, at most `budget` of them."""
        with self._warehouse() as warehouse:
            symbols = warehouse.verdict_symbols()
        summary: dict[str, Any] = {"checked": 0, "changed": [], "made": [], "failed": [], "left": 0, "stopped": ""}
        failures = 0
        for symbol in symbols:
            try:
                state = self.status(symbol)
            except AthenaError as exc:
                summary["failed"].append(f"{symbol}: {exc}")
                continue
            summary["checked"] += 1
            if not state["reasons"]:
                continue
            summary["changed"].append(symbol)
            if len(summary["made"]) >= budget:
                summary["left"] += 1
                continue
            if summary["stopped"]:
                summary["left"] += 1
                continue
            try:
                self.make(symbol, why="; ".join(state["reasons"]))
            except Exception as exc:  # one stock's failure is recorded; the model being down is a stop below
                failures += 1
                summary["failed"].append(f"{symbol}: {type(exc).__name__}: {str(exc)[:120]}")
                on_progress(f"{symbol}: failed ({type(exc).__name__})")
                if failures >= STOP_AFTER_FAILURES:
                    summary["stopped"] = f"{failures} failures in a row"
                continue
            failures = 0
            summary["made"].append(symbol)
            on_progress(f"{symbol}: made ({'; '.join(state['reasons'])})")
        return summary
