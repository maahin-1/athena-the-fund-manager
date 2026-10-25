"""What employee options and warrants take from the common shareholders, valued the way Damodaran's `employeeoption.xls` does.

Exercising options adds shares and brings in the strike price, so an ordinary Black-Scholes value overstates what they cost. The workbook prices one option on an adjusted stock price,
S' = (S x shares + options x value of one option) / (shares + options), where the value of one option is itself the Black-Scholes call on S': a circular definition, solved here by
repeating until it settles. The total, less tax when the options' cost is deductible, is a claim ahead of the common shareholders and is what `dcf.DcfInputs.option_value` takes.

The filings do not give the options outstanding, their strike or their life, so every input here comes from the owner (`esop_*` inputs, see `inputs_for`) and a company with none is valued
with no option claim and says so. Nothing is estimated from a name or a sector."""
from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def black_scholes_call(price: float, strike: float, years: float, volatility: float, dividend_yield: float, riskfree: float) -> float:
    """A European call on a stock paying a continuous dividend yield."""
    if years <= 0 or volatility <= 0 or price <= 0:
        return max(price - strike, 0.0)
    d1 = (math.log(price / strike) + (riskfree - dividend_yield + volatility**2 / 2) * years) / (volatility * math.sqrt(years))
    d2 = d1 - volatility * math.sqrt(years)
    return price * math.exp(-dividend_yield * years) * _norm_cdf(d1) - strike * math.exp(-riskfree * years) * _norm_cdf(d2)


@dataclass(frozen=True)
class OptionClaim:
    per_option: float  # value of one option on the dilution-adjusted price
    adjusted_price: float
    total: float  # all the options, before tax
    after_tax: float  # what is deducted from equity


def option_claim(
    price: float, strike: float, years: float, volatility: float, dividend_yield: float, riskfree: float, options: float, shares: float, tax_rate: float = 0.0, vested: float = 1.0,
) -> OptionClaim:
    """The claim of `options` options on a company with `shares` shares. `tax_rate` reduces it when the cost is deductible; `vested` is the likelihood the options vest."""
    if options < 0 or shares <= 0 or price <= 0 or strike <= 0:
        raise ValueError("options cannot be negative; shares, price and strike must be positive")
    if not 0.0 <= vested <= 1.0 or not 0.0 <= tax_rate < 1.0:
        raise ValueError("the vesting likelihood is between 0 and 1 and the tax rate is a fraction below 1")
    if options == 0:
        return OptionClaim(black_scholes_call(price, strike, years, volatility, dividend_yield, riskfree), price, 0.0, 0.0)
    adjusted, value = price, 0.0
    for _ in range(200):
        value = black_scholes_call(adjusted, strike, years, volatility, dividend_yield, riskfree)
        nxt = (price * shares + options * value) / (shares + options)
        if abs(nxt - adjusted) < 1e-12:
            adjusted = nxt
            break
        adjusted = nxt
    value = black_scholes_call(adjusted, strike, years, volatility, dividend_yield, riskfree)
    total = options * value * vested
    return OptionClaim(value, adjusted, total, total * (1.0 - tax_rate))
