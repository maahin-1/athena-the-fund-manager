"""A value range for a listed company: three scenarios built by stated rules from its own filings and its industry's tables, a growth by margin grid, and the growth its price implies.

Nothing here is a forecast made by a person or a model. The rules (written out in `assumptions` on every result) are:

  growth        base = the mean of the company's three-year revenue growth and its industry's expected five-year growth (turned from dollars into rupees), held within -5% to 30%;
                bear = half of base (or 3 points below a negative base); bull = 1.5 times base plus 2 points, at most 35%
  margin        base target = the mean of the company's current operating margin and its industry's; bear = the lower of the two; bull = the higher of the two
  reinvestment  sales-to-capital = the mean of the company's revenue over invested capital and its industry's, held within 0.3 and 6
  cost of capital  the low, base and high cases of `wacc` for bull, base and bear
  terminal      growth at the rupee risk-free rate, cost of capital at risk-free + the mature-market premium, return on capital equal to the cost of capital

Banks and finance companies are valued by the excess-return model on equity; insurers are not valued (their statements are in another layout). A company that cannot be valued says
which figure is missing; no figure is guessed."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from athena.reference.store import ReferenceStore
from athena.valuation import capital as cap
from athena.valuation import dcf, excess_return
from athena.valuation.industry import IndustryContext
from athena.valuation.inputs import CompanyInputs
from athena.valuation.wacc import LENDER_LAYOUTS, CompanyCapital

SHARES_PER_CRORE = 1e7
GROWTH_BOUNDS = (-0.05, 0.30)
BULL_GROWTH_CAP = 0.35
S2C_BOUNDS = (0.3, 6.0)
GRID_STEPS = (-0.04, -0.02, 0.0, 0.02, 0.04)
BANK_GROWTH_BOUNDS = (0.03, 0.20)
BANK_ROE_BOUNDS = (0.0, 0.30)


@dataclass(frozen=True)
class ScenarioValue:
    name: str
    value_per_share: float
    growth: float | None = None
    target_margin: float | None = None
    cost_of_capital: float | None = None
    roe: float | None = None
    stable_roe: float | None = None


@dataclass(frozen=True)
class ValuationRange:
    symbol: str
    on_date: date
    model: str  # "fcff", "excess_return" or "unsupported"
    ok: bool
    missing: tuple[str, ...] = ()
    price: float | None = None
    low: float | None = None
    base: float | None = None
    high: float | None = None
    scenarios: tuple[ScenarioValue, ...] = ()
    implied_growth: float | None = None
    implied_margin: float | None = None
    company_growth: float | None = None
    industry_growth: float | None = None
    justified_price_to_book: float | None = None
    grid_growth: tuple[float, ...] = ()
    grid_margin: tuple[float, ...] = ()
    grid_values: tuple[tuple[float, ...], ...] = ()
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def price_to_base(self) -> float | None:
        return self.price / self.base if self.price and self.base and self.base > 0 else None


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


def _to_inr(rate: float | None, inflation_inr: float, inflation_usd: float) -> float | None:
    return None if rate is None else cap.convert_rate(rate, inflation_inr, inflation_usd)


def _unsupported(company: CompanyInputs, on_date: date, model: str, missing: tuple[str, ...], flags: tuple[str, ...] = ()) -> ValuationRange:
    return ValuationRange(company.symbol, on_date, model, False, missing, price=company.price, flags=flags)


def value_company(store: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, capital: CompanyCapital, on_date: date) -> ValuationRange:
    """The value range for a company on a date. `ok` is False, with the reasons in `missing`, when it cannot be valued."""
    if company.layout == "insurer":
        return _unsupported(company, on_date, "unsupported", ("insurers file in a layout with no revenue or cost of capital lines this model reads",))
    if company.layout in LENDER_LAYOUTS:
        return _value_lender(store, company, industry, on_date)
    return _value_operating(store, company, industry, capital, on_date)


# ---- operating companies: free cash flow to the firm

def _value_operating(store: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, capital: CompanyCapital, on_date: date) -> ValuationRange:
    if not capital.ok:
        return _unsupported(company, on_date, "fcff", tuple(capital.missing))
    need = {
        "revenue": company.revenue, "ebit": company.ebit, "shares": company.shares, "borrowings": company.borrowings, "cash_and_bank": company.cash_and_bank,
        "price": company.price,
    }
    missing = [f"company {name}" for name, value in need.items() if value is None]
    if company.revenue is not None and company.revenue <= 0:
        missing.append("company revenue is not positive")
    erp_mature, tax_marginal = store.input("erp_mature", on_date), store.input("tax_marginal", on_date)
    inflation_inr, inflation_usd = store.input("inflation_inr", on_date), store.input("inflation_usd", on_date)
    if industry is None:
        missing.append("industry tables")
    if missing or not (erp_mature and tax_marginal and inflation_inr and inflation_usd and industry):
        return _unsupported(company, on_date, "fcff", tuple(missing) or ("reference inputs",))
    assert company.revenue and company.ebit is not None and company.shares and company.borrowings is not None and company.cash_and_bank is not None and company.price is not None
    flags: list[str] = list(capital.flags)

    own_margin = company.ebit / company.revenue
    industry_margin = industry.get("operating_margin")
    industry_growth_usd = industry.get("expected_revenue_growth_5y")
    industry_growth = _to_inr(industry_growth_usd, inflation_inr.value, inflation_usd.value)
    g_basis = _mean([company.revenue_cagr_3y, industry_growth])
    if g_basis is None:
        return _unsupported(company, on_date, "fcff", ("no revenue growth to start from: neither the company's three-year growth nor the industry's expected growth is available",))
    g0 = _clamp(g_basis, GROWTH_BOUNDS)
    s2c_company = company.revenue / company.invested_capital if company.invested_capital and company.invested_capital > 0 else None
    s2c = _mean([s2c_company, industry.get("sales_to_capital")])
    if s2c is None:
        return _unsupported(company, on_date, "fcff", ("no sales-to-capital ratio: neither the company's nor the industry's is available",))
    s2c = _clamp(s2c, S2C_BOUNDS)

    if industry_margin is None:
        flags.append("the industry table gives no operating margin: the company's own is held")
        industry_margin = own_margin
    margin_base = (own_margin + industry_margin) / 2 if own_margin > 0 else industry_margin
    if own_margin <= 0:
        flags.append("an operating loss: the margin is assumed to move to the industry's")
    margins = {"bear": min(own_margin, industry_margin) if own_margin > 0 else industry_margin * 0.75, "base": margin_base, "bull": max(own_margin, industry_margin, margin_base)}
    growth = {"bear": g0 * 0.5 if g0 > 0 else g0 - 0.03, "base": g0, "bull": min(g0 * 1.5 + 0.02, BULL_GROWTH_CAP)}
    coc = {"bear": capital.high_inr.cost_of_capital, "base": capital.inr.cost_of_capital, "bull": capital.low_inr.cost_of_capital}  # type: ignore[union-attr]
    convergence = {"bear": 7, "base": 5, "bull": 5} if own_margin <= 0 else {"bear": 5, "base": 5, "bull": 5}

    effective = company.effective_tax_rate if company.effective_tax_rate is not None else tax_marginal.value
    if company.effective_tax_rate is None:
        flags.append("no usable effective tax rate: the marginal rate is used from year 1")
    rf_inr = capital.riskfree_inr
    assert rf_inr is not None
    share_crore = company.shares / SHARES_PER_CRORE
    flags += [
        "debt, minority interests and investments are at book value; employee options are not modelled; lease liabilities are not in debt",
        "the benchmarks are one industry's: a company in several businesses is not weighted by its business mix",
        "the industry growth, margin and capital figures are Damodaran's India tables, in dollars; growth is turned into rupees by the inflation gap",
    ]

    def inputs_for(name: str, growth_value: float | None = None, margin_value: float | None = None) -> dcf.DcfInputs:
        g = growth[name] if growth_value is None else growth_value
        m = margins[name] if margin_value is None else margin_value
        return dcf.DcfInputs(
            revenue=company.revenue, growth_year1=g, growth_years_2_5=g, margin_year1=own_margin, target_margin=m, margin_convergence_year=convergence[name],
            sales_to_capital_1_5=s2c, sales_to_capital_6_10=s2c, effective_tax_rate=effective, marginal_tax_rate=tax_marginal.value, cost_of_capital=coc[name],
            riskfree_rate=rf_inr, mature_equity_premium=erp_mature.value, debt=company.borrowings, cash=company.cash_and_bank, shares=share_crore,
            minority_interests=company.minority or 0.0, non_operating_assets=company.investments or 0.0,
        )

    try:
        results = {name: dcf.run_fcff(inputs_for(name)) for name in ("bear", "base", "bull")}
    except ValueError as exc:
        return _unsupported(company, on_date, "fcff", (str(exc),), tuple(flags))
    values = {name: r.value_per_share for name, r in results.items()}
    scenarios = tuple(ScenarioValue(n, values[n], growth[n], margins[n], coc[n]) for n in ("bear", "base", "bull"))
    base_inputs = inputs_for("base")
    implied_g = dcf.reverse_growth(company.price, base_inputs)
    implied_m = dcf.reverse_margin(company.price, base_inputs)
    grid_g = tuple(g0 + s for s in GRID_STEPS)
    grid_m = tuple(margins["base"] + s for s in GRID_STEPS)
    grid = []
    for g in grid_g:
        row = []
        for m in grid_m:
            try:
                row.append(dcf.run_fcff(inputs_for("base", g, m)).value_per_share)
            except ValueError:
                row.append(float("nan"))
        grid.append(tuple(row))
    assumptions = (
        f"revenue growth {g0:.1%} a year for years 1 to 5 (company three-year {_pct(company.revenue_cagr_3y)}, industry expected {_pct(industry_growth)}), fading to {rf_inr:.1%} by year 10",
        f"operating margin {own_margin:.1%} now, moving to {margins['base']:.1%} (industry {industry_margin:.1%}) by year {convergence['base']}",
        f"sales to capital {s2c:.2f} (company {_num(s2c_company)}, industry {_num(industry.get('sales_to_capital'))}); effective tax {effective:.1%} moving to {tax_marginal.value:.1%}",
        f"cost of capital {coc['base']:.1%} for years 1 to 5 (range {coc['bull']:.1%} to {coc['bear']:.1%}), fading to {rf_inr + erp_mature.value:.1%}; terminal growth {rf_inr:.1%}, return on capital equal to the cost of capital",
        *capital.assumptions,
    )
    ordered = sorted(values.values())
    return ValuationRange(
        company.symbol, on_date, "fcff", True, (), company.price, ordered[0], values["base"], ordered[-1], scenarios, implied_g, implied_m, company.revenue_cagr_3y, industry_growth, None,
        grid_g, grid_m, tuple(grid), assumptions, tuple(flags),
    )


# ---- banks and finance companies: excess return on equity

def _value_lender(store: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, on_date: date) -> ValuationRange:
    missing: list[str] = []
    for name in ("book_value_per_share", "roe_3y_average", "price"):
        if getattr(company, name) is None:
            missing.append(f"company {name}")
    keys = {key: store.input(key, on_date) for key in ("us_10y_yield", "erp_mature", "inflation_inr", "inflation_usd")}
    missing += [f"input {key}" for key, v in keys.items() if v is None]
    india, us = store.country_premium("India", on_date), store.country_premium("United States", on_date)
    if india is None or us is None:
        missing.append("country premiums")
    if industry is None or industry.get("beta") is None:
        missing.append("industry beta")
    if missing:
        return _unsupported(company, on_date, "excess_return", tuple(dict.fromkeys(missing)))
    assert industry is not None and india is not None and us is not None and company.book_value_per_share is not None and company.roe_3y_average is not None
    assert company.price is not None
    pi_inr, pi_usd = keys["inflation_inr"].value, keys["inflation_usd"].value  # type: ignore[union-attr]
    rf_usd = keys["us_10y_yield"].value - us[1]  # type: ignore[union-attr]
    rf_inr = cap.convert_rate(rf_usd, pi_inr, pi_usd)
    inputs = cap.CapitalInputs(rf_usd, india[2], industry.get("beta"), 1.0, 0.0, 0.25, 0.0)  # type: ignore[arg-type]
    bands = cap.Bands(
        equity_risk_premium=_optional(store, "band_erp", on_date, cap.Bands.equity_risk_premium), riskfree=_optional(store, "band_rf", on_date, cap.Bands.riskfree),
        unlevered_beta=_optional(store, "band_beta", on_date, cap.Bands.unlevered_beta),
    )
    rng = cap.capital_range(inputs, bands)  # no debt: the cost of equity is risk-free + the industry's beta x the premium
    coe = {n: cap.convert_rate(c.cost_of_equity, pi_inr, pi_usd) for n, c in (("bull", rng.low), ("base", rng.base), ("bear", rng.high))}
    flags = list(industry.flags)
    roe = _clamp(company.roe_3y_average, BANK_ROE_BOUNDS)
    industry_roe = industry.get("roe")
    industry_growth = _to_inr(industry.get("fundamental_growth"), pi_inr, pi_usd)
    growth_basis = _mean([company.book_growth_3y, industry_growth])
    book_growth = _clamp(growth_basis if growth_basis is not None else 0.10, BANK_GROWTH_BOUNDS)
    if growth_basis is None:
        flags.append("no book-value growth to start from: 10% assumed")
    stable_growth = rf_inr
    stable = industry_roe if industry_roe is not None else coe["base"]
    # the cases move the return on equity, the stable return and the cost of equity together and hold book growth, so the ordering is the same whatever the sign of the excess return
    plan = {
        "bear": dict(roe=roe * 0.9, stable_roe=stable * 0.9, growth=book_growth),
        "base": dict(roe=roe, stable_roe=stable, growth=book_growth),
        "bull": dict(roe=roe * 1.1, stable_roe=stable * 1.1, growth=book_growth),
    }
    results: dict[str, excess_return.BankResult] = {}
    for name, p in plan.items():
        try:
            results[name] = excess_return.run_excess_return(
                excess_return.BankInputs(company.book_value_per_share, p["roe"], p["growth"], coe[name], p["stable_roe"], stable_growth)
            )
        except ValueError as exc:
            return _unsupported(company, on_date, "excess_return", (str(exc),), tuple(flags))
    values = {n: r.value_per_share for n, r in results.items()}
    scenarios = tuple(ScenarioValue(n, values[n], growth=plan[n]["growth"], cost_of_capital=coe[n], roe=plan[n]["roe"], stable_roe=plan[n]["stable_roe"]) for n in ("bear", "base", "bull"))
    ordered = sorted(values.values())
    assumptions = (
        f"cost of equity {coe['base']:.1%} (range {coe['bull']:.1%} to {coe['bear']:.1%}): dollar risk-free {rf_usd:.2%} + the industry beta {industry.get('beta'):.2f} x the {india[2]:.2%} India premium, in rupees",
        f"return on equity {roe:.1%} (company three-year average) for years 1 to 5, then {plan['base']['stable_roe']:.1%} ({'the industry average' if industry_roe is not None else 'the cost of equity'})",
        f"book value per share grows {book_growth:.1%} a year for five years; stable growth {stable_growth:.1%}; the three cases move the returns by 10% and the cost of equity by its band",
        f"industry {industry.industry}: table dated {industry.data_as_of}, {industry.n_firms} firms",
    )
    flags.append("a lender's debt is its raw material, so no free cash flow or cost of capital is used; provisions and loan-book quality are not modelled")
    return ValuationRange(
        company.symbol, on_date, "excess_return", True, (), company.price, ordered[0], values["base"], ordered[-1], scenarios, None, None, company.book_growth_3y, industry_growth,
        results["base"].justified_price_to_book, (), (), (), assumptions, tuple(flags),
    )


def _optional(store: ReferenceStore, key: str, on_date: date, default: float) -> float:
    found = store.input(key, on_date)
    return default if found is None else found.value


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"

