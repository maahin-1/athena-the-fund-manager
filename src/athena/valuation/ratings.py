"""Synthetic bond ratings from interest cover, and the default spread each rating carries (Damodaran's method).

A company with no rating is given one from how many times its operating profit covers its interest bill; the rating fixes a default spread, and the spread added to the
risk-free rate is its pre-tax cost of debt. The three tables below (large non-financial firms, smaller and riskier firms, financial firms) are copied from his
`fcffsimpleginzu.xlsx` and `ratings.xls` as fetched on 2026-10-22; the spreads there carry no date of their own. A test reads the workbook and fails if a table drifts."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

Kind = Literal["large", "small", "financial"]
NEGATIVE_COVER = -100000.0  # what the workbook uses for an operating loss: the lowest band
NO_INTEREST_COVER = 1000000.0  # no interest at all: the highest band

# (lower bound the cover must exceed or equal, rating, spread). A cover falls in the last band whose bound it reaches, as a spreadsheet's approximate lookup does.
_LARGE = (
    (-100000, "D2/D", 0.19), (0.2, "C2/C", 0.16), (0.65, "Ca2/CC", 0.126101), (0.8, "Caa/CCC", 0.0885), (1.25, "B3/B-", 0.050899), (1.5, "B2/B", 0.032098),
    (1.75, "B1/B+", 0.027531), (2.0, "Ba2/BB", 0.018395), (2.25, "Ba1/BB+", 0.013828), (2.5, "Baa2/BBB", 0.011113), (3.0, "A3/A-", 0.008872),
    (4.25, "A2/A", 0.007751), (5.5, "A1/A+", 0.007003), (6.5, "Aa2/AA", 0.005506), (8.5, "Aaa/AAA", 0.004),
)
_SMALL = (
    (-100000, "D2/D", 0.19), (0.5, "C2/C", 0.16), (0.8, "Ca2/CC", 0.126101), (1.25, "Caa/CCC", 0.0885), (1.5, "B3/B-", 0.050899), (2.0, "B2/B", 0.032098),
    (2.5, "B1/B+", 0.027531), (3.0, "Ba2/BB", 0.018395), (3.5, "Ba1/BB+", 0.013828), (4.0, "Baa2/BBB", 0.011113), (4.5, "A3/A-", 0.008872),
    (6.0, "A2/A", 0.007751), (7.5, "A1/A+", 0.007003), (9.5, "Aa2/AA", 0.005506), (12.5, "Aaa/AAA", 0.004),
)
_FINANCIAL = (
    (-100000, "D2/D", 0.19), (0.05, "C2/C", 0.16), (0.1, "Ca2/CC", 0.126101), (0.2, "Caa/CCC", 0.0885), (0.3, "B3/B-", 0.050899), (0.4, "B2/B", 0.032098),
    (0.5, "B1/B+", 0.027531), (0.6, "Ba2/BB", 0.018395), (0.75, "Ba1/BB+", 0.013828), (0.9, "Baa2/BBB", 0.011113), (1.2, "A3/A-", 0.008872),
    (1.5, "A2/A", 0.007751), (2.0, "A1/A+", 0.007003), (2.5, "Aa2/AA", 0.005506), (3.0, "Aaa/AAA", 0.004),
)
TABLES: dict[str, tuple[tuple[float, str, float], ...]] = {"large": _LARGE, "small": _SMALL, "financial": _FINANCIAL}
SPREAD_BY_RATING: dict[str, float] = {rating: spread for _, rating, spread in _LARGE}


@dataclass(frozen=True)
class SyntheticRating:
    kind: str
    coverage: float
    rating: str
    spread: float


def interest_cover(ebit: float, interest: float) -> float:
    """EBIT over interest the way the workbook reads it: no interest at all is the highest cover, an operating loss the lowest."""
    if interest == 0:
        return NO_INTEREST_COVER
    if ebit < 0:
        return NEGATIVE_COVER
    return ebit / interest


def synthetic_rating(ebit: float, interest: float, kind: Kind = "small") -> SyntheticRating:
    """The rating and default spread for an operating profit and an interest bill. A company with an operating loss is rated D here; for a going concern the caller
    should not take that as its cost of debt (he suggests a low but going-concern rating such as BB instead)."""
    if kind not in TABLES:
        raise ValueError(f"kind must be one of {', '.join(TABLES)}, not {kind!r}")
    cover = interest_cover(ebit, interest)
    table = TABLES[kind]
    bounds = [lower for lower, _, _ in table]
    _, rating, spread = table[max(bisect_right(bounds, cover) - 1, 0)]
    return SyntheticRating(kind, cover, rating, spread)
