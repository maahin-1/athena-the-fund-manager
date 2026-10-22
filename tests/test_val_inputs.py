from datetime import datetime, timezone
from pathlib import Path

import pytest

from athena.fundamentals import ratios
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore
from athena.valuation import inputs

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


def stored(*names: str) -> FundamentalsStore:
    store = FundamentalsStore(":memory:")
    for i, name in enumerate(names):
        statements = parse_rows((FIXTURES / f"{name}.xml").read_text(encoding="utf-8"))
        store.save_statements(statements, f"u{i}", datetime(2024, 4, 12 + i, tzinfo=timezone.utc))
        store.save_company(statements.symbol, f"{statements.symbol} Ltd", "INE", "IT", statements.layout, statements.shares)
    return store


def test_the_inputs_are_in_crore_with_ebit_as_operating_profit_less_depreciation():
    with stored("tcs_fy2020", "tcs_fy2024") as store:
        got = inputs.load_company_inputs(store, "TCS", "consolidated", price=3200.0)
        raw = store.lines("TCS", "consolidated", "income")
    fy24 = next(v for (s, e), v in raw.items() if e.year == 2024 and (e - s).days > 300)
    assert got.flow_basis in ("FY2024", "TTM")
    assert got.revenue == pytest.approx(ratios.crore(fy24["sales"])) and got.depreciation == pytest.approx(ratios.crore(fy24["depreciation"]))
    assert got.ebit == pytest.approx(ratios.crore(fy24["operating_profit"] - fy24["depreciation"]))
    assert got.market_cap == pytest.approx(3200.0 * got.shares / 1e7)
    assert got.price == 3200.0 and got.symbol == "TCS" and got.basis == "consolidated"


def test_a_figure_the_filings_do_not_give_is_none_and_named_not_filled_in():
    with stored("tcs_fy2024") as store:
        got = inputs.load_company_inputs(store, "TCS", "consolidated", price=None)
    assert got.price is None and got.market_cap is None
    assert "price" in got.missing
    assert all(getattr(got, name) is None or isinstance(getattr(got, name), float) for name in ("revenue", "ebit", "interest", "book_equity", "borrowings"))
    for name in got.missing:
        assert name in {"price", "shares", "effective_tax_rate", "revenue", "ebit", "interest", "book_equity", "borrowings", "cash_and_bank"}


def test_an_effective_tax_rate_outside_a_sensible_range_is_not_used_as_a_rate():
    with stored("tcs_fy2024") as store:
        got = inputs.load_company_inputs(store, "TCS", "consolidated", price=3200.0)
    assert got.effective_tax_rate is None or 0.0 <= got.effective_tax_rate <= 0.5


def test_no_statements_gives_none():
    with FundamentalsStore(":memory:") as store:
        assert inputs.load_company_inputs(store, "NOPE", "consolidated") is None
