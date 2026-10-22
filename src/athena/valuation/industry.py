"""A company's industry benchmarks: the Damodaran India figures for the industry it belongs to, with the flags that say how far to trust them.

The comparison figures are his aggregates where he gives them (market value over total earnings, not the average of company P/Es, which a handful of near-zero earnings
push to 98 for the whole market), and each is set aside, with a flag, when it falls outside what a multiple can sensibly be. Nothing here is computed from a price."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from athena.reference.store import DEFAULT_PATH, ReferenceStore

# public name -> "dataset.metric" in the reference store
FIELDS: dict[str, str] = {
    "pe": "pe.aggregate_mkt_cap_trailing_net_income_only_money_making_firms",
    "pe_simple_average": "pe.trailing_pe",
    "pb": "pbv.pbv",
    "ev_ebitda": "vebitda.ev_ebitda",
    "ev_sales": "ps.ev_sales",
    "price_sales": "ps.price_sales",
    "roe": "roe.roe_unadjusted",
    "roic": "pbv.roic",
    "operating_margin": "margin.pre_tax_unadjusted_operating_margin",
    "ebitda_margin": "margin.ebitda_sales",
    "net_margin": "margin.net_margin",
    "fundamental_growth": "fundgr.fundamental_growth",
    "revenue_cagr_5y": "histgr.cagr_in_revenues_last_5_years",
    "expected_revenue_growth_5y": "histgr.expected_growth_in_revenues_next_5_years",
    "sales_to_capital": "capex.sales_invested_capital_ltm",
    "reinvestment_rate": "fundgrEB.reinvestment_rate",
    "tax_rate": "taxrate.aggregate_tax_rate",
    "non_cash_wc_to_sales": "wcdata.non_cash_wc_sales",
    "unlevered_beta": "beta.unlevered_beta_corrected_for_cash",
    "beta": "beta.beta",
    "cost_of_equity": "wacc.cost_of_equity",
    "cost_of_capital_usd": "wacc.cost_of_capital",
    "cost_of_capital_inr": "wacc.cost_of_capital_local_currency",
    "debt_to_capital": "dbtfund.market_debt_to_capital_unadjusted",
    "interest_coverage": "dbtfund.interest_coverage_ratio",
    "dividend_payout": "divfund.dividend_payout",
}
# a value outside these bounds is not a usable benchmark (it is set aside and flagged)
SANITY: dict[str, tuple[float, float]] = {
    "pe": (0.0, 150.0),
    "pb": (0.0, 40.0),
    "ev_ebitda": (0.0, 150.0),
    "ev_sales": (0.0, 60.0),
    "price_sales": (0.0, 60.0),
    "roe": (-1.0, 3.0),
    "operating_margin": (-1.0, 1.0),
    "ebitda_margin": (-1.0, 1.0),
    "non_cash_wc_to_sales": (-0.5, 1.5),
}
SMALL_SAMPLE = 10
LOW_MAPPING_CONFIDENCE = 0.7
STALE_DAYS = 540  # the tables are annual; one much older than this is not this year's
FINANCIAL_WORDS = ("Bank", "Insurance", "Brokerage", "Investments & Asset", "Financial Svcs", "Reinsurance")


@dataclass(frozen=True)
class IndustryContext:
    industry: str
    mapping_method: str
    mapping_confidence: float
    n_firms: int | None
    data_as_of: date | None
    values: dict[str, float]
    flags: tuple[str, ...] = field(default_factory=tuple)

    def get(self, name: str) -> float | None:
        return self.values.get(name)

    def as_packet(self) -> dict[str, Any]:
        """The plain form a metrics packet carries (JSON-friendly)."""
        return {
            "industry": self.industry,
            "mapping_method": self.mapping_method,
            "mapping_confidence": self.mapping_confidence,
            "n_firms": self.n_firms,
            "data_as_of": self.data_as_of.isoformat() if self.data_as_of else None,
            "values": dict(self.values),
            "flags": list(self.flags),
        }


def is_financial(industry: str) -> bool:
    """Whether Damodaran puts the industry among lenders and insurers, whose value does not come from free cash flow."""
    return any(word in industry for word in FINANCIAL_WORDS)


def lookup(store: ReferenceStore, symbol: str, on_or_before: date) -> IndustryContext | None:
    """The industry benchmarks for a symbol as they stood on a date, or None when the symbol is not in the company map or no table was published by then."""
    mapped = store.company_industry(symbol)
    if mapped is None:
        return None
    industry, method, confidence = mapped
    stats = store.industry_stats(industry, on_or_before=on_or_before)
    if not stats:
        return None
    as_of = store.latest_stat_date("damodaran", "India", on_or_before)
    n_firms = next((n for _, n in stats.values() if n is not None), None)
    values: dict[str, float] = {}
    flags: list[str] = []
    for name, key in FIELDS.items():
        if key not in stats:
            continue
        value = stats[key][0]
        low, high = SANITY.get(name, (None, None))
        if low is not None and not (low <= value <= high):
            flags.append(f"{name} set aside: {value:.4g} is outside {low:g}..{high:g}")
            continue
        values[name] = value
    if n_firms is not None and n_firms < SMALL_SAMPLE:
        flags.append(f"small sample: {n_firms} firms")
    if confidence < LOW_MAPPING_CONFIDENCE:
        flags.append(f"industry matched by {method} at confidence {confidence:.2f}")
    if as_of is not None and (on_or_before - as_of).days > STALE_DAYS:
        flags.append(f"tables dated {as_of}: more than {STALE_DAYS} days old")
    return IndustryContext(industry, method, confidence, n_firms, as_of, values, tuple(flags))


def packet_lookup(path: str | Path = DEFAULT_PATH) -> Callable[[str, date], dict[str, Any] | None] | None:
    """A `(symbol, day) -> packet form` function reading the reference store, or None when there is no store yet (packets are then built without industry figures).
    The store is opened for each call and closed again, so a loader can write between calls."""
    if not Path(path).exists():
        return None

    def find(symbol: str, day: date) -> dict[str, Any] | None:
        with ReferenceStore(path, read_only=True) as store:
            context = lookup(store, symbol, day)
        return context.as_packet() if context else None

    return find
