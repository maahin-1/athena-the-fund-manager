"""What the base value would be under the inputs that move it most, so the person reading a value sees what each assumption is worth.

The three alternatives are the ones the calibration study found largest (see `docs/valuation-calibration-2026-10-22.md`): the India country premium left out of the equity premium,
rupee inflation one point higher, and no conversion of the dollar rates to rupees at all. They are alternatives to look at, never replacements for the live inputs."""
from __future__ import annotations

from datetime import date
from typing import Any

from athena.reference.store import ReferenceStore
from athena.valuation import scenarios, wacc
from athena.valuation.calibrate import OverrideStore, Variant
from athena.valuation.industry import IndustryContext
from athena.valuation.inputs import CompanyInputs

INFLATION_STEP = 0.01


def alternatives(ref: ReferenceStore, on_date: date) -> list[tuple[str, Variant]]:
    out: list[tuple[str, Variant]] = [("Equity premium = mature market only, no India country premium", Variant("mature-premium", erp_crp_share=0.0))]
    inflation_inr, inflation_usd = ref.input("inflation_inr", on_date), ref.input("inflation_usd", on_date)
    if inflation_inr is not None:
        out.append((f"Rupee inflation {inflation_inr.value + INFLATION_STEP:.0%} instead of {inflation_inr.value:.0%}", Variant("inflation-up", inflation_inr=inflation_inr.value + INFLATION_STEP)))
    if inflation_usd is not None:
        out.append(("No rupee conversion: dollar rates used as rupee rates", Variant("no-conversion", inflation_inr=inflation_usd.value, inflation_usd=inflation_usd.value)))
    return out


def sensitivities(ref: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, base: float, on_date: date) -> list[dict[str, Any]]:
    """The base value per share under each alternative, with its change from `base`. An alternative that cannot be valued is left out."""
    out: list[dict[str, Any]] = []
    for label, variant in alternatives(ref, on_date):
        store = OverrideStore(ref, variant)
        capital = wacc.company_cost_of_capital(store, company, industry, on_date)  # type: ignore[arg-type]
        got = scenarios.value_company(store, company, industry, capital, on_date, scenarios.Policy(detail=False))  # type: ignore[arg-type]
        if got.ok and got.base is not None and base > 0:
            out.append({"label": label, "base": got.base, "change": got.base / base - 1.0})
    return out
