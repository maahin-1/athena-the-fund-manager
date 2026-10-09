import math
from datetime import date, timedelta

import pytest

from athena.adapters.prices import JugaadPriceAdapter
from athena.risk_overlay.apply import apply_overlay
from athena.risk_overlay.checks import figures_from_bars, run_checks
from athena.risk_overlay.model import PRESETS, Holding, Limit, Overlay, RiskProfile

pytestmark = pytest.mark.live

VERDICT = {"verdict": "Buy", "conviction": 70, "key_risks": [], "resolution_path": "blend"}


@pytest.fixture(scope="module")
def adapter():
    return JugaadPriceAdapter()


def bars_for(adapter, symbol):
    end = date.today()
    return adapter.fetch_ohlcv(symbol, "1d", since=end - timedelta(days=760), until=end)


@pytest.mark.parametrize("symbol", ["SBIN", "HDFCBANK", "IDEA"])
def test_live_every_figure_is_real_and_in_a_sensible_range(adapter, symbol):
    figures = figures_from_bars(bars_for(adapter, symbol))
    assert figures.reasons == {} and figures.window == "last 252 daily returns"
    for value in (figures.volatility, figures.drawdown, figures.var_95, figures.cvar_95, figures.traded_value):
        assert value is not None and math.isfinite(value) and value > 0
    assert 0.05 < figures.volatility < 1.5 and 0 < figures.drawdown < 1 and figures.var_95 < figures.cvar_95 < 0.5
    assert figures.traded_value > 1e7  # a listed large cap trades more than a crore of rupees a day


def test_live_a_stock_is_held_to_the_limits_on_its_own_measured_risk(adapter):
    figures = figures_from_bars(bars_for(adapter, "IDEA"))
    strict = RiskProfile("strict", {"volatility": Limit(figures.volatility / 3, figures.volatility / 2), "var_95": Limit(figures.var_95 / 3, figures.var_95 / 2)})
    loose = RiskProfile("loose", {"volatility": Limit(figures.volatility * 2, figures.volatility * 3), "var_95": Limit(figures.var_95 * 2, figures.var_95 * 3)})
    assert {f.status for f in run_checks(figures, Overlay(strict), "IDEA")} == {"breach"}
    assert {f.status for f in run_checks(figures, Overlay(loose), "IDEA")} == {"ok"}
    held = apply_overlay(VERDICT, run_checks(figures, Overlay(strict), "IDEA"))
    assert held["verdict"] == "Hold" and held["pre_overlay_verdict"] == "Buy"
    print("\n" + "\n".join(held["key_risks"]))
    assert apply_overlay(VERDICT, run_checks(figures, Overlay(loose), "IDEA"))["verdict"] == "Buy"


def test_live_an_amount_and_holdings_are_checked_against_the_real_traded_value(adapter):
    figures = figures_from_bars(bars_for(adapter, "SBIN"))
    amount = figures.traded_value * 0.10  # a tenth of a normal day's trading: far more than any preset allows
    overlay = Overlay(PRESETS["moderate"], (Holding("INFY", 900_000.0),), amount)
    found = {f.check: f for f in run_checks(figures, overlay, "SBIN")}
    assert found["liquidity"].status == "breach" and found["liquidity"].value == pytest.approx(0.10)
    assert found["position"].status == "breach" and found["concentration"].status in ("warn", "breach")
    small = Overlay(PRESETS["moderate"], (Holding("INFY", 900_000.0),), 10_000.0)
    assert {f.check: f.status for f in run_checks(figures, small, "SBIN")}["liquidity"] == "ok"
