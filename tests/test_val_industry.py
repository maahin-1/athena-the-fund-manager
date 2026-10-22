from datetime import date

from athena.reference.store import ReferenceStore, StatRow
from athena.valuation import industry as ind


def stat(industry, dataset, metric, value, n=185, as_of=date(2026, 1, 5)):
    return StatRow("damodaran", "India", dataset, industry, metric, value, n, as_of, f"{dataset}India.xls")


def store_with(rows, mapping=("Steel", "nse_ticker", 1.0)):
    store = ReferenceStore(":memory:")
    store.put_stats(rows)
    store.put_company_industries([("JSL", *mapping, date(2026, 1, 5))])
    return store


STEEL = [
    stat("Steel", "pe", "aggregate_mkt_cap_trailing_net_income_only_money_making_firms", 26.87),
    stat("Steel", "pe", "trailing_pe", 45.67),
    stat("Steel", "pbv", "pbv", 2.56),
    stat("Steel", "roe", "roe_unadjusted", 0.09),
    stat("Steel", "margin", "pre_tax_unadjusted_operating_margin", 0.0918),
]


def test_the_benchmarks_use_his_aggregate_pe_and_carry_the_firm_count_and_the_table_date():
    with store_with(STEEL) as store:
        ctx = ind.lookup(store, "JSL", date(2026, 10, 22))
    assert (ctx.industry, ctx.n_firms, ctx.data_as_of, ctx.mapping_method) == ("Steel", 185, date(2026, 1, 5), "nse_ticker")
    assert ctx.get("pe") == 26.87 and ctx.get("pe_simple_average") == 45.67 and ctx.get("pb") == 2.56
    assert ctx.flags == ()
    assert ctx.get("ev_ebitda") is None  # a figure the tables do not give is absent, not zero


def test_a_symbol_outside_the_map_or_before_the_first_table_gets_nothing():
    with store_with(STEEL) as store:
        assert ind.lookup(store, "NOPE", date(2026, 10, 22)) is None
        assert ind.lookup(store, "JSL", date(2025, 12, 31)) is None  # the table was published after that date


def test_an_implausible_multiple_is_set_aside_with_a_flag_and_the_rest_stay():
    rows = STEEL + [stat("Steel", "vebitda", "ev_ebitda", 400.0)]
    with store_with(rows) as store:
        ctx = ind.lookup(store, "JSL", date(2026, 10, 22))
    assert ctx.get("ev_ebitda") is None and ctx.get("pe") == 26.87
    assert any("ev_ebitda set aside" in f for f in ctx.flags)


def test_a_thin_industry_a_weak_match_and_an_old_table_are_each_flagged():
    rows = [stat("Steel", d, m, v, n=6) for d, m, v in (("pbv", "pbv", 2.0), ("roe", "roe_unadjusted", 0.1))]
    with store_with(rows, mapping=("Steel", "crosswalk", 0.4)) as store:
        ctx = ind.lookup(store, "JSL", date(2028, 1, 1))
    text = " | ".join(ctx.flags)
    assert "small sample: 6 firms" in text and "crosswalk at confidence 0.40" in text and "more than 540 days old" in text


def test_lenders_and_insurers_are_recognised_by_industry_name():
    assert all(ind.is_financial(n) for n in ("Bank (Money Center)", "Banks (Regional)", "Insurance (Life)", "Financial Svcs. (Non-bank & Insurance)", "Brokerage & Investment Banking"))
    assert not any(ind.is_financial(n) for n in ("Steel", "Computer Services", "Real Estate (Development)"))
