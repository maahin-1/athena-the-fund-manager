import plotly.graph_objects as go
import pytest

from athena.dashboard.charts import RSI_OVERBOUGHT, RSI_OVERSOLD, price_chart, rsi_chart
from athena.technicals.candles import candles_from_bars
from athena.technicals.series import indicator_series
from bar_factory import make_bars

CANDLES = candles_from_bars(make_bars([100.0 + i * 0.5 for i in range(260)]))
SERIES = indicator_series(CANDLES)


def traces_by_name(figure):
    return {trace.name: trace for trace in figure.data}


def test_price_chart_has_candles_averages_bands_and_volume():
    figure = price_chart(CANDLES, SERIES, "SBIN")
    traces = traces_by_name(figure)
    assert list(traces) == ["Price", "SMA 50", "SMA 200", "Bollinger upper", "Bollinger lower", "Volume"]
    assert isinstance(traces["Price"], go.Candlestick) and isinstance(traces["Volume"], go.Bar)
    assert figure.layout.title.text == "SBIN"


def test_every_trace_has_one_point_per_candle_and_values_come_from_the_data():
    traces = traces_by_name(price_chart(CANDLES, SERIES, "SBIN"))
    for name, trace in traces.items():
        assert len(trace.x) == len(CANDLES), name
    assert traces["Price"].close[-1] == CANDLES[-1].close
    assert traces["SMA 50"].y[-1] == pytest.approx(SERIES.sma_50[-1])
    assert traces["Volume"].y[0] == CANDLES[0].volume


def test_volume_bars_are_coloured_by_candle_direction():
    traces = traces_by_name(price_chart(CANDLES, SERIES, "x"))
    assert set(traces["Volume"].marker.color) == {"#2e9e6b"}  # a steady climb: every candle closes up


def test_rsi_chart_has_the_series_and_the_two_reference_lines():
    figure = rsi_chart(CANDLES, SERIES)
    assert len(figure.data) == 1 and figure.data[0].name == "RSI 14"
    levels = sorted(shape.y0 for shape in figure.layout.shapes)
    assert levels == [RSI_OVERSOLD, RSI_OVERBOUGHT]
    assert tuple(figure.layout.yaxis.range) == (0, 100)


def test_charts_refuse_empty_or_misaligned_input():
    with pytest.raises(ValueError, match="no candles"):
        price_chart([], indicator_series([]), "x")
    with pytest.raises(ValueError, match="not aligned"):
        rsi_chart(CANDLES[:-1], SERIES)
