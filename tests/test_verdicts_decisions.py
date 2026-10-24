from datetime import date, datetime, timedelta

import pytest

from athena.verdicts import decisions


def verdict(rating="Buy", **extra):
    return {"verdict": rating, "conviction": 40, **extra}


def test_a_rating_becomes_buy_sell_or_hold_when_enough_analysts_agree_to_act():
    assert decisions.decide(verdict("Overweight"), ["a", "b", "c"], None)["type"] == decisions.BUY
    assert decisions.decide(verdict("Underweight"), ["a", "b"], None)["type"] == decisions.SELL
    assert decisions.decide(verdict("Hold"), ["a", "b"], None)["type"] == decisions.HOLD


def test_review_wins_when_analysts_disagree_too_few_formed_a_view_or_the_verdict_was_held_back():
    contested = decisions.decide(verdict("Hold"), ["a", "b"], {"bullish": ["a"], "bearish": ["b"]})
    assert contested["type"] == decisions.REVIEW and "disagree" in contested["reason"]
    assert decisions.decide(verdict("Buy"), ["a"], None)["type"] == decisions.REVIEW
    held = decisions.decide(verdict("Hold", held_back_by="price data"), ["a", "b"], None)
    assert held["type"] == decisions.REVIEW and "price data" in held["reason"]
    assert decisions.decide(verdict("Buy"), ["a", "b"], None)["settle_after_trading_days"] == [5, 20, 60]


DAYS = [date(2026, 9, 1) + timedelta(days=i) for i in range(80)]
STOCK = [(d, 100.0 * (1.01 ** i)) for i, d in enumerate(DAYS)]  # up 1% a day
BENCH = {d: 1000.0 * (1.002 ** i) for i, d in enumerate(DAYS)}  # up 0.2% a day


def test_settling_sets_the_stock_against_the_benchmark_over_the_trading_days_after_the_verdict():
    got = decisions.settle_one(DAYS[10], STOCK, BENCH)
    assert set(got) == {5, 20, 60}
    assert got[5]["stock"] == pytest.approx(1.01 ** 5 - 1) and got[5]["benchmark"] == pytest.approx(1.002 ** 5 - 1)
    assert got[20]["excess"] == pytest.approx((1.01 ** 20 - 1) - (1.002 ** 20 - 1))


def test_a_horizon_not_reached_yet_is_absent_and_an_unknown_day_settles_nothing():
    assert set(decisions.settle_one(DAYS[70], STOCK, BENCH)) == {5}
    assert decisions.settle_one(date(2020, 1, 1), STOCK, BENCH) == {}
    assert decisions.settle_one(DAYS[79], STOCK, BENCH) == {}


def test_the_summary_groups_by_decision_type_and_says_how_often_buys_beat_and_sells_fell():
    def row(kind, excess):
        return {"decision": {"type": kind}, "outcomes": {5: {"excess": excess}}}

    summary = decisions.summarize([row("BUY", 0.02), row("BUY", -0.01), row("BUY", 0.03), row("SELL", -0.02), row("SELL", 0.01), row("REVIEW", 0.5)])
    assert summary["BUY"]["5"]["n"] == 3 and summary["BUY"]["5"]["went_the_way_said"] == pytest.approx(2 / 3)
    assert summary["SELL"]["5"]["went_the_way_said"] == 0.5 and summary["REVIEW"]["5"]["went_the_way_said"] is None
    assert "BUY after 5 trading days: 3 settled" in decisions.format_summary(summary, 6)
    assert "none old enough" in decisions.format_summary({}, 2)


def test_settling_all_uses_the_stored_decision_or_works_one_out_for_an_older_verdict():
    payload = {"verdict": verdict("Buy"), "taking_part": ["a", "b"], "conflict": None}
    history = {"ABC": [{"made_at": datetime(2026, 9, 11), "data_as_of": DAYS[10], "payload": payload}]}
    rows = decisions.settle_all(history, lambda symbol: STOCK, BENCH)
    assert rows[0]["decision"]["type"] == "BUY" and set(rows[0]["outcomes"]) == {5, 20, 60}


def test_a_buy_is_also_given_its_excess_return_after_the_cost_of_buying_and_selling_it_and_a_sell_is_not():
    def row(kind, excess):
        return {"decision": {"type": kind}, "outcomes": {20: {"excess": excess}}}

    summary = decisions.summarize([row("BUY", 0.03), row("BUY", 0.01), row("SELL", -0.02)], buy_cost=0.005)
    assert summary["BUY"]["20"]["mean_excess"] == pytest.approx(0.02) and summary["BUY"]["20"]["mean_excess_after_costs"] == pytest.approx(0.015)
    assert "mean_excess_after_costs" not in summary["SELL"]["20"]
    assert "+1.5% after costs" in decisions.format_summary(summary, 3)
