"""A company's reportable segments from its results filing: name, revenue, profit before tax and finance costs, assets and liabilities.

The exchange's XBRL names each segment in a context numbered by position: revenue sits in `FourReportable<n>D` (the year to date; `OneReportable<n>D` is the quarter), profit
in `FourReportableFinance<n>D`, assets in `OneReportable3<n>I` and liabilities in `OneReportable4<n>I`, and the segment's name is the text of
`DescriptionOfReportableSegment` in the revenue and profit contexts. Amounts are in rupees. A filing that reports one segment, or none, gives an empty list.

    python -m athena.fundamentals.segments backfill [--apply]    read the cached annual filings and record their segments"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from defusedxml import ElementTree

from athena.fundamentals.store import DEFAULT_PATH, FundamentalsStore, open_read
from athena.pit.nse_results import CACHE_DIR

_REVENUE = re.compile(r"^FourReportable(\d+)D$")
_PROFIT = re.compile(r"^FourReportableFinance(\d+)D$")
_ASSETS = re.compile(r"^OneReportable3(\d+)I$")
_LIABILITIES = re.compile(r"^OneReportable4(\d+)I$")
RECENT_ANNUAL = 3  # the backfill reads this many of the newest annual filings per company and basis


@dataclass(frozen=True)
class Segment:
    index: int
    name: str
    revenue: float | None
    profit: float | None  # before tax and finance costs
    assets: float | None
    liabilities: float | None


@dataclass(frozen=True)
class SegmentReport:
    symbol: str
    basis: str
    period_end: date
    multi: bool
    segments: tuple[Segment, ...]


def _number(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def parse_segments(text: str) -> SegmentReport | None:
    """The segments a results filing reports, or None when it is not an XBRL filing or does not say which period it covers. A single-segment filing returns an empty tuple."""
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError:
        return None
    facts: dict[str, dict[str, str]] = {}
    for element in root:
        ref = element.get("contextRef")
        if ref is None or not (element.text or "").strip():
            continue
        facts.setdefault(ref, {})[element.tag.rsplit("}", 1)[-1]] = element.text.strip()
    current = facts.get("OneD", {})
    try:
        end = date.fromisoformat(current.get("DateOfEndOfReportingPeriod", ""))
    except ValueError:
        return None
    nature = (current.get("NatureOfReportStandaloneConsolidated") or "").strip().lower()
    multi = "multi" in (current.get("IsCompanyReportingMultisegmentOrSingleSegment") or "").lower()
    names: dict[int, str] = {}
    revenue: dict[int, float | None] = {}
    profit: dict[int, float | None] = {}
    assets: dict[int, float | None] = {}
    liabilities: dict[int, float | None] = {}
    for ref, got in facts.items():
        for pattern, target, key in ((_REVENUE, revenue, "SegmentRevenue"), (_PROFIT, profit, "SegmentProfitLossBeforeTaxAndFinanceCosts"), (_ASSETS, assets, "SegmentAssets"), (_LIABILITIES, liabilities, "SegmentLiabilities")):
            match = pattern.match(ref)
            if match is None:
                continue
            n = int(match.group(1))
            target[n] = _number(got.get(key))
            if got.get("DescriptionOfReportableSegment"):
                names.setdefault(n, got["DescriptionOfReportableSegment"])
    indexes = sorted(set(names) & (set(revenue) | set(profit)))
    segments = tuple(Segment(n, names[n], revenue.get(n), profit.get(n), assets.get(n), liabilities.get(n)) for n in indexes)
    return SegmentReport((current.get("Symbol") or "").strip(), nature if nature in ("consolidated", "standalone") else "", end, multi, segments if len(segments) > 1 else ())


def backfill(path: Path, cache_dir: Path, apply: bool, recent: int = RECENT_ANNUAL, out=sys.stdout) -> dict[str, int]:
    """Read the newest cached annual filings of every company, and record the segments of those that report several."""
    stats: Counter[str] = Counter()
    with FundamentalsStore(path) if apply else open_read(path) as store:
        filings = store._con.execute(
            "SELECT symbol, basis, url, period_end, filed FROM (SELECT *, row_number() OVER (PARTITION BY symbol, basis ORDER BY period_end DESC, filed DESC) n FROM filings"
            " WHERE status = 'ok' AND annual) WHERE n <= ?",
            [recent],
        ).fetchall()
        for symbol, basis, url, period_end, filed in filings:
            file = cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".xml")
            if not file.exists():
                stats["filing not cached"] += 1
                continue
            report = parse_segments(file.read_text(encoding="utf-8"))
            if report is None:
                stats["filing unreadable"] += 1
            elif not report.segments:
                stats["single segment"] += 1
            else:
                stats["multi segment"] += 1
                if apply:
                    store.save_segments(url, symbol, basis, period_end, filed, report.segments)
    for key, n in sorted(stats.items()):
        print(f"{n:6d}  {key}", file=out)
    return dict(stats)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m athena.fundamentals.segments", description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["backfill"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    parser.add_argument("--cache", default=str(CACHE_DIR))
    args = parser.parse_args(argv)
    backfill(Path(args.path), Path(args.cache), args.apply)
    print("applied" if args.apply else "dry run: nothing written (use --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

