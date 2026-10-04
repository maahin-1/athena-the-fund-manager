from __future__ import annotations

ROUTING: dict[str, tuple[str, ...]] = {
    "equity": ("valuation", "moat_quality", "quant_technical", "earnings_intelligence"),
    "etf": ("etf_analyst", "quant_technical"),
    "mutual_fund": ("mutual_fund_analyst",),
    "bond": ("credit_analysis", "duration_curve"),
}


def route(asset_class: str) -> tuple[str, ...]:
    try:
        return ROUTING[asset_class]
    except KeyError:
        raise ValueError(f"unknown asset class {asset_class!r}; expected one of {sorted(ROUTING)}") from None
