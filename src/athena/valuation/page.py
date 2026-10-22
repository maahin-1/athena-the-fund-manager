"""One company's valuation as the document the terminal's Valuation section draws.

    python -m athena.valuation.page TCS --basis consolidated --price 3200

The price (when given) comes from the caller. Every figure is computed in code (see `scenarios`); a company that cannot be valued gets `ok: false` and the reasons. Money is in
rupees per share; rates are fractions."""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from datetime import date
from typing import Any

from athena.contracts import AthenaError
from athena.fundamentals.store import DEFAULT_PATH as FUNDAMENTALS, open_read
from athena.reference.store import DEFAULT_PATH as REFERENCE, ReferenceStore
from athena.valuation import industry as ind
from athena.valuation import inputs, mix, scenarios, sensitivity, wacc


def _clean(value: Any) -> Any:
    """JSON without NaN or infinity: a figure that could not be worked out is null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    return value


def capital_block(capital: wacc.CompanyCapital) -> dict[str, Any] | None:
    if not capital.ok or capital.inr is None:
        return None
    return {
        "firm_kind": capital.firm_kind, "rating": capital.rating, "interest_cover": capital.interest_cover, "riskfree_usd": capital.riskfree_usd, "riskfree_inr": capital.riskfree_inr,
        "equity_risk_premium": capital.equity_risk_premium, "unlevered_beta": capital.unlevered_beta, "levered_beta": capital.levered_beta, "equity_weight": capital.equity_weight,
        "inr": {"cost_of_equity": capital.inr.cost_of_equity, "pre_tax_cost_of_debt": capital.inr.pre_tax_cost_of_debt, "cost_of_capital": capital.inr.cost_of_capital},
        "low": capital.low_inr.cost_of_capital if capital.low_inr else None, "high": capital.high_inr.cost_of_capital if capital.high_inr else None,
    }


def build_valuation(fund, ref: ReferenceStore, symbol: str, basis: str, price: float | None, price_note: str, today: date) -> dict[str, Any]:
    company = inputs.load_company_inputs(fund, symbol, basis, price)
    if company is None and basis == "consolidated":
        company = inputs.load_company_inputs(fund, symbol, "standalone", price)
        basis = "standalone" if company is not None else basis
    if company is None:
        return {"symbol": symbol, "basis": basis, "error": "no statements stored"}
    prepared = mix.prepare(fund, ref, company, ind.lookup(ref, symbol, today), today)
    company, industry = prepared.company, prepared.industry
    capital = wacc.company_cost_of_capital(ref, company, industry, today)
    got = scenarios.value_company(ref, company, industry, capital, today)
    doc: dict[str, Any] = {
        "symbol": symbol, "basis": basis, "on_date": today.isoformat(), "price": price, "price_note": price_note, "model": got.model, "ok": got.ok, "missing": list(got.missing),
        "period_end": company.period_end.isoformat(), "flow_basis": company.flow_basis, "industry": industry.industry if industry else None,
        "business_mix": [{"industry": name, "weight": weight} for name, weight in prepared.mix],
    }
    if got.ok:
        doc.update({
            "low": got.low, "base": got.base, "high": got.high, "price_to_base": got.price_to_base,
            "scenarios": [{"name": s.name, "value": s.value_per_share, "growth": s.growth, "margin": s.target_margin, "cost": s.cost_of_capital, "roe": s.roe, "stable_roe": s.stable_roe} for s in got.scenarios],
            "implied_growth": got.implied_growth, "implied_margin": got.implied_margin, "company_growth": got.company_growth, "industry_growth": got.industry_growth,
            "justified_price_to_book": got.justified_price_to_book, "implied_return": got.implied_return,
            "required_return": got.scenarios[1].cost_of_capital if len(got.scenarios) > 1 else None,
            "sensitivity": sensitivity.sensitivities(ref, company, industry, got.base, today) if got.base else [],
            "grid": {"growth": list(got.grid_growth), "margin": list(got.grid_margin), "values": [list(row) for row in got.grid_values]} if got.grid_values else None,
            "cost_of_capital": capital_block(capital), "assumptions": list(got.assumptions), "flags": list(got.flags),
        })
    else:
        doc["flags"] = list(got.flags)
    return _clean(doc)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("symbol")
    parser.add_argument("--basis", default="consolidated", choices=("consolidated", "standalone"))
    parser.add_argument("--price", type=float, default=None)
    parser.add_argument("--price-note", default="")
    parser.add_argument("--store", default=str(FUNDAMENTALS))
    parser.add_argument("--reference", default=str(REFERENCE))
    args = parser.parse_args(argv)
    symbol = args.symbol.upper()
    try:
        with open_read(args.store) as fund, ReferenceStore(args.reference, read_only=True) as ref:
            doc = build_valuation(fund, ref, symbol, args.basis, args.price, args.price_note, date.today())
    except AthenaError as exc:
        doc = {"symbol": symbol, "basis": args.basis, "error": str(exc), "busy": True}
    sys.stdout.write(json.dumps(doc, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
