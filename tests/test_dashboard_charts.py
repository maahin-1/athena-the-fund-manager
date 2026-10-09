import plotly.graph_objects as go
import pytest
from dash_fakes import trend_runs

from athena.backtest.engine import BacktestResult, Config
from athena.dashboard.charts import build_chart, equity_chart, returns_chart, signal_share_chart, specialist_chart
from athena.dashboard.view import SpecialistRow
from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import Selected, compute, default_params, default_selection
from bar_factory import make_bars

CANDLES = candles_from_bars(make_bars([100.0 + i * 0.5 for i in range(260)]))


def chosen(key, **params):
    return Selected(f"{key}-1", key, {**default_params(key), **params})


def traces_by_name(figure):
    return {trace.name: trace for trace in figure.data}


def test_the_default_chart_has_candles_averages_bands_volume_and_rsi_on_one_figure():
    figure = build_chart(CANDLES, default_selection(), "SBIN")
    traces = traces_by_name(figure)
    assert list(traces) == ["Price", "SMA 50", "SMA 200", "Bollinger upper (20, 2)", "Bollinger lower (20, 2)", "Volume", "RSI 14"]
    assert isinstance(traces["Price"], go.Candlestick) and isinstance(traces["Volume"], go.Bar)
    assert figure.layout.title.text == "SBIN"
    assert [traces[n].yaxis for n in ("Price", "SMA 50", "Volume", "RSI 14")] == ["y", "y", "y2", "y3"]  # price, volume, RSI rows
    bands = [traces["Bollinger upper (20, 2)"], traces["Bollinger lower (20, 2)"]]
    assert {b.line.color for b in bands} == {"#8a8f98"} and {b.line.dash for b in bands} == {"dot"}  # grey and dotted, as ever


def test_every_trace_has_one_point_per_candle_and_values_come_from_the_data():
    traces = traces_by_name(build_chart(CANDLES, default_selection(), "SBIN"))
    for name, trace in traces.items():
        assert len(trace.x) == len(CANDLES), name
    assert traces["Price"].close[-1] == CANDLES[-1].close
    assert traces["SMA 50"].y[-1] == pytest.approx(compute(CANDLES, default_selection()[0])[0].values[-1])
    assert traces["Volume"].y[0] == CANDLES[0].volume


def test_volume_bars_are_coloured_by_candle_direction():
    traces = traces_by_name(build_chart(CANDLES, [], "x"))
    assert set(traces["Volume"].marker.color) == {"#2e9e6b"}  # a steady climb: every candle closes up


def test_the_volume_row_can_be_left_out_and_the_panes_move_up():
    traces = traces_by_name(build_chart(CANDLES, [chosen("rsi")], "x", show_volume=False))
    assert list(traces) == ["Price", "RSI 14"] and traces["RSI 14"].yaxis == "y2"
    assert build_chart(CANDLES, [], "x", show_volume=False).layout.height < build_chart(CANDLES, [], "x").layout.height  # no empty row left


def test_each_pane_indicator_gets_its_own_row_with_its_reference_lines_and_range():
    selection = [chosen("rsi"), chosen("macd"), chosen("stoch")]
    figure = build_chart(CANDLES, selection, "x")
    traces = traces_by_name(figure)
    assert [traces[n].yaxis for n in ("Volume", "RSI 14", "MACD", "%K")] == ["y2", "y3", "y4", "y5"]
    assert isinstance(traces["Histogram"], go.Bar) and traces["Histogram"].yaxis == "y4"
    assert len(figure.layout.shapes) == 2 + 1 + 2  # RSI 30 and 70, the MACD zero line, stochastic 20 and 80
    assert tuple(figure.layout.yaxis3.range) == (0, 100) and tuple(figure.layout.yaxis5.range) == (0, 100)
    assert figure.layout.height > build_chart(CANDLES, [], "x").layout.height  # more rows, taller figure


def test_overlays_share_the_price_axis_and_are_told_apart_by_colour_and_style():
    selection = [chosen("sma", length=20), chosen("ema", length=20), chosen("sar"), chosen("channel", length=60)]
    selection = [Selected(f"{item.key}-{n}", item.key, item.params) for n, item in enumerate(selection)]
    traces = traces_by_name(build_chart(CANDLES, selection, "x"))
    for name in ("SMA 20", "EMA 20", "Parabolic SAR", "High 60", "Low 60"):
        assert traces[name].yaxis == "y", name
    assert traces["Parabolic SAR"].mode == "markers" and traces["High 60"].line.dash == "dot"
    colors = [traces[n].line.color for n in ("SMA 20", "EMA 20")]
    assert len(set(colors)) == 2  # two overlays are never drawn in the same colour


def test_the_chart_refuses_no_candles_and_a_choice_that_cannot_be_drawn():
    with pytest.raises(ValueError, match="no candles"):
        build_chart([], default_selection(), "x")
    with pytest.raises(ValueError, match="Length must be between"):
        build_chart(CANDLES, [Selected("sma-1", "sma", {"length": 1})], "x")


def test_equity_chart_draws_both_curves_and_marks_each_trade_on_the_strategy_curve():
    result = trend_runs()[0].result
    traces = traces_by_name(equity_chart(result, "SBIN trend"))
    assert list(traces) == ["Strategy", "Buy and hold", "Entry", "Exit"]
    assert list(traces["Strategy"].y) == list(result.equity) and list(traces["Buy and hold"].y) == list(result.benchmark)
    trade = result.trades[0]
    level = dict(zip(result.days, result.equity))
    assert list(traces["Entry"].x) == [trade.entry_day] and list(traces["Entry"].y) == [level[trade.entry_day]]
    assert list(traces["Exit"].x) == [trade.exit_day] and list(traces["Exit"].y) == [level[trade.exit_day]]


def test_equity_chart_for_a_rule_that_never_trades_has_empty_marker_traces():
    result = trend_runs()[1].result
    traces = traces_by_name(equity_chart(result, "SBIN persona"))
    assert result.trades == () and len(traces["Entry"].x) == 0 and len(traces["Exit"].x) == 0 and len(traces["Strategy"].x) == len(result.days)


def test_equity_chart_refuses_a_result_without_days():
    with pytest.raises(ValueError, match="no days"):
        equity_chart(BacktestResult("x", Config(), (), (), (), (), ()), "x")


def specialist_row(name="quant_technical", signal="bullish", confidence=72, coverage="full"):
    return SpecialistRow(name, signal, confidence, coverage, "because", ())


def test_specialist_bars_point_right_for_bullish_left_for_bearish_and_skip_the_ones_without_a_view():
    rows = [specialist_row("a", "bullish", 70), specialist_row("b", "bearish", 40), specialist_row("c", "neutral", 55), specialist_row("d", "bullish", 90, "insufficient")]
    bars = specialist_chart(rows).data[0]
    assert list(bars.y) == ["a", "b", "c"] and list(bars.x) == [70, -40, 0]
    assert list(bars.marker.color) == ["#2e9e6b", "#d8574b", "#8a8f98"]


def test_there_is_no_specialist_chart_when_nobody_formed_a_view():
    assert specialist_chart([]) is None
    assert specialist_chart([specialist_row(coverage="insufficient")]) is None


def test_the_donut_splits_weight_by_confidence_scaled_by_coverage_and_leaves_out_empty_sides():
    rows = [specialist_row("a", "bullish", 60), specialist_row("b", "bullish", 50, "partial"), specialist_row("c", "bearish", 40)]
    pie = signal_share_chart(rows).data[0]
    assert list(pie.labels) == ["bullish", "bearish"]
    assert list(pie.values) == pytest.approx([60 + 50 * 0.7, 40])
    assert signal_share_chart([specialist_row(coverage="insufficient")]) is None


def test_return_bars_use_the_close_that_many_trading_days_back_and_skip_horizons_the_history_lacks():
    bars = returns_chart(CANDLES).data[0]
    assert list(bars.x) == ["1M", "3M", "6M", "1Y"]
    assert bars.y[0] == pytest.approx((CANDLES[-1].close / CANDLES[-22].close - 1) * 100)
    assert bars.y[3] == pytest.approx((CANDLES[-1].close / CANDLES[-253].close - 1) * 100)
    assert list(returns_chart(CANDLES[-100:]).data[0].x) == ["1M", "3M"]
    assert returns_chart(CANDLES[-10:]) is None


def test_a_fall_is_drawn_in_the_down_colour():
    falling = candles_from_bars(make_bars([300.0 - i for i in range(100)]))
    assert list(returns_chart(falling).data[0].marker.color) == ["#d8574b", "#d8574b"]
