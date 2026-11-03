"""Detectors: each measure for one company set against the same measure for every peer.

A single ratio means little (a high receivables growth is ordinary for a fast grower), so each result is shown as where it stands in the field: the share of peers it is *more concerning than*. Peers are
the company's own industry when at least `MIN_INDUSTRY_PEERS` companies in it have the measure, else every industrial company, and the output says which. A result at or above `ELEVATED` (the 90th
percentile) is marked elevated. This is a screen for what to look at, not a verdict; the innocent explanations come with every finding.

The universe is computed once per date and kept for the process, so a run over many companies loads the statements once."""
from __future__ import annotations

import bisect
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from athena.forensic.statements.annuals import Annuals, load
from athena.forensic.statements.metrics import BY_KEY, MEASURES, Value, compute

MIN_INDUSTRY_PEERS = 15
ELEVATED = 0.90


@dataclass(frozen=True)
class Finding:
    key: str
    label: str
    unit: str
    value: float
    worse_than: float  # the share of peers this value is more concerning than, 0 to 1
    peers: int
    peer_basis: str
    elevated: bool
    reading: str
    what: str
    innocent: tuple[str, ...]
    series: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "measure": self.key, "label": self.label, "unit": self.unit, "value": round(self.value, 2), "worse_than_share_of_peers": round(self.worse_than, 3), "peers": self.peers, "peer_basis": self.peer_basis,
            "elevated": self.elevated, "reading": self.reading, "what_it_measures": self.what, "innocent_explanations": list(self.innocent), "figures": self.series,
        }


class Universe:
    """Every industrial company's measures at one date, with the peer sets to rank against."""

    def __init__(self, annuals: Mapping[str, Annuals]):
        self.annuals = dict(annuals)
        self.values: dict[str, dict[str, Value]] = {s: compute(a) for s, a in self.annuals.items()}
        self._sorted: dict[tuple[str, str], list[float]] = {}  # (measure, industry or "") -> sorted values
        self._elevated: dict[str, int] | None = None

    @classmethod
    def build(cls, con: duckdb.DuckDBPyConnection, as_of: date) -> "Universe":
        return cls(load(con, as_of))

    def _peer_values(self, measure: str, industry: str) -> tuple[list[float], str]:
        key = (measure, industry)
        if key not in self._sorted:
            members = [v[measure].value for s, v in self.values.items() if measure in v and (not industry or self.annuals[s].industry == industry)]
            self._sorted[key] = sorted(members)
        own = self._sorted[key]
        if industry and len(own) >= MIN_INDUSTRY_PEERS:
            return own, f"industry: {industry}"
        if (measure, "") not in self._sorted:  # built once; setdefault would sort the whole universe again on every call
            self._sorted[(measure, "")] = sorted(v[measure].value for v in self.values.values() if measure in v)
        return self._sorted[(measure, "")], "all industrial companies"

    def worse_than(self, measure: str, symbol: str) -> tuple[float, int, str] | None:
        value = self.values.get(symbol, {}).get(measure)
        if value is None:
            return None
        peers, basis = self._peer_values(measure, self.annuals[symbol].industry)
        if len(peers) < 5:
            return None
        below, above = bisect.bisect_left(peers, value.value), len(peers) - bisect.bisect_right(peers, value.value)
        ties = len(peers) - below - above
        # "high is bad": the share of peers strictly lower, with ties counted half; "low is bad": the share strictly higher
        less_concerning = (below if BY_KEY[measure].bad == "high" else above) + ties / 2.0
        return less_concerning / len(peers), len(peers), basis

    def elevated_counts(self) -> dict[str, int]:
        """How many measures are elevated for each company, computed once."""
        if self._elevated is None:
            self._elevated = {s: sum(1 for f in self.findings(s) if f.elevated) for s in self.annuals}
        return self._elevated

    def convergence(self, symbol: str) -> dict[str, Any] | None:
        """How many measures are elevated at once, and what share of companies have fewer. One elevated measure is common (about one company in four); several at once is rare, and that is what is worth a closer look."""
        counts = self.elevated_counts()
        if symbol not in counts:
            return None
        mine = counts[symbol]
        computed = len(self.values.get(symbol, {}))
        return {"elevated_measures": mine, "measures_computed": computed, "share_of_companies_with_fewer": round(sum(1 for c in counts.values() if c < mine) / len(counts), 3), "companies": len(counts)}

    def findings(self, symbol: str) -> list[Finding]:
        out = []
        for m in MEASURES:
            value = self.values.get(symbol, {}).get(m.key)
            ranked = self.worse_than(m.key, symbol) if value else None
            if value is None or ranked is None:
                continue
            share, peers, basis = ranked
            out.append(Finding(m.key, m.label, m.unit, value.value, share, peers, basis, share >= ELEVATED, _reading(m.label, m.unit, value.value, share, peers, basis), m.what, m.innocent, value.series))
        return sorted(out, key=lambda f: -f.worse_than)


def _reading(label: str, unit: str, value: float, share: float, peers: int, basis: str) -> str:
    unit_text = {"pct": "%", "pp": " points a year", "x": "x", "days": " days"}.get(unit, "")
    return f"{label}: {value:.1f}{unit_text}. More concerning than {share * 100:.0f}% of {peers} peers ({basis})."
