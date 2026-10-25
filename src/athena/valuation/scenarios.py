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
from athena.valuation import dcf, dilution, distress, excess_return
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
class Policy:
    """The written rules that turn a company's figures and its industry's into assumptions, as switches. The defaults are the rules in the module note above; the calibration study
    (`calibrate.py`) runs the alternatives side by side to see which hold up against what happened afterwards. Nothing here is a price or a forecast."""

    growth_company_weight: float = 0.5  # growth = weight x company three-year + (1 - weight) x industry expected
    margin_rule: str = "mean"  # "mean" of company and industry; "own" holds the company's; "industry"; "persist" never pulls an above-industry company down
    terminal_excess_share: float = 0.0  # share of the company's return on capital above its cost of capital that lasts for ever (0 = Damodaran's default)
    terminal_growth: float | None = None  # None = the rupee risk-free rate
    minority_at_industry_pb: bool = False  # value minority interests at book x the industry's price to book instead of at book
    lender_stable_roe: str = "industry"  # "industry", "company" or "blend" (their mean)
    bull_moat_share: float = 0.5  # in the bull case only: the share of excess return on capital that lasts, for a company that has earned it (see moat_margin, moat_years)
    moat_margin: float = 0.05  # after-tax return on capital must beat the base cost of capital by this much ...
    moat_years: int = 3  # ... in each of this many latest financial years
    detail: bool = True  # False skips the implied growth and the grid (the calibration needs neither)
    failure: bool = True  # blend the going-concern value with a failure value: the chance comes from the synthetic rating, the proceeds are a share of book capital (see `distress`)
    failure_horizon: int = 10  # years the chance of failure is taken over (1 to 10)
    failure_share: float = 0.5  # share of value (or of book capital) the operating assets fetch if the firm fails
    failure_tie: str = "value"  # "value": the share is of the going-concern value, so failure can never be worth more than survival; "book": of book capital, as an uncapped amount


DEFAULT_POLICY = Policy()
ROIC_CAP = 0.30  # a company's own return on capital counts for the terminal rule only up to this


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
    implied_return: float | None = None  # the yearly return at the price if the base case comes true (cost of equity for a lender)
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


def value_company(
    store: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, capital: CompanyCapital, on_date: date, policy: Policy = DEFAULT_POLICY
) -> ValuationRange:
    """The value range for a company on a date. `ok` is False, with the reasons in `missing`, when it cannot be valued."""
    if company.layout == "insurer":
        return _unsupported(company, on_date, "unsupported", ("insurers file in a layout with no revenue or cost of capital lines this model reads",))
    if company.layout in LENDER_LAYOUTS:
        return _value_lender(store, company, industry, on_date, policy)
    return _value_operating(store, company, industry, capital, on_date, policy)


# ---- operating companies: free cash flow to the firm

def _value_operating(
    store: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, capital: CompanyCapital, on_date: date, policy: Policy = DEFAULT_POLICY
) -> ValuationRange:
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
    w = policy.growth_company_weight
    if company.revenue_cagr_3y is not None and industry_growth is not None:
        g_basis = w * company.revenue_cagr_3y + (1 - w) * industry_growth
    else:
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
    margin_base = _target_margin(policy.margin_rule, own_margin, industry_margin)
    if own_margin <= 0:
        flags.append("an operating loss: the margin is assumed to move to the industry's")
    margins = {"bear": min(own_margin, industry_margin, margin_base) if own_margin > 0 else industry_margin * 0.75, "base": margin_base, "bull": max(own_margin, industry_margin, margin_base)}
    growth = {"bear": g0 * 0.5 if g0 > 0 else g0 - 0.03, "base": g0, "bull": min(g0 * 1.5 + 0.02, BULL_GROWTH_CAP)}
    coc = {"bear": capital.high_inr.cost_of_capital, "base": capital.inr.cost_of_capital, "bull": capital.low_inr.cost_of_capital}  # type: ignore[union-attr]
    convergence = {"bear": 7, "base": 5, "bull": 5} if own_margin <= 0 else {"bear": 5, "base": 5, "bull": 5}

    effective = company.effective_tax_rate if company.effective_tax_rate is not None else tax_marginal.value
    if company.effective_tax_rate is None:
        flags.append("no usable effective tax rate: the marginal rate is used from year 1")
    rf_inr = capital.riskfree_inr
    assert rf_inr is not None
    share_crore = company.shares / SHARES_PER_CRORE
    minority = company.minority or 0.0
    if policy.minority_at_industry_pb and minority and industry.get("pb") is not None:
        minority *= industry.get("pb")  # type: ignore[operator]
        flags.append("minority interests valued at book x the industry price to book")
    terminal_growth = policy.terminal_growth
    roic_own = company.ebit * (1 - (effective if effective is not None else tax_marginal.value)) / company.invested_capital if company.invested_capital and company.invested_capital > 0 else None
    terminal_floor = rf_inr + erp_mature.value  # the terminal cost of capital
    history = [r * (1 - tax_marginal.value) for r in company.pretax_roic_by_year]
    moat = len(history) >= policy.moat_years and all(r >= coc["base"] + policy.moat_margin for r in history[-policy.moat_years :])
    moat_share = {"bear": policy.terminal_excess_share, "base": policy.terminal_excess_share, "bull": max(policy.terminal_excess_share, policy.bull_moat_share if moat else 0.0)}

    def terminal_roic_for(name: str) -> float | None:
        share = moat_share[name]
        if share <= 0 or roic_own is None:
            return None
        return terminal_floor + share * max(0.0, min(roic_own, ROIC_CAP) - terminal_floor)
    esop = _option_claim(store, company, rf_inr, tax_marginal.value, on_date)
    listed = store.input(f"listed_investments:{company.symbol}", on_date)
    non_operating = company.investments or 0.0
    if listed is not None and listed.value >= 0:
        non_operating = listed.value
        flags.append(f"investments taken at Rs {listed.value:,.0f} cr, the owner's market value of the listed holdings (input dated {listed.as_of}), in place of their book value of Rs {(company.investments or 0.0):,.0f} cr")
    failed = distress.failure(capital.rating or "", company.invested_capital, policy.failure_horizon, policy.failure_share, policy.failure_tie) if policy.failure and capital.rating else None
    if failed is not None and own_margin <= 0:
        flags.append("operating loss: the chance of failure is taken from the BB row used for the cost of capital; a loss-making firm's real chance is likely higher")
    flags += [
        "debt and minority interests are at book value" + ("" if listed is not None else ", investments too (the filings give no market value of what a company holds)") + "; lease liabilities are not in debt",
        *((esop[1],) if esop else ("employee options are not modelled: the filings do not give them (set esop_options:SYMBOL and esop_strike:SYMBOL to include them)",)),
        *(() if industry.mapping_method == "segment mix" else ("the benchmarks are one industry's: a company in several businesses is not weighted by its business mix",)),
        "the industry growth, margin and capital figures are Damodaran's India tables, in dollars; growth is turned into rupees by the inflation gap",
    ]

    def inputs_for(name: str, growth_value: float | None = None, margin_value: float | None = None) -> dcf.DcfInputs:
        g = growth[name] if growth_value is None else growth_value
        m = margins[name] if margin_value is None else margin_value
        return dcf.DcfInputs(
            revenue=company.revenue, growth_year1=g, growth_years_2_5=g, margin_year1=own_margin, target_margin=m, margin_convergence_year=convergence[name],
            sales_to_capital_1_5=s2c, sales_to_capital_6_10=s2c, effective_tax_rate=effective, marginal_tax_rate=tax_marginal.value, cost_of_capital=coc[name],
            riskfree_rate=rf_inr, mature_equity_premium=erp_mature.value, debt=company.borrowings, cash=company.cash_and_bank, shares=share_crore,
            minority_interests=minority, non_operating_assets=non_operating, terminal_growth=terminal_growth, terminal_roic=terminal_roic_for(name),
            probability_of_failure=failed.probability if failed else 0.0, distress_proceeds=(failed.proceeds or 0.0) if failed else 0.0,
            distress_share_of_value=failed.share if failed and failed.tie == "value" else None, option_value=esop[0] if esop else 0.0,
        )

    try:
        results = {name: dcf.run_fcff(inputs_for(name)) for name in ("bear", "base", "bull")}
    except ValueError as exc:
        return _unsupported(company, on_date, "fcff", (str(exc),), tuple(flags))
    values = {name: r.value_per_share for name, r in results.items()}
    scenarios = tuple(ScenarioValue(n, values[n], growth[n], margins[n], coc[n]) for n in ("bear", "base", "bull"))
    base_inputs = inputs_for("base")
    implied_g = implied_m = implied_r = None
    grid_g = grid_m = ()
    grid = []
    if policy.detail:
        implied_g = dcf.reverse_growth(company.price, base_inputs)
        implied_m = dcf.reverse_margin(company.price, base_inputs)
        implied_r = dcf.implied_return(company.price, base_inputs)
        grid_g = tuple(g0 + s for s in GRID_STEPS)
        grid_m = tuple(margins["base"] + s for s in GRID_STEPS)
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
        *((failed.sentence(),) if failed else ()),
    )
    if moat and terminal_roic_for("bull") is not None:
        assumptions += (
            f"bull case only: {policy.bull_moat_share:.0%} of the return on capital above the cost of capital lasts for ever (after-tax return on capital {min(roic_own, ROIC_CAP):.1%}), because it beat the cost of capital "  # type: ignore[type-var]
            f"by {policy.moat_margin:.0%} in each of the last {policy.moat_years} financial years",
        )
    ordered = sorted(values.values())
    return ValuationRange(
        company.symbol, on_date, "fcff", True, (), company.price, ordered[0], values["base"], ordered[-1], scenarios, implied_g, implied_m, company.revenue_cagr_3y, industry_growth, None,
        implied_r, grid_g, grid_m, tuple(grid), assumptions, tuple(flags),
    )


# ---- banks and finance companies: excess return on equity

def _value_lender(store: ReferenceStore, company: CompanyInputs, industry: IndustryContext | None, on_date: date, policy: Policy = DEFAULT_POLICY) -> ValuationRange:
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
    if policy.lender_stable_roe == "company":
        stable = roe
    elif policy.lender_stable_roe == "blend" and industry_roe is not None:
        stable = (roe + industry_roe) / 2
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
    implied_r = None
    if policy.detail:
        base_plan = plan["base"]
        implied_r = excess_return.implied_cost_of_equity(
            company.price, excess_return.BankInputs(company.book_value_per_share, base_plan["roe"], base_plan["growth"], coe["base"], base_plan["stable_roe"], stable_growth)
        )
    if company.equity_jump_years:
        flags.append(f"book equity grew by more than a quarter in {company.equity_jump_years} of the last three years (a merger or a raise): the return on equity is worked on average equity")
    return ValuationRange(
        company.symbol, on_date, "excess_return", True, (), company.price, ordered[0], values["base"], ordered[-1], scenarios, None, None, company.book_growth_3y, industry_growth,
        results["base"].justified_price_to_book, implied_r, (), (), (), assumptions, tuple(flags),
    )


def _target_margin(rule: str, own: float, industry: float) -> float:
    if own <= 0:
        return industry
    if rule == "own":
        return own
    if rule == "industry":
        return industry
    mean = (own + industry) / 2
    if rule == "persist":
        return max(own, mean)
    return mean


def _option_claim(store: ReferenceStore, company: CompanyInputs, riskfree: float, tax_rate: float, on_date: date) -> tuple[float, str] | None:
    """The owner's employee-option inputs for this company as a claim in crore after tax, with the sentence that says so; None when the owner has given none. The count (in shares)
    and the strike (rupees) are per company (`esop_options:SYMBOL`, `esop_strike:SYMBOL`); the life in years and the volatility are shared inputs with stated defaults."""
    count, strike = store.input(f"esop_options:{company.symbol}", on_date), store.input(f"esop_strike:{company.symbol}", on_date)
    if count is None or strike is None or count.value <= 0 or strike.value <= 0 or not company.price or not company.shares:
        return None
    life, vol = _optional(store, "esop_years", on_date, 5.0), _optional(store, "esop_volatility", on_date, 0.35)
    claim = dilution.option_claim(company.price, strike.value, life, vol, 0.0, riskfree, count.value, company.shares, tax_rate)
    crore = claim.after_tax / SHARES_PER_CRORE
    return crore, (
        f"employee options: {count.value:,.0f} at a strike of Rs {strike.value:,.2f} (the owner's inputs dated {count.as_of}, life {life:g} years, volatility {vol:.0%}) are a claim of Rs {crore:,.0f} cr "
        "after tax, taken off the equity value"
    )


def _optional(store: ReferenceStore, key: str, on_date: date, default: float) -> float:
    found = store.input(key, on_date)
    return default if found is None else found.value


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"

