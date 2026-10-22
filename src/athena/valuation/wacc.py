"""A listed company's cost of capital in rupees, from its filings, its industry's beta and the dated reference inputs.

The steps are Damodaran's (see `capital.py`), worked in US dollars and turned into rupees with the two expected inflation rates, because the industry betas, the premiums
and the dollar risk-free rate are all dollar figures:

    risk-free (USD)  = US 10-year Treasury yield - US default spread
    equity premium   = Damodaran's total premium for India (mature market premium + India country premium)
    beta             = the industry's unlevered beta (corrected for cash), relevered at the company's own debt / equity
    cost of debt     = risk-free + the synthetic rating's spread (from interest cover) + India's default spread

A figure the company or the reference store does not hold is listed in `missing` and no cost of capital is given; the only defaults are the dated inputs the owner can read and
change (`python -m athena.reference input`). The result also carries a low and a high case (every driver moved by its band against and for the company)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from athena.reference.store import ReferenceStore
from athena.valuation import capital as cap
from athena.valuation.industry import IndustryContext
from athena.valuation.inputs import CompanyInputs
from athena.valuation.ratings import TABLES, synthetic_rating

LENDER_LAYOUTS = ("bank", "finance", "insurer")
GOING_CONCERN_RATING = "Ba2/BB"
REQUIRED_INPUTS = ("us_10y_yield", "tax_marginal", "inflation_inr", "inflation_usd", "erp_mature")


@dataclass(frozen=True)
class RateSet:
    cost_of_equity: float
    pre_tax_cost_of_debt: float
    cost_of_capital: float


@dataclass(frozen=True)
class CompanyCapital:
    symbol: str
    on_date: date
    ok: bool
    missing: tuple[str, ...] = ()
    industry: str | None = None
    firm_kind: str | None = None
    rating: str | None = None
    interest_cover: float | None = None
    riskfree_usd: float | None = None
    riskfree_inr: float | None = None
    equity_risk_premium: float | None = None
    unlevered_beta: float | None = None
    levered_beta: float | None = None
    equity_weight: float | None = None
    debt_weight: float | None = None
    usd: RateSet | None = None
    inr: RateSet | None = None
    low_inr: RateSet | None = None
    high_inr: RateSet | None = None
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    flags: tuple[str, ...] = field(default_factory=tuple)


def _optional(store: ReferenceStore, key: str, on_date: date, default: float) -> float:
    """A tuning input (a band, a threshold) with a stated default; unlike the market inputs it may be left unset."""
    found = store.input(key, on_date)
    return default if found is None else found.value


def _rates(cc: cap.CostOfCapital, inflation_inr: float, inflation_usd: float) -> tuple[RateSet, RateSet]:
    usd = RateSet(cc.cost_of_equity, cc.pre_tax_cost_of_debt, cc.cost_of_capital)
    coe = cap.convert_rate(cc.cost_of_equity, inflation_inr, inflation_usd)
    kd = cap.convert_rate(cc.pre_tax_cost_of_debt, inflation_inr, inflation_usd)
    tax = cc.inputs.tax_rate
    wacc = cc.equity_weight * coe + cc.debt_weight * kd * (1 - tax) + cc.preferred_weight * cap.convert_rate(cc.inputs.preferred_cost, inflation_inr, inflation_usd)
    return usd, RateSet(coe, kd, wacc)


def company_cost_of_capital(
    store: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, on_date: date, country: str = "India"
) -> CompanyCapital:
    """The cost of capital for a company on a date. `ok` is False, with the reasons, when anything it stands on is missing."""
    missing: list[str] = []
    values = {key: store.input(key, on_date) for key in REQUIRED_INPUTS}
    missing += [f"input {key}" for key, v in values.items() if v is None]
    india = store.country_premium(country, on_date)
    us = store.country_premium("United States", on_date)
    if india is None:
        missing.append(f"country premium for {country}")
    if us is None:
        missing.append("country premium for United States")
    if industry is None or industry.get("unlevered_beta") is None:
        missing.append("industry unlevered beta")
    for name in ("market_cap", "ebit", "interest", "borrowings", "price"):
        if getattr(company, name) is None:
            missing.append(f"company {name}")
    if missing:
        return CompanyCapital(company.symbol, on_date, False, tuple(dict.fromkeys(missing)), industry=industry.industry if industry else None)

    assert industry is not None and india is not None and us is not None and company.market_cap is not None and company.ebit is not None
    assert company.interest is not None and company.borrowings is not None
    flags: list[str] = list(industry.flags)
    threshold = store.input("large_firm_cap_crore", on_date)
    lender = company.layout in LENDER_LAYOUTS
    kind = "financial" if lender else ("large" if threshold is not None and company.market_cap >= threshold.value else "small")
    if company.ebit <= 0:
        spread = next(s for _, rating, s in TABLES[kind] if rating == GOING_CONCERN_RATING)
        rating, cover = GOING_CONCERN_RATING, None
        flags.append("operating loss: rated BB as a going concern instead of the D the interest-cover table gives")
    else:
        rated = synthetic_rating(company.ebit, company.interest, kind)
        rating, cover, spread = rated.rating, rated.coverage, rated.spread
    riskfree_usd = values["us_10y_yield"].value - us[1]  # type: ignore[union-attr]
    kd_usd = riskfree_usd + spread + india[1]
    maturity = store.input("debt_maturity_years", on_date)
    years = maturity.value if maturity is not None else 3.0
    debt = cap.market_value_of_debt(company.borrowings, company.interest, years, kd_usd) if company.interest > 0 and company.borrowings > 0 else company.borrowings
    tax = values["tax_marginal"].value  # type: ignore[union-attr]
    erp = india[2]
    base_inputs = cap.CapitalInputs(riskfree_usd, erp, industry.get("unlevered_beta"), company.market_cap, debt, tax, kd_usd)  # type: ignore[arg-type]
    bands = cap.Bands(
        equity_risk_premium=_optional(store, "band_erp", on_date, cap.Bands.equity_risk_premium),
        riskfree=_optional(store, "band_rf", on_date, cap.Bands.riskfree),
        unlevered_beta=_optional(store, "band_beta", on_date, cap.Bands.unlevered_beta),
    )
    rng = cap.capital_range(base_inputs, bands)
    pi_inr, pi_usd = values["inflation_inr"].value, values["inflation_usd"].value  # type: ignore[union-attr]
    usd, inr = _rates(rng.base, pi_inr, pi_usd)
    _, low = _rates(rng.low, pi_inr, pi_usd)
    _, high = _rates(rng.high, pi_inr, pi_usd)
    assumptions = (
        f"US 10-year yield {values['us_10y_yield'].value:.4f} on {values['us_10y_yield'].as_of} less the US default spread {us[1]:.4f} gives a dollar risk-free rate of {riskfree_usd:.4f}",  # type: ignore[union-attr]
        f"equity risk premium {erp:.4f} for {country} (mature market {values['erp_mature'].value:.4f} plus the country premium; Damodaran file of {india[4]})",  # type: ignore[union-attr]
        f"industry {industry.industry}: unlevered beta {industry.get('unlevered_beta'):.3f} from the table dated {industry.data_as_of}",
        f"marginal tax rate {tax:.4f} (input dated {values['tax_marginal'].as_of}); inflation {pi_inr:.3f} rupee and {pi_usd:.3f} dollar convert the dollar rates",  # type: ignore[union-attr]
        f"cost of debt: {rating} ({kind} firm table) spread {spread:.4f} plus {country} default spread {india[1]:.4f}",
        f"debt valued as one coupon bond over {years:g} years at the pre-tax cost of debt; leases are not in the stored borrowings",
    )
    return CompanyCapital(
        company.symbol, on_date, True, (), industry.industry, kind, rating, cover, riskfree_usd, cap.convert_rate(riskfree_usd, pi_inr, pi_usd), erp,
        industry.get("unlevered_beta"), rng.base.levered_beta, rng.base.equity_weight, rng.base.debt_weight, usd, inr, low, high, assumptions, tuple(flags),
    )
