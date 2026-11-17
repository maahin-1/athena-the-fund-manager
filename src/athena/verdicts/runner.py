"""Making verdicts from the warehouse: on demand for one stock, or again for the stocks that already have one and have changed.

The analysts read the stored technical and fundamentals packets, so a verdict costs the model calls and nothing else. The
warehouse file is opened only for each read or write and closed again, since a long job elsewhere may need to write to it."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from athena.clock import utc_now
from athena.freshness import DEFAULT_LIMITS
from athena.timing import ENTRY_POLICY, decision_date
from athena.trading_calendar import TradingCalendar, ist_date
from athena.contracts import AthenaError
from athena.llm import envelope
from athena.orchestrator.blend import participants
from athena.orchestrator.orchestrator import OK, Orchestrator
from athena.resolver import Resolution
from athena.routing import route
from athena.verdicts.calibration import Calibration, dampen
from athena.council.evidence import build_sheet
from athena.council.run import Council, convene
from athena.news.hub import NewsHub
from athena.news.packet import news_packet_builder
from athena.valuation.facts import with_model
from athena.verdicts.changes import Facts, change_reasons, facts_from
from athena.verdicts.decisions import decide
from athena.warehouse.history import joined_actions
from athena.warehouse.packets import FUNDAMENTALS, TECHNICAL
from athena.warehouse.warehouse import ETF, NO_SESSION, STOCK, Warehouse

DB_WAIT_SECONDS = 120
STOP_AFTER_FAILURES = 3  # in a row: a bad key or a down provider should not burn the whole budget
BREAK_PREFIX = "price break with no recorded action: "
FUNDAMENTAL_ANALYSTS = ("valuation", "moat_quality", "earnings_intelligence")
CLASS_OF = {STOCK: "equity", ETF: "etf"}

Specialists = Mapping[str, Any]  # name -> something with .analyze(packet)
ModelFacts = Callable[[str, float | None], tuple[Mapping[str, Any] | None, float | None, date | None]]


class NoVerdictPossible(AthenaError):
    """The warehouse holds nothing to make this verdict from."""


class StaleInputs(NoVerdictPossible):
    """The stored packets are older than the market calendar allows for a new verdict. Nothing was stored and no model was asked; `latest` is the verdict already held (or None), still the
    last one, now to be shown as stale rather than replaced by a verdict made from old data."""

    def __init__(self, message: str, latest: dict[str, Any] | None = None):
        super().__init__(message)
        self.latest = latest


class VerdictRunner:
    def __init__(
        self, db_path: Path | str, specialists: Specialists, calibration: Calibration, clock: Callable[[], datetime] = utc_now,
        model: str = "", wait_seconds: float = DB_WAIT_SECONDS, model_facts: ModelFacts | None = None,
        news: NewsHub | None = None, council: Council | None = None,
    ):
        self._path = Path(db_path)
        self._specialists = dict(specialists)
        self._calibration = calibration
        self._clock = clock
        self._model = model
        self._wait = wait_seconds
        self._news = news  # the exchange's announcements and the press for the news analyst; None leaves that analyst out
        self._council = council  # debate and judge beside the blend (stored with the verdict, never replacing it); None leaves them out
        self._model_facts = model_facts  # symbol, close -> the valuation model's document, the market's median gap and its date

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

    def _freshness(self, technical: dict[str, Any]) -> dict[str, Any]:
        """How old the price packet is, in exchange sessions, against the declared `price.eod` limit (`athena.freshness`). Holidays the warehouse has recorded as no-session days do not count."""
        now = self._clock()
        data_day = technical["data_as_of"]
        with self._warehouse() as warehouse:
            marked = warehouse.day_status(data_day, ist_date(now))
        calendar = TradingCalendar.from_dates(day for day, state in marked.items() if state == NO_SESSION)
        age = calendar.trading_days_between(data_day, ist_date(now))
        limit = DEFAULT_LIMITS["price.eod"]
        assert limit is not None
        fresh = age <= limit.amount
        reason = None if fresh else f"price.eod data is {age} trading days old (prices to {data_day.isoformat()}), limit is {limit.amount} {limit.unit.replace('_', ' ')}"
        return {"dataset": "price.eod", "data_as_of": data_day.isoformat(), "age_trading_days": age, "limit_trading_days": limit.amount, "fresh": fresh, "reason": reason}

    def status(self, symbol: str) -> dict[str, Any]:
        """The stored verdict (or None), the reasons it is out of date (none when it still stands or there is no verdict), and whether the packets a new one would be made from are fresh
        enough to make it (`inputs`). A stale-input verdict is kept as the last one; it is not replaced."""
        entry, technical, fundamentals, stored = self._inputs(symbol)
        freshness = self._freshness(technical)
        return {"verdict": stored, "reasons": self._reasons(stored, technical, fundamentals),
                "inputs": {"fresh": freshness["fresh"], "reason": freshness["reason"], "age_trading_days": freshness["age_trading_days"]}}

    @staticmethod
    def _reasons(stored: dict[str, Any] | None, technical: dict[str, Any], fundamentals: dict[str, Any] | None) -> list[str]:
        if stored is None:
            return []
        now = facts_from(technical, fundamentals)
        then = Facts(stored["data_as_of"], stored["close"], stored["annual_period"], stored["quarter"])
        return change_reasons(then, now) if now else []

    def make(self, symbol: str, why: str = "asked for", historical: bool = False) -> dict[str, Any]:
        """Run the analysts on the stock's stored packets, store the verdict and return it as `Warehouse.latest_verdict` would.

        The packets must be fresh by the market calendar (`StaleInputs` otherwise: nothing is stored, no model is asked, the last verdict stays). `historical=True` is the explicit decision to
        analyse old packets on purpose; the verdict then records `mode: historical_explicit` and the age it accepted. Every verdict records its timing (`payload["timing"]`), including the moment its
        decision was completed, which is what a later settlement starts from."""
        entry, technical, fundamentals, latest = self._inputs(symbol)
        freshness = self._freshness(technical)
        if not freshness["fresh"] and not historical:
            raise StaleInputs(f"{symbol}: {freshness['reason']}; no new verdict was made and the last one stands", latest)
        facts = facts_from(technical, fundamentals)
        assert facts is not None
        flags = [flag.removeprefix(BREAK_PREFIX) for flag in technical["flags"] if flag.startswith(BREAK_PREFIX)]

        def statements(_: Resolution) -> dict[str, Any]:
            if fundamentals is None:
                raise AthenaError(f"no statements are held for {symbol}")
            return fundamentals["packet"]

        def valuation(resolution: Resolution) -> dict[str, Any]:
            packet = statements(resolution)
            if self._model_facts is None:
                return packet
            doc, gap, gap_date = self._model_facts(symbol, facts.close)
            return with_model(packet, doc, gap, gap_date)

        builders = {"quant_technical": lambda _: technical["packet"], **{name: statements for name in FUNDAMENTAL_ANALYSTS}, "valuation": valuation}
        if self._news is not None:
            with self._warehouse() as warehouse:
                events = tuple((a.day, a.kind, a.text) for a in joined_actions(warehouse, symbol))
            builders["news_sentiment"] = news_packet_builder(self._news, lambda _: events, lambda _: None, lambda: facts.data_as_of)
        seen: dict[str, Any] = {}
        kinds = {"quant_technical": "technical", "valuation": "fundamentals", "news_sentiment": "news"}

        def recorded(name: str, build: Callable[[Resolution], dict[str, Any]]) -> Callable[[Resolution], dict[str, Any]]:
            def build_and_keep(resolution: Resolution) -> dict[str, Any]:
                packet = build(resolution)
                if name in kinds:
                    seen[kinds[name]] = packet
                return packet

            return build_and_keep

        builders = {name: recorded(name, build) for name, build in builders.items()}
        orchestrator = Orchestrator(None, self._specialists, builders, price_flags=lambda _: flags)  # type: ignore[arg-type]
        asset_class = CLASS_OF[entry["kind"]]
        resolution = Resolution(
            asset_class, "ticker", symbol, entry["name"] or symbol, entry["isin"] or "", "exact", 1.0, (), route(asset_class)
        )
        with envelope.record_run(clock=self._clock) as run:  # every model request the analysts and the council make is noted in this run, without their knowing
            result = orchestrator.analyze_resolved(resolution)
            assert result.verdict is not None
            taking_part = tuple(participants(result.specialists))
            if not taking_part:  # nothing formed a view (the model down, or too little data): keep the verdict already held
                why_not = "; ".join(f"{name}: {reason}" for name, reason in result.skipped.items()) or "every analyst abstained for lack of data"
                raise NoVerdictPossible(f"no analyst formed a view on {symbol} ({why_not[:300]})")
            verdict = dampen(result.verdict, taking_part, self._calibration)
            now = self._clock()
            conflict = {"bullish": list(result.conflict.bullish), "bearish": list(result.conflict.bearish)} if result.conflict else None
            payload = {
                "status": result.status, "verdict": verdict, "specialists": result.specialists, "skipped": result.skipped,
                "net": result.net, "notes": list(result.notes), "taking_part": list(taking_part), "model": self._model, "why": why,
                "conflict": conflict, "decision": decide(verdict, taking_part, conflict),
            }
            if self._council is not None:
                sheet = build_sheet(symbol, str(facts.data_as_of), seen, result.specialists, verdict, payload["decision"], conflict, result.net)
                try:
                    payload["council"] = convene(self._council, sheet)
                except Exception as exc:  # the blend stands; a council that fell over is recorded, not fatal
                    payload["council"] = {"failed": f"{type(exc).__name__}: {str(exc)[:160]}"}
        completed = self._clock()  # after the analysts and the council: the decision exists from here, which is what a settlement starts from
        payload["timing"] = {
            "version": "timing/1", "entry_policy": ENTRY_POLICY, "mode": "historical_explicit" if historical and not freshness["fresh"] else "current",
            "decision_completed_at": completed.astimezone(timezone.utc).isoformat(timespec="seconds"), "decision_date_ist": decision_date(completed).isoformat(),
            "data_as_of": facts.data_as_of.isoformat(), "packet_built_at": technical["built_at"].isoformat(timespec="seconds"),
            "freshness": {"dataset": freshness["dataset"], "age_trading_days": freshness["age_trading_days"], "limit_trading_days": freshness["limit_trading_days"],
                          "status": "fresh" if freshness["fresh"] else "stale_accepted_historical"},
        }
        payload["run"] = self._envelope(run, payload, technical, fundamentals, freshness)
        with self._warehouse(write=True) as warehouse:
            warehouse.put_verdict(symbol, now, facts.data_as_of, facts.close, facts.annual_period, facts.quarter, payload)
            stored = warehouse.latest_verdict(symbol)
        assert stored is not None
        return stored

    @staticmethod
    def _envelope(run: envelope.RunRecorder, payload: dict[str, Any], technical: dict[str, Any], fundamentals: dict[str, Any] | None, freshness: Mapping[str, Any]) -> dict[str, Any]:
        """The run's envelope (`athena.llm.envelope`): the code, the hashes of the packets the analysts read, the policies the decision was made under, the schema each analyst's output was checked by,
        and the time the decision completed, beside every model request the run made. It holds hashes and names, never a packet, a prompt or a credential."""
        def sha(packet: Any) -> str:
            return hashlib.sha256(json.dumps(packet, sort_keys=True, default=str).encode()).hexdigest()

        timing = payload["timing"]
        run.inputs.update({
            "technical_packet_sha256": sha(technical["packet"]), "fundamentals_packet_sha256": sha(fundamentals["packet"]) if fundamentals else "none: no statements held",
            "data_as_of": timing["data_as_of"], "packet_built_at": timing["packet_built_at"],
        })
        run.policies.update({
            "entry": timing["entry_policy"], "freshness": {"dataset": freshness["dataset"], "limit_trading_days": freshness["limit_trading_days"], "status": timing["freshness"]["status"]},
            "calendar": {"source": "the warehouse's recorded no-session days", "kind": "TradingCalendar"},
            "costs": "not applied at decision: costs are applied at settlement (athena.costs), after the decision exists",
        })
        run.schemas.update({"timing": timing["version"], "specialist_verification": {name: (out.get("verification") or {}).get("scheme", "none") for name, out in payload["specialists"].items()}})
        return run.envelope(decision_completed_at=timing["decision_completed_at"], entry_policy=timing["entry_policy"])

    def refresh_changed(self, budget: int, on_progress: Callable[[str], None] = lambda text: None) -> dict[str, Any]:
        """Make the verdict again for stocks that have one and have changed, the longest-judged first, at most `budget` of them."""
        with self._warehouse() as warehouse:
            symbols = warehouse.verdict_symbols()
        summary: dict[str, Any] = {"checked": 0, "changed": [], "made": [], "failed": [], "left": 0, "stopped": "", "stale": []}
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
            except StaleInputs as exc:  # the data, not the model, is behind: the old verdict stands, and this is not a failure towards a stop
                summary["stale"].append(symbol)
                on_progress(f"{symbol}: not remade, {exc}")
                continue
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
