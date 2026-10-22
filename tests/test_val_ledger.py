from datetime import date

import pytest

from athena.contracts import AthenaError
from athena.valuation import ledger
from athena.valuation.scenarios import ScenarioValue, ValuationRange

SNAP = date(2026, 10, 31)


def vr(symbol, low, base, high, price, ok=True):
    return ValuationRange(
        symbol, SNAP, "fcff", ok, (), price, low, base, high, (ScenarioValue("bear", low, cost_of_capital=0.14), ScenarioValue("base", base, cost_of_capital=0.12), ScenarioValue("bull", high)),
        implied_return=0.09,
    )


def value_one(symbol, price):
    table = {"AAA": vr("AAA", 80.0, 100.0, 140.0, price), "BBB": vr("BBB", 40.0, 50.0, 70.0, price), "CCC": vr("CCC", None, None, None, price, ok=False)}
    got = table.get(symbol)
    return None if got is None else (got, "Steel", 2, "consolidated")


PRICES = {"AAA": (120.0, date(2026, 10, 30)), "BBB": (45.0, date(2026, 10, 30)), "CCC": (10.0, date(2026, 10, 30)), "OLD": (9.0, date(2026, 9, 1)), "NONE": (1.0, date(2026, 10, 30))}


def test_a_snapshot_records_each_valued_company_and_leaves_out_stale_prices_failures_and_unknowns():
    with ledger.Ledger(":memory:") as book:
        recorded, total, stale = ledger.take_snapshot(book, value_one, ["AAA", "BBB", "CCC", "OLD", "NONE", "MISSING"], PRICES, SNAP)
        assert (recorded, total, stale) == (2, 6, 2)  # OLD has a 60-day-old price and MISSING has none; CCC could not be valued; NONE is not a company
        rows = book.rows(SNAP)
        assert [r["symbol"] for r in rows] == ["AAA", "BBB"]
        assert rows[0]["price"] == 120.0 and rows[0]["low"] == 80.0 and rows[0]["high"] == 140.0 and rows[0]["required_return"] == 0.12 and rows[0]["implied_return"] == 0.09
        assert rows[0]["industry"] == "Steel" and rows[0]["price_date"] == date(2026, 10, 30)
        assert book.snapshot_dates() == [SNAP]


def test_the_ledger_is_never_rewritten():
    with ledger.Ledger(":memory:") as book:
        ledger.take_snapshot(book, value_one, ["AAA"], PRICES, SNAP)
        with pytest.raises(AthenaError, match="already recorded"):
            ledger.take_snapshot(book, value_one, ["AAA", "BBB"], PRICES, SNAP)
        assert len(book.rows(SNAP)) == 1
        later = {k: (v[0], date(2026, 11, 29)) for k, v in PRICES.items()}
        ledger.take_snapshot(book, value_one, ["AAA", "BBB"], later, date(2026, 11, 30))  # a later date is a new snapshot
        assert book.snapshot_dates() == [SNAP, date(2026, 11, 30)]


def test_a_price_dated_after_the_snapshot_is_not_used_and_an_empty_snapshot_is_refused_so_it_can_be_retried():
    with ledger.Ledger(":memory:") as book:
        with pytest.raises(AthenaError, match="nothing to record"):
            ledger.take_snapshot(book, value_one, ["AAA"], {"AAA": (120.0, date(2026, 11, 2))}, SNAP)
        assert book.snapshot_dates() == []
        assert ledger.take_snapshot(book, value_one, ["AAA"], PRICES, SNAP)[0] == 1


def test_scoring_sets_the_recorded_ranges_against_the_later_prices():
    rows = [
        {"price": 100.0, "low": 90.0, "base": 150.0, "high": 200.0, "later": 160.0},  # cheap, rose, inside the range
        {"price": 100.0, "low": 90.0, "base": 140.0, "high": 190.0, "later": 130.0},  # cheap, rose, inside
        {"price": 100.0, "low": 40.0, "base": 60.0, "high": 90.0, "later": 95.0},  # dear, rose a little, above the range
        {"price": 100.0, "low": 40.0, "base": 55.0, "high": 90.0, "later": 30.0},  # dear, fell, below the range
    ] * 3
    got = ledger.score_rows(rows)
    assert got["n"] == 12 and got["ic"] > 0.5  # the cheaper by the model did better
    assert got["inside"] == pytest.approx(0.5) and got["above"] == pytest.approx(0.25) and got["below"] == pytest.approx(0.25)
    assert ledger.score_rows([])["n"] == 0
    assert ledger.score_rows([{"price": 0.0, "low": 1.0, "base": 1.0, "high": 2.0, "later": 1.0}])["n"] == 0  # unusable rows are dropped
