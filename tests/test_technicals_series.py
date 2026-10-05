
import pytest
from bar_factory import NOW, make_bars

from athena.technicals.candles import candles_from_bars
from athena.technicals.packet import build_technical_packet
from athena.technicals.series import indicator_series

CLOSES = [100.0 + i * 0.5 + (3 if i % 7 == 0 else 0) for i in range(300)]
BARS = make_bars(CLOSES)
CANDLES = candles_from_bars(BARS)
SERIES = indicator_series(CANDLES)


def test_every_series_is_aligned_with_the_candles():
    assert SERIES.days == tuple(c.day for c in CANDLES)
    for name in ("sma_50", "sma_200", "bb_upper", "bb_middle", "bb_lower", "rsi_14"):
        assert len(getattr(SERIES, name)) == len(CANDLES), name


def test_warmup_values_are_none_and_the_rest_are_floats():
    assert SERIES.sma_50[:49] == (None,) * 49 and SERIES.sma_50[49] is not None
    assert SERIES.sma_200[:199] == (None,) * 199 and SERIES.sma_200[199] is not None
    assert SERIES.bb_upper[:19] == (None,) * 19 and SERIES.bb_upper[19] is not None
    assert SERIES.rsi_14[:14] == (None,) * 14 and SERIES.rsi_14[14] is not None
    assert all(isinstance(v, float) for v in SERIES.sma_50[49:])


def test_bands_straddle_the_middle_band():
    for upper, middle, lower in zip(SERIES.bb_upper, SERIES.bb_middle, SERIES.bb_lower):
        if upper is not None:
            assert lower < middle < upper


def test_the_last_values_match_what_the_technical_packet_reports():
    metrics = build_technical_packet("X", NOW, BARS)["metrics"]
    assert SERIES.sma_50[-1] == pytest.approx(metrics["sma_50"]["value"], abs=1e-3)
    assert SERIES.sma_200[-1] == pytest.approx(metrics["sma_200"]["value"], abs=1e-3)
    assert SERIES.rsi_14[-1] == pytest.approx(metrics["rsi_14"]["value"], abs=1e-3)
    pct_b = (CLOSES[-1] - SERIES.bb_lower[-1]) / (SERIES.bb_upper[-1] - SERIES.bb_lower[-1])
    assert pct_b == pytest.approx(metrics["bollinger_pct_b"]["value"], abs=1e-3)


def test_a_short_history_is_all_none_not_an_error():
    short = indicator_series(candles_from_bars(make_bars(CLOSES[:10])))
    assert len(short.days) == 10 and set(short.sma_50) == {None} and set(short.rsi_14[:14]) == {None}
    assert indicator_series([]).days == ()
