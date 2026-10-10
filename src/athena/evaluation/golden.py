from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from athena.contracts import Bar
from athena.metrics.fundamentals import build_fundamentals_packet
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.reconcile import reconcile
from athena.technicals.packet import build_technical_packet

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "tests" / "golden"
SNAPSHOT_SOURCE = "golden snapshot"

PASS = "pass"
FAIL = "fail"  # the figure or signal is outside the labelled range
NO_ANSWER = "no answer"  # a specialist gave nothing to score: reported, never counted as a pass

TECHNICAL = "technical"
FUNDAMENTALS = "fundamentals"
ABSTAIN = "abstain"


@dataclass(frozen=True)
class GoldenCase:
    """One hand-labelled instrument. `figures` and `missing` are about the evidence (a packet metric inside a range, a
    metric that must be absent), each named `packet.metric` (e.g. `fundamentals.pe_trailing`); `signals` are about what
    a specialist may conclude from it (`abstain` among the allowed signals means giving no view is a correct answer)."""

    symbol: str
    why: str
    figures: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # a text metric (a trend label) and what it may read
    missing: tuple[str, ...] = ()
    signals: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class Snapshot:
    symbol: str
    name: str
    asset_class: str
    captured_on: datetime
    bars: tuple[Bar, ...]
    fundamentals: Mapping[str, Any] | None  # the stored statements payload, or None for an ETF
    fundamentals_as_of: datetime | None
    events: tuple[PriceEvent, ...] = ()  # recorded splits and bonuses as of the capture; the bars are raw, as the exchange printed them


@dataclass(frozen=True)
class Finding:
    symbol: str
    check: str  # what was checked, e.g. "figures.pe_trailing" or "signal.valuation"
    outcome: str
    detail: str


@dataclass
class GoldenReport:
    findings: list[Finding] = field(default_factory=list)

    def add(self, findings: Sequence[Finding]) -> None:
        self.findings.extend(findings)

    def count(self, outcome: str) -> int:
        return sum(1 for finding in self.findings if finding.outcome == outcome)

    def failures(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.outcome == FAIL]

    def by_component(self) -> dict[str, Counter]:
        out: dict[str, Counter] = defaultdict(Counter)
        for finding in self.findings:
            out[finding.check.split(".", 1)[0]][finding.outcome] += 1
        return out


def _case(raw: Mapping[str, Any]) -> GoldenCase:
    return GoldenCase(
        symbol=raw["symbol"], why=raw["why"],
        figures={name: (float(low), float(high)) for name, (low, high) in raw.get("figures", {}).items()},
        values={name: tuple(allowed) for name, allowed in raw.get("values", {}).items()},
        missing=tuple(raw.get("missing", ())),
        signals={name: tuple(allowed) for name, allowed in raw.get("signals", {}).items()},
    )


def load_cases(path: Path | None = None) -> list[GoldenCase]:
    path = path or GOLDEN_DIR / "cases.json"
    return [_case(raw) for raw in json.loads(path.read_text(encoding="utf-8"))]


def snapshot_to_json(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "symbol": snapshot.symbol, "name": snapshot.name, "asset_class": snapshot.asset_class,
        "captured_on": snapshot.captured_on.isoformat(),
        "bars": [[b.timestamp.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in snapshot.bars],
        "fundamentals": snapshot.fundamentals,
        "fundamentals_as_of": snapshot.fundamentals_as_of.isoformat() if snapshot.fundamentals_as_of else None,
        "events": [[event.day.isoformat(), event.ratio, event.source] for event in snapshot.events],
    }


def snapshot_from_json(raw: Mapping[str, Any]) -> Snapshot:
    captured = datetime.fromisoformat(raw["captured_on"])
    bars = tuple(
        Bar(raw["symbol"], datetime.fromisoformat(row[0]), row[1], row[2], row[3], row[4], row[5], captured, SNAPSHOT_SOURCE)
        for row in raw["bars"]
    )
    as_of = raw.get("fundamentals_as_of")
    return Snapshot(
        raw["symbol"], raw["name"], raw["asset_class"], captured, bars, raw.get("fundamentals"),
        datetime.fromisoformat(as_of) if as_of else None,
        tuple(PriceEvent(date.fromisoformat(day), float(ratio), source) for day, ratio, source in raw.get("events", [])),
    )


def load_snapshot(symbol: str, directory: Path | None = None) -> Snapshot:
    path = (directory or GOLDEN_DIR / "snapshots") / f"{symbol}.json"
    return snapshot_from_json(json.loads(path.read_text(encoding="utf-8")))


def load_index_history(path: Path | None = None) -> dict[date, dict[str, float]]:
    path = path or GOLDEN_DIR / "index_valuation.json"
    return {date.fromisoformat(day): dict(row) for day, row in json.loads(path.read_text(encoding="utf-8")).items()}


def build_packets(snapshot: Snapshot, index_history: Mapping[date, Mapping[str, float]]) -> dict[str, dict[str, Any]]:
    """The packets the specialists read, rebuilt from the frozen data as of the day it was captured."""
    bars = reconcile(snapshot.bars, snapshot.events).bars  # the same event-verified prices the dashboard reads
    packets = {TECHNICAL: build_technical_packet(snapshot.symbol, snapshot.captured_on, bars)}
    if snapshot.fundamentals is not None and snapshot.fundamentals_as_of is not None and snapshot.bars:
        packets[FUNDAMENTALS] = build_fundamentals_packet(
            snapshot.symbol, snapshot.fundamentals_as_of, snapshot.fundamentals, bars[-1].close,
            snapshot.captured_on, index_history,
        )
    return packets


def _locate(name: str, packets: Mapping[str, Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, str]:
    kind, _, metric = name.partition(".")
    return packets.get(kind), metric


def check_evidence(case: GoldenCase, packets: Mapping[str, Mapping[str, Any]]) -> list[Finding]:
    """Each labelled figure inside its range, and each labelled metric absent. A figure that is absent when the label
    expects a value fails (the data went missing), and so does one that is present when the label expects it absent."""
    findings: list[Finding] = []
    for name, (low, high) in case.figures.items():
        packet, metric_name = _locate(name, packets)
        if packet is None:
            findings.append(Finding(case.symbol, f"figures.{name}", FAIL, "that packet could not be built"))
            continue
        metric = packet["metrics"].get(metric_name)
        if metric is None:
            why = packet.get("missing_reasons", {}).get(metric_name, "not in the packet")
            findings.append(Finding(case.symbol, f"figures.{name}", FAIL, f"expected {low}..{high}, but it is missing: {why}"))
        elif isinstance(metric["value"], (int, float)) and low <= metric["value"] <= high:
            findings.append(Finding(case.symbol, f"figures.{name}", PASS, f"{metric['value']} within {low}..{high}"))
        else:
            findings.append(Finding(case.symbol, f"figures.{name}", FAIL, f"{metric['value']} outside {low}..{high}"))
    for name, allowed in case.values.items():
        packet, metric_name = _locate(name, packets)
        metric = packet["metrics"].get(metric_name) if packet is not None else None
        if metric is None:
            findings.append(Finding(case.symbol, f"values.{name}", FAIL, f"expected one of {list(allowed)}, but it is missing"))
        elif metric["value"] in allowed:
            findings.append(Finding(case.symbol, f"values.{name}", PASS, f"{metric['value']} is in {list(allowed)}"))
        else:
            findings.append(Finding(case.symbol, f"values.{name}", FAIL, f"{metric['value']} is not in {list(allowed)}"))
    for name in case.missing:
        packet, metric_name = _locate(name, packets)
        if packet is None:
            findings.append(Finding(case.symbol, f"missing.{name}", PASS, "that packet is absent, so the figure is too"))
        elif metric_name in packet["metrics"]:
            findings.append(Finding(case.symbol, f"missing.{name}", FAIL, f"expected missing, but it is {packet['metrics'][metric_name]['value']}"))
        else:
            findings.append(Finding(case.symbol, f"missing.{name}", PASS, packet.get("missing_reasons", {}).get(metric_name, "absent")))
    return findings


def check_signals(case: GoldenCase, outputs: Mapping[str, Mapping[str, Any] | None]) -> list[Finding]:
    """Each labelled specialist's signal inside its allowed set. A specialist that gave no output, or reported
    `insufficient` coverage, is reported as no answer rather than judged."""
    findings: list[Finding] = []
    for name, allowed in case.signals.items():
        output = outputs.get(name)
        if output is None or output.get("data_coverage") == "insufficient":
            if ABSTAIN in allowed:
                findings.append(Finding(case.symbol, f"signal.{name}", PASS, "gave no view, as labelled"))
            else:
                findings.append(Finding(case.symbol, f"signal.{name}", NO_ANSWER, "the specialist gave no view"))
        elif output["signal"] in allowed:
            findings.append(Finding(case.symbol, f"signal.{name}", PASS, f"{output['signal']} is in {list(allowed)}"))
        else:
            findings.append(Finding(case.symbol, f"signal.{name}", FAIL, f"{output['signal']} is not in {list(allowed)}"))
    return findings
