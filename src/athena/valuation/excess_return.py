"""Value of a bank or lender from the equity it holds and what that equity earns over its cost: Damodaran's excess-return (price-to-book) model.

A lender's debt is its raw material, so there is no free cash flow to the firm to discount and no cost of capital: the value is the book equity plus the present value of the
returns above the cost of equity.

    years 1 to N   earnings = ROE x opening book value; book value grows at g; cash to shareholders = earnings - the growth in book value
    after year N   terminal value = closing book value x (stable ROE - stable g) / (cost of equity - stable g)     the justified price-to-book
    value          = present value of the cash to shareholders + the discounted terminal value

When the stable ROE equals the cost of equity the terminal price-to-book is 1: growth after year N adds nothing, which is the conservative case and the default."""
from __future__ import annotations

from dataclasses import dataclass, replace

YEARS = 5


@dataclass(frozen=True)
class BankInputs:
    book_value_per_share: float
    roe: float  # for years 1 to 5
    book_growth: float  # growth in book value per share for years 1 to 5
    cost_of_equity: float
    stable_roe: float
    stable_growth: float


@dataclass(frozen=True)
class BankYear:
    n: int
    opening_book: float
    earnings: float
    closing_book: float
    cash_to_shareholders: float
    pv: float


@dataclass(frozen=True)
class BankResult:
    inputs: BankInputs
    years: tuple[BankYear, ...]
    terminal_price_to_book: float
    terminal_value: float
    pv_terminal: float
    pv_cash: float
    value_per_share: float
    justified_price_to_book: float


def implied_cost_of_equity(price: float, x: BankInputs, high: float = 0.60, tolerance: float = 1e-7) -> float | None:
    """The cost of equity at which the bank's value per share equals the price, everything else as given: the return an owner earns a year if the returns come true. None when no
    rate between the stable growth and `high` gives the price."""
    def value(rate: float) -> float:
        return run_excess_return(replace(x, cost_of_equity=rate)).value_per_share

    low = x.stable_growth + 0.002
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


def run_excess_return(x: BankInputs) -> BankResult:
    if x.cost_of_equity <= x.stable_growth:
        raise ValueError(f"the cost of equity {x.cost_of_equity:.4f} must be above the stable growth {x.stable_growth:.4f}")
    if x.book_value_per_share <= 0:
        raise ValueError("the book value per share must be positive")
    book, factor, pv_cash, rows = x.book_value_per_share, 1.0, 0.0, []
    for n in range(1, YEARS + 1):
        factor /= 1 + x.cost_of_equity
        earnings = x.roe * book
        closing = book * (1 + x.book_growth)
        cash = earnings - (closing - book)
        pv = cash * factor
        pv_cash += pv
        rows.append(BankYear(n, book, earnings, closing, cash, pv))
        book = closing
    terminal_pb = (x.stable_roe - x.stable_growth) / (x.cost_of_equity - x.stable_growth)
    terminal_value = book * terminal_pb
    pv_terminal = terminal_value * factor
    value = pv_cash + pv_terminal
    return BankResult(x, tuple(rows), terminal_pb, terminal_value, pv_terminal, pv_cash, value, value / x.book_value_per_share)
