"""A ten-year free-cash-flow-to-the-firm valuation in the shape of Damodaran's `fcffsimpleginzu.xlsx`, and the question asked backwards.

    revenue      grows at one rate in year 1, another in years 2 to 5, then fades in equal steps to the terminal rate by year 10
    margin       moves in a straight line from the year-1 margin to a target by the convergence year and stays there
    tax          the effective rate for years 1 to 5, then in equal steps to the marginal rate by year 10; operating losses carried forward shelter profit first
    reinvestment (next year's revenue - this year's revenue) / sales-to-capital ratio: growth is paid for one year ahead
    discount     the cost of capital for years 1 to 5, then in equal steps to the mature-company rate (risk-free + mature premium) by year 10
    terminal     free cash flow in year 11 / (terminal cost of capital - terminal growth), with reinvestment = growth / return on capital x after-tax profit
    equity       operating assets (blended with a failure value when a failure probability is given) - debt - minorities + cash + non-operating assets - options

Every function is pure arithmetic on its arguments. The golden test feeds the workbook's sample company and checks each of the 10 years and the bridge to the saved results."""
from __future__ import annotations

from dataclasses import dataclass, replace

YEARS = 10
FADE_YEARS = 5


@dataclass(frozen=True)
class DcfInputs:
    revenue: float  # base year, trailing twelve months
    growth_year1: float
    growth_years_2_5: float
    margin_year1: float  # pre-tax operating margin
    target_margin: float
    margin_convergence_year: int  # 1 to 10
    sales_to_capital_1_5: float
    sales_to_capital_6_10: float
    effective_tax_rate: float
    marginal_tax_rate: float
    cost_of_capital: float  # years 1 to 5
    riskfree_rate: float  # terminal growth cannot exceed it
    mature_equity_premium: float  # terminal cost of capital = riskfree + this
    debt: float
    cash: float
    shares: float
    minority_interests: float = 0.0
    non_operating_assets: float = 0.0
    option_value: float = 0.0
    terminal_growth: float | None = None  # default: the risk-free rate
    terminal_cost_of_capital: float | None = None  # default: riskfree + mature premium
    terminal_roic: float | None = None  # default: the terminal cost of capital (growth then adds no value)
    nol_start: float = 0.0
    probability_of_failure: float = 0.0
    distress_proceeds: float = 0.0  # value of operating assets if the firm fails (an amount, not a percentage)


@dataclass(frozen=True)
class Year:
    n: int  # 1 to 10; 11 is the terminal year
    growth: float
    revenue: float
    margin: float
    ebit: float
    tax_rate: float
    nopat: float
    reinvestment: float
    fcff: float
    cost_of_capital: float
    discount_factor: float
    pv: float
    nol_end: float


@dataclass(frozen=True)
class DcfResult:
    inputs: DcfInputs
    years: tuple[Year, ...]  # years 1 to 10
    terminal: Year  # year 11, discounted with the year-10 factor
    terminal_cost_of_capital: float
    terminal_growth: float
    terminal_roic: float
    terminal_value: float
    pv_terminal: float
    pv_cash_flows: float
    operating_assets: float
    equity_value: float
    equity_to_common: float
    value_per_share: float
    reinvestment_total: float = 0.0


def _fade(start: float, end: float, step: int) -> float:
    """The value `step` of 5 equal steps from `start` toward `end`."""
    return start - (start - end) / FADE_YEARS * step


def run_fcff(x: DcfInputs) -> DcfResult:
    g_terminal = x.riskfree_rate if x.terminal_growth is None else x.terminal_growth
    coc_terminal = x.riskfree_rate + x.mature_equity_premium if x.terminal_cost_of_capital is None else x.terminal_cost_of_capital
    if coc_terminal <= g_terminal:
        raise ValueError(f"the terminal cost of capital {coc_terminal:.4f} must be above the terminal growth {g_terminal:.4f}")
    if not 1 <= x.margin_convergence_year <= YEARS:
        raise ValueError("the margin convergence year must be between 1 and 10")
    if x.shares <= 0 or x.sales_to_capital_1_5 <= 0 or x.sales_to_capital_6_10 <= 0:
        raise ValueError("shares and the sales-to-capital ratios must be positive")
    if not 0.0 <= x.probability_of_failure <= 1.0:
        raise ValueError("the probability of failure is a fraction between 0 and 1")

    growth = [x.growth_year1] + [x.growth_years_2_5] * 4 + [_fade(x.growth_years_2_5, g_terminal, k) for k in range(1, 6)]
    revenue = [x.revenue]
    for g in growth:
        revenue.append(revenue[-1] * (1 + g))
    revenue_terminal = revenue[-1] * (1 + g_terminal)
    margin = [x.margin_year1] + [
        x.target_margin if n > x.margin_convergence_year else x.target_margin - (x.target_margin - x.margin_year1) / x.margin_convergence_year * (x.margin_convergence_year - n)
        for n in range(2, YEARS + 1)
    ]
    ebit = [m * revenue[i + 1] for i, m in enumerate(margin)]
    ebit_terminal = margin[-1] * revenue_terminal
    tax = [x.effective_tax_rate] * 5 + [x.effective_tax_rate + (x.marginal_tax_rate - x.effective_tax_rate) / FADE_YEARS * k for k in range(1, 6)]
    coc = [x.cost_of_capital] * 5 + [_fade(x.cost_of_capital, coc_terminal, k) for k in range(1, 6)]
    s2c = [x.sales_to_capital_1_5] * 5 + [x.sales_to_capital_6_10] * 5

    nol, nopat = [x.nol_start], []
    for i in range(YEARS):
        e, t, held = ebit[i], tax[i], nol[-1]
        nopat.append((e if e < held else e - (e - held) * t) if e > 0 else e)
        nol.append(held - e if (e < 0 or held > e) else 0.0)
    nopat_terminal = ebit_terminal * (1 - x.marginal_tax_rate)

    reinvestment = [(revenue[i + 2] if i + 2 <= YEARS else revenue_terminal) - revenue[i + 1] for i in range(YEARS)]
    reinvestment = [r / s for r, s in zip(reinvestment, s2c)]
    roic_terminal = coc[-1] if x.terminal_roic is None else x.terminal_roic
    reinvestment_terminal = g_terminal / roic_terminal * nopat_terminal if g_terminal > 0 else 0.0

    factor, rows, pv_sum = 1.0, [], 0.0
    for i in range(YEARS):
        factor /= 1 + coc[i]
        fcff = nopat[i] - reinvestment[i]
        pv = fcff * factor
        pv_sum += pv
        rows.append(Year(i + 1, growth[i], revenue[i + 1], margin[i], ebit[i], tax[i], nopat[i], reinvestment[i], fcff, coc[i], factor, pv, nol[i + 1]))
    fcff_terminal = nopat_terminal - reinvestment_terminal
    terminal = Year(11, g_terminal, revenue_terminal, margin[-1], ebit_terminal, x.marginal_tax_rate, nopat_terminal, reinvestment_terminal, fcff_terminal, coc_terminal, factor, 0.0, nol[-1])
    value_terminal = fcff_terminal / (coc_terminal - g_terminal)
    pv_terminal = value_terminal * factor
    operating = pv_sum + pv_terminal
    blended = operating * (1 - x.probability_of_failure) + x.distress_proceeds * x.probability_of_failure
    equity = blended - x.debt - x.minority_interests + x.cash + x.non_operating_assets
    common = equity - x.option_value
    return DcfResult(
        x, tuple(rows), terminal, coc_terminal, g_terminal, roic_terminal, value_terminal, pv_terminal, pv_sum, blended, equity, common, common / x.shares, sum(reinvestment) + reinvestment_terminal,
    )


def reverse_growth(price: float, x: DcfInputs, low: float = -0.10, high: float = 0.60, tolerance: float = 1e-6) -> float | None:
    """The revenue growth (years 1 to 5, held for years 2 to 5 too) at which the value per share equals the price, everything else as given. None when the price
    lies outside what growth in [low, high] can produce. Value rises with growth, so a bisection finds it."""
    def value(growth: float) -> float:
        return run_fcff(replace(x, growth_year1=growth, growth_years_2_5=growth)).value_per_share

    try:
        v_low, v_high = value(low), value(high)
    except ValueError:
        return None
    if not (min(v_low, v_high) <= price <= max(v_low, v_high)):
        return None
    for _ in range(80):
        mid = (low + high) / 2
        v = value(mid)
        if abs(v - price) < tolerance:
            return mid
        if (v < price) == (v_low < v_high):
            low = mid
        else:
            high = mid
    return (low + high) / 2


def reverse_margin(price: float, x: DcfInputs, low: float = -0.20, high: float = 0.80, tolerance: float = 1e-6) -> float | None:
    """The target operating margin at which the value per share equals the price, everything else as given; None when no margin in [low, high] does."""
    def value(margin: float) -> float:
        return run_fcff(replace(x, target_margin=margin)).value_per_share

    v_low, v_high = value(low), value(high)
    if not (min(v_low, v_high) <= price <= max(v_low, v_high)):
        return None
    for _ in range(80):
        mid = (low + high) / 2
        v = value(mid)
        if abs(v - price) < tolerance:
            return mid
        if (v < price) == (v_low < v_high):
            low = mid
        else:
            high = mid
    return (low + high) / 2


def implied_return(price: float, x: DcfInputs, high: float = 0.60, tolerance: float = 1e-7) -> float | None:
    """The single discount rate, used for all ten years and the terminal, at which the cash flows of `x` are worth the price per share: what an owner earns a year if they come
    true. The return on new capital after year 10 stays as `x` has it (the terminal rate would otherwise change it, and growth would stop mattering as the rate nears growth).
    None when no rate between the terminal growth and `high` gives the price."""
    roic = run_fcff(x).terminal_roic
    growth = x.riskfree_rate if x.terminal_growth is None else x.terminal_growth

    def value(rate: float) -> float:
        try:
            return run_fcff(replace(x, cost_of_capital=rate, terminal_cost_of_capital=rate, terminal_roic=roic)).value_per_share
        except ValueError:
            return float("inf")

    low = growth + 0.002
    if not (value(high) <= price <= value(low)):
        return None
    for _ in range(80):
        mid = (low + high) / 2
        if value(mid) > price:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    return (low + high) / 2

