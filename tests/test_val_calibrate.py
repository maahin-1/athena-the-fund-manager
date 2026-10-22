from datetime import date, datetime

import pytest

from athena.reference.store import ReferenceStore
from athena.valuation import calibrate as c
from athena.valuation.industry import IndustryContext
from athena.valuation.scenarios import Policy


def test_ranks_average_ties_and_spearman_is_one_for_a_monotone_pair():
    assert c.ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]
    xs = list(range(20))
    assert c.spearman(xs, [x * x for x in xs]) == pytest.approx(1.0)
    assert c.spearman(xs, [-x for x in xs]) == pytest.approx(-1.0)


def test_spearman_refuses_a_small_or_constant_sample():
    assert c.spearman([1, 2, 3], [3, 2, 1]) is None
    assert c.spearman([1.0] * 20, list(range(20))) is None


def test_quintile_returns_split_by_signal_lowest_first():
    signal = list(range(50))
    forward = [0.0] * 10 + [0.1] * 10 + [0.2] * 10 + [0.3] * 10 + [0.4] * 10
    q = c.quintile_returns(signal, forward)
    assert q == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])
    assert c.quintile_returns(signal[:10], forward[:10]) is None


def test_median_and_quantile():
    assert c.median([3, 1, 2]) == 2
    assert c.median([1, 2, 3, 4]) == 2.5
    assert c.median([]) is None
    assert c.quantile([1, 2, 3, 4, 5], 0.0) == 1 and c.quantile([1, 2, 3, 4, 5], 1.0) == 5


def test_summarize_ic_gives_mean_t_and_share_positive():
    s = c.summarize_ic([0.1, 0.05, None, 0.15, -0.02])
    assert s["n"] == 4 and s["mean"] == pytest.approx(0.07) and s["positive"] == 0.75
    assert s["t"] is not None and s["t"] > 0
    assert c.summarize_ic([None])["mean"] is None


def test_add_months_clamps_to_the_month_end():
    assert c.add_months(date(2024, 8, 31), 6) == date(2025, 2, 28)
    assert c.add_months(date(2024, 12, 31), 6) == date(2025, 6, 30)
    assert c.add_months(date(2025, 6, 30), 12) == date(2026, 6, 30)


def test_adjustment_multiplies_splits_and_bonuses_in_the_window_and_refuses_what_it_cannot_read():
    actions = [(date(2023, 3, 1), "split", 5.0), (date(2023, 9, 1), "bonus", 2.0), (date(2024, 3, 1), "bonus", 1.5)]
    assert c.adjustment(actions, date(2023, 1, 1)) == pytest.approx(15.0)
    assert c.adjustment(actions, date(2023, 1, 1), date(2023, 12, 31)) == pytest.approx(10.0)
    assert c.adjustment(actions, date(2024, 6, 1)) == 1.0
    assert c.adjustment([(date(2023, 3, 1), "demerger", None)], date(2023, 1, 1)) is None
    assert c.adjustment([(date(2023, 3, 1), "split", None)], date(2023, 1, 1)) is None
    assert c.adjustment([(date(2022, 3, 1), "demerger", None)], date(2023, 1, 1)) == 1.0  # before the day: already in the price


def test_shrink_betas_moves_unlevered_toward_the_average_and_raw_toward_one():
    context = IndustryContext("X", "nse_ticker", 1.0, 20, date(2026, 1, 5), {"unlevered_beta": 1.2, "beta": 1.5, "pe": 20.0})
    out = c.shrink_betas(context, 0.8, 0.5)
    assert out.values["unlevered_beta"] == pytest.approx(1.0) and out.values["beta"] == pytest.approx(1.25) and out.values["pe"] == 20.0
    assert c.shrink_betas(context, 0.8, 0.0) is context


def _store() -> ReferenceStore:
    store = ReferenceStore(":memory:")
    store.put_input("inflation_inr", date(2026, 1, 1), 0.04, "test")
    store.put_input("inflation_usd", date(2026, 1, 1), 0.02, "test")
    store.put_input("erp_mature", date(2026, 1, 1), 0.042, "test")
    store.put_country_premiums([("India", "Asia", "Baa3", 0.0175, 0.0692, 0.0272, date(2026, 1, 1)), ("United States", "North America", "Aaa", 0.0, 0.042, 0.0, date(2026, 1, 1))])
    return store


def test_override_store_replaces_only_what_the_variant_sets(tmp_path):
    store = _store()
    store.put_input("tax_marginal", date(2026, 1, 1), 0.2517, "test")
    plain = c.OverrideStore(store, c.Variant("plain"))
    assert plain.input("inflation_inr", date(2026, 6, 1)).value == 0.04
    assert plain.input("tax_marginal", date(2026, 6, 1)).value == 0.2517
    changed = c.OverrideStore(store, c.Variant("x", inflation_inr=0.05, inflation_usd=0.03))
    assert changed.input("inflation_inr", date(2026, 6, 1)).value == 0.05
    assert changed.input("inflation_usd", date(2026, 6, 1)).value == 0.03
    assert changed.input("tax_marginal", date(2026, 6, 1)).value == 0.2517
    assert changed.input("missing_key", date(2026, 6, 1)) is None
    store.close()


def test_policy_defaults_are_the_live_rules():
    p = Policy()
    assert (p.growth_company_weight, p.margin_rule, p.terminal_excess_share, p.terminal_growth, p.minority_at_industry_pb, p.lender_stable_roe) == (0.5, "mean", 0.0, None, False, "industry")


def test_as_of_fundamentals_only_reads_rows_filed_by_the_cutoff_and_divides_shares_by_later_splits():
    import duckdb

    class Base:
        def __init__(self):
            self._con = duckdb.connect(":memory:")
            self._con.execute("CREATE TABLE statement_lines (symbol TEXT, basis TEXT, statement TEXT, period_start DATE, period_end DATE, key TEXT, value DOUBLE, filing_url TEXT, filed TIMESTAMP, source TEXT)")
            rows = [
                ("A", "consolidated", "income", date(2023, 4, 1), date(2024, 3, 31), "sales", 100.0, "u1", datetime(2024, 5, 1), "x"),
                ("A", "consolidated", "income", date(2024, 4, 1), date(2025, 3, 31), "sales", 200.0, "u2", datetime(2025, 5, 1), "x"),
            ]
            self._con.executemany("INSERT INTO statement_lines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

        def company(self, symbol):
            return {"symbol": symbol, "shares": 1000.0, "layout": "indas"}

    store = c.AsOfFundamentals(Base(), datetime(2024, 12, 31), {"A": 10.0})  # type: ignore[arg-type]
    got = store.lines("A", "consolidated", "income")
    assert list(got) == [(date(2023, 4, 1), date(2024, 3, 31))]
    assert store.company("A")["shares"] == 100.0


def test_override_store_sets_the_india_premium_from_a_share_of_the_country_premium_or_outright():
    store = _store()
    live = c.OverrideStore(store, c.Variant("live")).country_premium("India", date(2026, 6, 1))
    assert live[2] == pytest.approx(0.0692)
    half = c.OverrideStore(store, c.Variant("half", erp_crp_share=0.5)).country_premium("India", date(2026, 6, 1))
    assert half[2] == pytest.approx(0.042 + 0.5 * 0.0272) and half[1] == 0.0175
    mature = c.OverrideStore(store, c.Variant("mature", erp_crp_share=0.0)).country_premium("India", date(2026, 6, 1))
    assert mature[2] == pytest.approx(0.042)
    fixed = c.OverrideStore(store, c.Variant("fixed", erp_total=0.055)).country_premium("India", date(2026, 6, 1))
    assert fixed[2] == 0.055
    assert c.OverrideStore(store, c.Variant("half", erp_crp_share=0.5)).country_premium("United States", date(2026, 6, 1))[2] == 0.042  # only India is changed
    store.close()
