"""Cost of capital the way Damodaran's Ginzu workbook works it out.

    levered beta   = unlevered beta x (1 + (1 - tax) x debt / equity)            debt and equity at market value
    cost of equity = risk-free rate + levered beta x equity risk premium          the premium includes the country premium
    cost of debt   = risk-free rate + default spread (from a rating, real or synthetic) + country default spread
    capital        = equity weight x cost of equity + debt weight x cost of debt x (1 - tax) [+ preferred]

Every rate is in one currency. The industry tables are in US dollars; a rate in rupees follows from the dollar rate and the two expected inflation rates (`convert_rate`).
Each function is plain arithmetic on its arguments so it can be checked against the workbook cell by cell."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from athena.valuation.ratings import Kind, SyntheticRating, synthetic_rating


def levered_beta(unlevered: float, tax_rate: float, debt: float, equity: float) -> float:
    if equity <= 0:
        raise ValueError("equity value must be positive to relever a beta")
    return unlevered * (1 + (1 - tax_rate) * (debt / equity))


def cost_of_equity(riskfree: float, beta: float, equity_risk_premium: float) -> float:
    return riskfree + beta * equity_risk_premium


def market_value_of_debt(book_debt: float, interest_expense: float, average_maturity: float, pre_tax_cost_of_debt: float) -> float:
    """Debt valued as one coupon bond: the interest bill as the coupon, the book debt repaid at the average maturity, discounted at the pre-tax cost of debt."""
    k = pre_tax_cost_of_debt
    if k <= 0:
        return book_debt
    return interest_expense * (1 - (1 + k) ** -average_maturity) / k + book_debt / (1 + k) ** average_maturity


def convert_rate(rate: float, inflation_to: float, inflation_from: float) -> float:
    """A rate in one currency as the same real rate in another: (1 + r) x (1 + inflation_to) / (1 + inflation_from) - 1."""
    return (1 + rate) * (1 + inflation_to) / (1 + inflation_from) - 1


@dataclass(frozen=True)
class CapitalInputs:
    riskfree: float
    equity_risk_premium: float  # mature-market premium plus the country premium
    unlevered_beta: float  # the industry's, corrected for cash
    equity_value: float
    debt_value: float  # market value
    tax_rate: float  # marginal
    pre_tax_cost_of_debt: float
    preferred_value: float = 0.0
    preferred_cost: float = 0.0


@dataclass(frozen=True)
class CostOfCapital:
    levered_beta: float
    cost_of_equity: float
    pre_tax_cost_of_debt: float
    after_tax_cost_of_debt: float
    equity_weight: float
    debt_weight: float
    preferred_weight: float
    cost_of_capital: float
    inputs: CapitalInputs


def cost_of_capital(inputs: CapitalInputs) -> CostOfCapital:
    total = inputs.equity_value + inputs.debt_value + inputs.preferred_value
    if inputs.equity_value <= 0 or total <= 0:
        raise ValueError("equity value must be positive to weight a cost of capital")
    beta = levered_beta(inputs.unlevered_beta, inputs.tax_rate, inputs.debt_value, inputs.equity_value)
    coe = cost_of_equity(inputs.riskfree, beta, inputs.equity_risk_premium)
    kd_after = inputs.pre_tax_cost_of_debt * (1 - inputs.tax_rate)
    we, wd, wp = inputs.equity_value / total, inputs.debt_value / total, inputs.preferred_value / total
    return CostOfCapital(beta, coe, inputs.pre_tax_cost_of_debt, kd_after, we, wd, wp, we * coe + wd * kd_after + wp * inputs.preferred_cost, inputs)


@dataclass(frozen=True)
class Bands:
    """How far each driver moves in the low and high cases. Inputs, not constants: set them in the dated inputs table."""

    equity_risk_premium: float = 0.005
    riskfree: float = 0.005
    unlevered_beta: float = 0.15


@dataclass(frozen=True)
class CapitalRange:
    low: CostOfCapital  # every driver in the company's favour
    base: CostOfCapital
    high: CostOfCapital  # every driver against it


def capital_range(inputs: CapitalInputs, bands: Bands = Bands()) -> CapitalRange:
    """The base cost of capital and the two cases that move the premium, the risk-free rate and the beta together by their bands: a deliberately wide range, since the
    drivers are estimated with error and tend to be wrong in the same direction."""
    low = replace(
        inputs, equity_risk_premium=max(inputs.equity_risk_premium - bands.equity_risk_premium, 0.0), riskfree=max(inputs.riskfree - bands.riskfree, 0.0),
        unlevered_beta=max(inputs.unlevered_beta - bands.unlevered_beta, 0.05),
    )
    high = replace(
        inputs, equity_risk_premium=inputs.equity_risk_premium + bands.equity_risk_premium, riskfree=inputs.riskfree + bands.riskfree,
        unlevered_beta=inputs.unlevered_beta + bands.unlevered_beta,
    )
    return CapitalRange(cost_of_capital(low), cost_of_capital(inputs), cost_of_capital(high))


@dataclass(frozen=True)
class DebtCost:
    pre_tax: float
    rating: SyntheticRating | None
    basis: str = field(default="")


def synthetic_cost_of_debt(ebit: float, interest: float, riskfree: float, kind: Kind, country_default_spread: float = 0.0) -> DebtCost:
    """Risk-free rate + the synthetic rating's spread + the country's default spread."""
    rating = synthetic_rating(ebit, interest, kind)
    return DebtCost(riskfree + rating.spread + country_default_spread, rating, f"synthetic rating {rating.rating} from interest cover {rating.coverage:.2f} ({kind} firm table)")
