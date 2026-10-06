from dataclasses import replace

from dash_fakes import trend_runs

from athena.backtest.rules import RULES
from athena.backtest.summary import num, pct
from athena.dashboard.backtest_view import build_backtest_view

RUNS = list(trend_runs())


def test_there_is_one_panel_per_rule_with_the_description_the_table_and_the_curve():
    view = build_backtest_view("SBIN", RUNS, "assumed costs")
    assert view.identifier == "SBIN" and view.assumptions == "assumed costs"
    assert [panel.rule for panel in view.panels] == ["trend", "persona"]
    trend = view.panels[0]
    assert trend.description == RULES["trend"].description
    assert [row[0] for row in trend.rows] == ["Total return", "Yearly return", "Worst drawdown", "Sharpe"]
    assert trend.figure.layout.title.text == "SBIN: trend rule against buy and hold" and len(trend.figure.data) == 4


def test_the_table_shows_the_strategy_beside_buy_and_hold_in_the_right_columns():
    summary = RUNS[0].summary
    rows = {measure: (strategy, hold) for measure, strategy, hold in build_backtest_view("SBIN", RUNS, "").panels[0].rows}
    assert rows["Total return"] == (pct(summary.total_return), pct(summary.benchmark_total_return))
    assert rows["Worst drawdown"] == (pct(summary.max_drawdown), pct(summary.benchmark_max_drawdown))
    assert rows["Sharpe"] == (num(summary.sharpe), num(summary.benchmark_sharpe))
    assert rows["Total return"][0] != rows["Total return"][1]  # the two columns are not the same figure twice


def test_the_facts_give_the_window_the_market_and_the_trade_statistics():
    summary = RUNS[0].summary
    window, trades = build_backtest_view("SBIN", RUNS, "").panels[0].facts
    assert f"{summary.start} to {summary.end} ({summary.bars} trading days)" in window
    assert f"excess over buy and hold {pct(summary.excess_return)}" in window
    assert f"the market (NIFTY 50) returned {pct(summary.index_total_return)}" in window
    assert "1 trades (0 stopped out)" in trades and f"in the market {pct(summary.time_in_market)} of days" in trades


def test_a_rule_that_never_trades_shows_not_available_instead_of_failing():
    persona = build_backtest_view("SBIN", RUNS, "").panels[1]
    assert "0 trades (0 stopped out)" in persona.facts[1] and "win rate n/a" in persona.facts[1]


def test_the_market_comparison_is_left_out_when_the_market_series_is_missing():
    bare = replace(RUNS[0], summary=replace(RUNS[0].summary, index_total_return=None))
    assert "NIFTY 50" not in build_backtest_view("SBIN", [bare], "").panels[0].facts[0]


def test_the_blinding_result_is_carried_through_per_rule():
    runs = [RUNS[0], replace(RUNS[1], blinded_identical=False)]
    assert [panel.blinded_identical for panel in build_backtest_view("SBIN", runs, "").panels] == [True, False]
