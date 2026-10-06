from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from athena.contracts import Bar
from athena.orchestrator.orchestrator import NEEDS_CLARIFICATION, OrchestrationResult
from athena.technicals.candles import Candle, candles_from_bars

NO_DATA = "no data"
UNMAPPED_ETF_NOTE = "tracking error and difference are unavailable: no tracking index is mapped for this ETF"
ETF_FUNDAMENTALS_NOTE = "financial statements do not apply to ETFs, so there are no valuation, quality or earnings panels"
LENDER_NOTE = "this is a bank or lender: cash-flow, margin and working-capital ratios do not apply and are listed as unavailable"
FUNDAMENTAL_PANELS = (("valuation", "Valuation"), ("quality", "Business quality"), ("earnings", "Earnings"))


@dataclass(frozen=True)
class MetricRow:
    name: str
    value: str | float
    unit: str
    window: str
    note: str


@dataclass(frozen=True)
class Panel:
    """A block of figures with its own as-of date, source and coverage label (TRD 2.9)."""

    title: str
    as_of: str
    source: str
    coverage: str  # full | partial | insufficient
    rows: tuple[MetricRow, ...]
    missing: Mapping[str, str]  # metric -> why it could not be computed


@dataclass(frozen=True)
class SpecialistRow:
    name: str
    signal: str
    confidence: int
    coverage: str
    reasoning: str
    missing: tuple[str, ...]


@dataclass(frozen=True)
class CandidateRow:
    identifier: str
    name: str
    asset_class: str
    score: float


@dataclass(frozen=True)
class DashboardView:
    status: str
    query: str
    identifier: str
    name: str
    asset_class: str
    match: str  # how the input was resolved
    verdict: Mapping[str, Any] | None
    specialists: tuple[SpecialistRow, ...]
    skipped: Mapping[str, str]
    panels: tuple[Panel, ...]
    candles: tuple[Candle, ...]  # the daily history the chart is drawn from
    chart_title: str
    candidates: tuple[CandidateRow, ...]
    notes: tuple[str, ...]


def _coverage(packet: Mapping[str, Any]) -> str:
    if not packet["metrics"]:
        return "insufficient"
    return "partial" if packet["missing"] else "full"


def _panel(title: str, packet: Mapping[str, Any], as_of: str, source: str) -> Panel:
    rows = tuple(
        MetricRow(name, metric["value"], metric["unit"], metric.get("window") or "", metric.get("note", ""))
        for name, metric in packet["metrics"].items()
    )
    return Panel(title, as_of, source, _coverage(packet), rows, dict(packet.get("missing_reasons", {})))


def _fundamental_panels(packet: Mapping[str, Any]) -> list[Panel]:
    """One panel per group of the fundamentals packet, each with its own coverage label."""
    groups = packet["groups"]
    as_of = f"annual to {packet['latest_annual_period'] or 'n/a'}, quarter to {packet['latest_quarter'] or 'n/a'}"
    panels = []
    for group, title in FUNDAMENTAL_PANELS:
        rows = tuple(
            MetricRow(name, metric["value"], metric["unit"], metric.get("window") or "", metric.get("note", ""))
            for name, metric in packet["metrics"].items()
            if metric["group"] == group
        )
        missing = {name: packet["missing_reasons"][name] for name in packet["missing"] if groups[name] == group}
        coverage = "insufficient" if not rows else "partial" if missing else "full"
        source = "Yahoo statements, NSE index valuation" if group == "valuation" else "Yahoo statements"
        panels.append(Panel(title, as_of, source, coverage, rows, missing))
    return panels


def build_view(
    result: OrchestrationResult,
    bars: Sequence[Bar] = (),
    technical: Mapping[str, Any] | None = None,
    risk: Mapping[str, Any] | None = None,
    fundamentals: Mapping[str, Any] | None = None,
    fundamentals_note: str | None = None,
) -> DashboardView:
    """Everything the page shows, as plain data: verdict, specialist views, panels with as-of and coverage, charts."""
    if result.status == NEEDS_CLARIFICATION and result.ambiguity:
        candidates = tuple(
            CandidateRow(c.identifier, c.name, c.asset_class, c.score) for c in result.ambiguity.candidates
        )
        return DashboardView(
            result.status, result.query, "", "", "", "", None, (), {}, (), (), "", candidates, (result.ambiguity.reason,)
        )

    resolution = result.resolution
    assert resolution is not None
    candles = candles_from_bars(bars)
    as_of = f"{candles[-1].day.isoformat()} (last close)" if candles else NO_DATA
    source = bars[-1].source if bars else NO_DATA

    panels: list[Panel] = []
    if technical is not None:
        panels.append(_panel("Technical indicators (ta-lib)", technical, as_of, source))
    notes = list(result.notes)
    if risk is not None:
        panels.append(_panel("Risk and benchmark metrics", risk, as_of, f"{source}, NIFTY 50, Nifty 1D Rate Index"))
        if resolution.asset_class == "etf" and "tracking_error" in risk["missing"]:
            notes.append(UNMAPPED_ETF_NOTE)

    if fundamentals is not None:
        panels.extend(_fundamental_panels(fundamentals))
        notes.extend(fundamentals["data_quality_flags"])
        if fundamentals["is_lender"]:
            notes.append(LENDER_NOTE)
    if fundamentals_note:
        notes.append(fundamentals_note)
    if resolution.asset_class == "etf":
        notes.append(ETF_FUNDAMENTALS_NOTE)

    chart_title = f"{resolution.identifier}  {resolution.name}  ({as_of})" if candles else ""

    specialists = tuple(
        SpecialistRow(name, out["signal"], out["confidence"], out["data_coverage"], out["reasoning"], tuple(out["missing"]))
        for name, out in result.specialists.items()
    )
    return DashboardView(
        result.status, result.query, resolution.identifier, resolution.name, resolution.asset_class,
        resolution.resolution_path, result.verdict, specialists, dict(result.skipped), tuple(panels),
        tuple(candles), chart_title, (), tuple(notes),
    )
