import math
from datetime import date, timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.dashboard.charts import build_chart
from athena.technicals.candles import candles_from_bars
from athena.technicals.indicators import REGISTRY, Selected, compute, default_params, validate

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def candles():
    end = date.today()
    bars = JugaadPriceAdapter().fetch_ohlcv("SBIN", "1d", since=end - timedelta(days=760), until=end)
    return candles_from_bars(bars)


def test_live_every_indicator_computes_on_real_prices_and_volume(candles):
    assert len(candles) > 400
    for key in REGISTRY:
        for line in compute(candles, Selected(f"{key}-1", key, default_params(key))):
            real = [v for v in line.values if v is not None]
            assert real and all(math.isfinite(v) for v in real), (key, line.name)


def test_live_a_chart_with_many_overlays_and_panes_can_be_built(candles):
    selection = [Selected(f"{key}-1", key, default_params(key)) for key in REGISTRY]
    valid, problems = validate(selection[:8])
    assert not problems
    assert len(build_chart(candles, valid, "SBIN").data) > 8
    panes = [item for item in selection if REGISTRY[item.key].placement == "pane"][:6]
    assert len(build_chart(candles, panes, "SBIN").data) > 6
