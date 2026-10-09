import math
from dataclasses import replace

import pytest
from bar_factory import make_bars

from athena.evaluation.schema import validate_judge_verdict
from athena.risk_overlay.apply import HELD_BACK, apply_overlay
from athena.risk_overlay.checks import Figures, figures_from_bars, run_checks
from athena.risk_overlay.model import MEASURES, PRESETS, Finding, Holding, Limit, Overlay, RiskProfile


def alternating(count, up=1.01, down=0.99):
    closes, level = [100.0], 100.0
    for i in range(count):
        level *= up if i % 2 == 0 else down
        closes.append(level)
    return closes


def test_volatility_and_the_loss_figures_come_from_the_last_252_daily_returns():
    bars = make_bars([50.0] * 40 + alternating(400), volume=1000.0)
    figures = figures_from_bars(bars)
    expected = math.sqrt(252 * 0.01**2 / 251) * math.sqrt(252)
    assert figures.window == "last 252 daily returns"
    assert figures.volatility == pytest.approx(expected, rel=1e-6)
    assert figures.var_95 == pytest.approx(0.01, rel=1e-6) and figures.cvar_95 == pytest.approx(0.01, rel=1e-6)
    assert figures.reasons == {}


def test_the_drawdown_is_the_worst_fall_as_a_positive_size():
    closes = [100.0] * 10 + [120.0, 90.0] + [100.0] * 150
    figures = figures_from_bars(make_bars(closes))
    assert figures.drawdown == pytest.approx(0.25)


STOCK_CHECKS = ("volatility", "drawdown", "var_95", "cvar_95")


def test_fewer_than_126_daily_returns_leave_every_stock_figure_unchecked_with_the_count():
    figures = figures_from_bars(make_bars(alternating(100)))
    assert (figures.volatility, figures.drawdown, figures.var_95, figures.cvar_95) == (None, None, None, None)
    assert figures.returns == 100
    for check in STOCK_CHECKS:
        assert figures.reasons[check] == "only 100 daily returns; at least 126 are needed for a risk check"
    found = findings_for(figures, Overlay(PRESETS["moderate"]))
    assert {found[check].status for check in STOCK_CHECKS} == {"unchecked"}
    assert found["volatility"].message == "Volatility not checked: only 100 daily returns; at least 126 are needed for a risk check."
    assert figures_from_bars(make_bars(alternating(125))).volatility is None
    assert figures_from_bars(make_bars(alternating(126))).volatility is not None


def test_between_126_and_251_returns_the_stock_findings_say_they_cover_less_than_a_year():
    figures = figures_from_bars(make_bars(alternating(150)))
    assert figures.returns == 150 and figures.reasons == {}
    overlay = Overlay(PRESETS["moderate"], (Holding("A", 100.0),), 100.0)
    found = findings_for(replace(figures, traded_value=1e9), overlay)
    for check in STOCK_CHECKS:
        assert found[check].status == "ok"
        assert found[check].message.endswith(" (measured over the last 150 daily returns, less than a year).")
    assert "less than a year" not in found["liquidity"].message and "less than a year" not in found["position"].message
    assert found["volatility"].message.startswith("Volatility 15.9% is within your limits (warning at 35.0%)")


def test_a_full_year_of_returns_keeps_the_plain_message():
    figures = figures_from_bars(make_bars(alternating(300)))
    assert figures.returns == 252
    found = findings_for(figures, Overlay(profile(volatility=(0.2, 0.3))))
    assert found["volatility"].message == "Volatility 15.9% is within your limits (warning at 20.0%)."


def calm(count=300):
    return [100.0 * (1 + 0.004 * math.sin(i * 0.7) + 0.0002 * i) for i in range(count)]


def test_a_bonus_inside_the_window_is_adjusted_away_and_the_figures_say_so():
    closes, cut = calm(), 220
    plain = make_bars(closes, volume=1000.0)
    broken = [replace(bar, open=bar.open * 2, high=bar.high * 2, low=bar.low * 2, close=bar.close * 2, volume=500.0) for bar in plain[:cut]] + plain[cut:]
    figures, expected = figures_from_bars(broken), figures_from_bars(plain)
    assert expected.adjustment_note == "" and expected.volatility < 0.1
    assert replace(figures, adjustment_note="") == expected
    assert figures.adjustment_note.startswith("Prices adjusted for 1 split or bonus event(s)") and "(x0.5)" in figures.adjustment_note


def test_the_liquidity_figure_uses_the_adjusted_volume():
    plain = make_bars([100.0] * 200, volume=1000.0)
    broken = [replace(bar, open=200.0, close=200.0, volume=500.0) for bar in plain[:190]] + plain[190:]
    assert figures_from_bars(broken).traded_value == pytest.approx(100.0 * 1000.0)


@pytest.mark.parametrize("bad", [float("nan"), 0.0, -5.0, float("inf")])
def test_a_bad_close_in_the_middle_is_dropped_never_read_as_a_price(bad):
    bars = make_bars(calm())
    holed = bars[:150] + [replace(bars[150], close=bad)] + bars[151:]
    figures = figures_from_bars(holed)
    assert figures == figures_from_bars(bars[:150] + bars[151:])
    assert all(math.isfinite(value) for value in (figures.volatility, figures.drawdown, figures.var_95, figures.cvar_95))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_bar_with_a_volume_that_is_not_a_number_is_dropped_whole(bad):
    bars = make_bars(calm())
    holed = bars[:150] + [replace(bars[150], close=bars[150].close * 1.3, volume=bad)] + bars[151:]
    assert figures_from_bars(holed) == figures_from_bars(bars[:150] + bars[151:])


def test_a_zero_close_and_a_missing_volume_never_raise():
    bars = make_bars(calm())
    figures = figures_from_bars([replace(bars[0], close=0.0, open=0.0)] + bars[1:-1] + [replace(bars[-1], volume=float("nan"))])
    assert figures.volatility is not None and math.isfinite(figures.traded_value)
    assert figures_from_bars([replace(bar, close=0.0) for bar in bars]).reasons["volatility"].startswith("only 0 daily returns")


def test_a_figure_that_is_not_a_number_is_unchecked_never_within_the_limits():
    for value in (float("nan"), float("inf")):
        found = findings_for(Figures(volatility=value), Overlay(profile(volatility=(0.2, 0.3))))["volatility"]
        assert found.status == "unchecked" and found.value is None
        assert found.message == "Volatility not checked: the price history has gaps."


def test_holdings_too_large_to_add_up_leave_the_money_checks_unchecked():
    overlay = Overlay(profile(position=(0.1, 0.2), concentration=(0.2, 0.4)), (Holding("A", 1e308), Holding("B", 1e308)), 1e308)
    found = findings_for(Figures(), overlay)
    assert {f.status for f in found.values()} == {"unchecked"}
    assert all("too large" in f.message for f in found.values())


def test_an_infinite_traded_value_leaves_liquidity_unchecked():
    found = findings_for(Figures(traded_value=float("inf")), Overlay(profile(liquidity=(0.02, 0.05)), amount=1000.0))
    assert found["liquidity"].status == "unchecked"


def with_volume(bar, volume):
    return replace(bar, volume=volume)


def test_traded_value_is_the_median_of_the_last_twenty_days_so_one_huge_day_does_not_count():
    bars = make_bars([100.0] * 40)
    figures = figures_from_bars(bars[:-1] + [with_volume(bars[-1], 10**9)])
    assert figures.traded_value == pytest.approx(100.0 * 1000.0)


def test_traded_value_ignores_days_before_the_last_twenty():
    bars = make_bars([100.0] * 60, volume=1000.0)
    figures = figures_from_bars([with_volume(bar, 50000.0) for bar in bars[:30]] + bars[30:])
    assert figures.traded_value == pytest.approx(100.0 * 1000.0)


def test_no_volume_is_reported_not_treated_as_zero_risk():
    figures = figures_from_bars(make_bars([100.0 + i for i in range(40)], volume=0.0))
    assert figures.traded_value is None and figures.reasons["liquidity"] == "there is no volume data"
    assert figures_from_bars([]).reasons["liquidity"] == "there is no volume data"


def profile(**limits):
    return RiskProfile("t", {name: Limit(*pair) for name, pair in limits.items()})


def findings_for(figures, overlay, symbol="SBIN"):
    return {f.check: f for f in run_checks(figures, overlay, symbol)}


@pytest.mark.parametrize(
    "value, status",
    [(0.10, "ok"), (0.1999, "ok"), (0.20, "warn"), (0.25, "warn"), (0.30, "breach"), (0.90, "breach")],
)
def test_a_stock_figure_warns_at_its_warning_level_and_breaches_at_its_hard_limit(value, status):
    found = findings_for(Figures(volatility=value), Overlay(profile(volatility=(0.2, 0.3))))["volatility"]
    assert (found.status, found.value, found.warn, found.hard) == (status, value, 0.2, 0.3)


def test_each_stock_figure_is_checked_against_its_own_limit():
    figures = Figures(volatility=0.1, drawdown=0.5, var_95=0.01, cvar_95=0.09)
    overlay = Overlay(profile(volatility=(0.2, 0.3), drawdown=(0.2, 0.4), var_95=(0.02, 0.03), cvar_95=(0.03, 0.05)))
    got = {check: found.status for check, found in findings_for(figures, overlay).items()}
    assert got == {"volatility": "ok", "drawdown": "breach", "var_95": "ok", "cvar_95": "breach"}


def test_the_messages_say_what_was_measured_and_the_limit_in_plain_numbers():
    overlay = Overlay(profile(volatility=(0.2, 0.3), concentration=(0.2, 0.4)), (Holding("A", 900.0),), 100.0)
    found = findings_for(Figures(volatility=0.482), overlay)
    assert found["volatility"].message == "Volatility 48.2% is at or above your hard limit of 30.0%."
    assert "The largest holding would be A at 90.0%." in found["concentration"].message
    assert "Concentration (HHI) 0.820 is at or above your hard limit of 0.400." in found["concentration"].message
    ok = findings_for(Figures(volatility=0.1), Overlay(profile(volatility=(0.2, 0.3))))["volatility"]
    assert ok.message == "Volatility 10.0% is within your limits (warning at 20.0%)."
    warn = findings_for(Figures(volatility=0.25), Overlay(profile(volatility=(0.2, 0.3))))["volatility"]
    assert warn.message == "Volatility 25.0% is at or above your warning level of 20.0% (hard limit 30.0%)."


def test_a_figure_that_could_not_be_computed_is_unchecked_with_the_reason_never_ok():
    figures = Figures(reasons={"volatility": "volatility needs at least 20 observations, got 5"})
    found = findings_for(figures, Overlay(profile(volatility=(0.2, 0.3), drawdown=(0.2, 0.3))))
    assert found["volatility"].status == "unchecked" and found["volatility"].value is None
    assert found["volatility"].message == "Volatility not checked: volatility needs at least 20 observations, got 5."
    assert found["drawdown"].status == "unchecked"


def test_liquidity_compares_the_amount_with_the_typical_daily_traded_value():
    overlay = Overlay(profile(liquidity=(0.02, 0.05)), amount=20_000.0)
    figures = Figures(traded_value=1_000_000.0)
    assert findings_for(figures, overlay)["liquidity"].status == "warn"
    assert findings_for(figures, Overlay(overlay.profile, amount=50_000.0))["liquidity"].status == "breach"
    assert findings_for(figures, Overlay(overlay.profile, amount=19_999.0))["liquidity"].status == "ok"
    assert findings_for(figures, Overlay(overlay.profile, amount=20_000.0))["liquidity"].value == pytest.approx(0.02)


def test_the_checks_that_need_an_amount_say_so_when_there_is_none():
    overlay = Overlay(profile(liquidity=(0.02, 0.05), position=(0.1, 0.2), concentration=(0.2, 0.4)), (Holding("A", 100.0),))
    found = findings_for(Figures(traded_value=1e6), overlay)
    assert {f.status for f in found.values()} == {"unchecked"}
    assert all("no amount to invest was given" in f.message for f in found.values())
    zero = Overlay(overlay.profile, overlay.holdings, 0.0)
    assert {f.status for f in findings_for(Figures(traded_value=1e6), zero).values()} == {"unchecked"}


def test_liquidity_without_a_traded_value_is_unchecked_with_the_reason():
    found = findings_for(Figures(reasons={"liquidity": "there is no volume data"}), Overlay(profile(liquidity=(0.02, 0.05)), amount=1000.0))
    assert found["liquidity"].status == "unchecked" and "there is no volume data" in found["liquidity"].message


def test_position_is_the_weight_after_the_buy_and_adds_to_what_is_already_held():
    overlay = Overlay(profile(position=(0.10, 0.15)), (Holding("A", 900_000.0), Holding("SBIN", 50_000.0)), 50_000.0)
    found = findings_for(Figures(), overlay)["position"]
    assert found.value == pytest.approx(0.10) and found.status == "warn"
    bigger = Overlay(overlay.profile, overlay.holdings, 200_000.0)
    assert findings_for(Figures(), bigger)["position"].value == pytest.approx(250_000 / 1_150_000)


def test_a_stock_not_yet_owned_weighs_just_the_amount_against_the_portfolio():
    overlay = Overlay(profile(position=(0.10, 0.15), concentration=(0.5, 0.9)), (Holding("A", 600.0), Holding("B", 300.0)), 100.0)
    found = findings_for(Figures(), overlay)
    assert found["position"].value == pytest.approx(0.1)
    assert found["concentration"].value == pytest.approx(0.6**2 + 0.3**2 + 0.1**2)


def test_concentration_is_the_sum_of_squared_weights_after_the_buy():
    overlay = Overlay(profile(concentration=(0.15, 0.25)), (Holding("A", 500.0), Holding("B", 500.0)), 1000.0)
    found = findings_for(Figures(), overlay)["concentration"]
    assert found.value == pytest.approx(0.25**2 + 0.25**2 + 0.5**2) and found.status == "breach"


def test_without_holdings_position_and_concentration_are_unchecked_not_one_hundred_percent():
    found = findings_for(Figures(), Overlay(profile(position=(0.1, 0.2), concentration=(0.2, 0.4)), (), 1000.0))
    assert {f.status for f in found.values()} == {"unchecked"}
    assert all("no holdings were given" in f.message for f in found.values())


def test_only_the_checks_the_profile_switches_on_are_run_and_in_the_report_order():
    assert [f.check for f in run_checks(Figures(volatility=0.1), Overlay(profile(volatility=(0.2, 0.3))), "X")] == ["volatility"]
    every = Overlay(PRESETS["moderate"], (Holding("A", 1.0),), 1.0)
    assert [f.check for f in run_checks(Figures(), every, "X")] == list(MEASURES)
    assert run_checks(Figures(), Overlay(RiskProfile("empty")), "X") == ()


def finding(status, message="m", check="volatility"):
    return Finding(check, status, 0.5, 0.2, 0.3, message)


VERDICT = {"verdict": "Buy", "conviction": 70, "key_risks": ["a data gap"], "resolution_path": "blend"}


@pytest.mark.parametrize("verdict", ["Buy", "Overweight"])
def test_a_breach_holds_back_a_buy_or_an_overweight_and_keeps_the_specialists_call(verdict):
    out = apply_overlay({**VERDICT, "verdict": verdict}, [finding("breach", "too wild")])
    assert out["verdict"] == "Hold" and out["pre_overlay_verdict"] == verdict
    assert out["key_risks"] == [f"{HELD_BACK}: the specialists said {verdict}.", "too wild", "a data gap"]
    assert out["conviction"] == 70 and out["resolution_path"] == "blend"


@pytest.mark.parametrize("verdict", ["Hold", "Underweight", "Sell"])
def test_a_breach_never_changes_a_hold_or_a_sale_but_is_still_listed(verdict):
    out = apply_overlay({**VERDICT, "verdict": verdict}, [finding("breach", "too wild")])
    assert out["verdict"] == verdict and "pre_overlay_verdict" not in out
    assert out["key_risks"] == ["too wild", "a data gap"]


@pytest.mark.parametrize("status", ["ok", "warn", "unchecked"])
def test_only_a_breach_changes_a_verdict(status):
    out = apply_overlay(VERDICT, [finding(status)])
    assert out["verdict"] == "Buy" and "pre_overlay_verdict" not in out


def test_warnings_are_listed_after_breaches_and_before_the_specialists_own_risks_and_ok_checks_are_not():
    findings = [finding("warn", "w1"), finding("ok", "fine"), finding("breach", "b1"), finding("unchecked", "n/a"), finding("warn", "w2")]
    out = apply_overlay({**VERDICT, "verdict": "Hold"}, findings)
    assert out["key_risks"] == ["b1", "w1", "w2", "a data gap"]


def test_every_finding_travels_with_the_verdict_as_plain_data_and_the_input_is_not_changed():
    original = {**VERDICT, "key_risks": ["a data gap"]}
    findings = [finding("ok", "fine"), finding("breach", "b")]
    out = apply_overlay(original, findings)
    assert out["risk_findings"] == [f.to_dict() for f in findings]
    assert out["risk_findings"][1] == {"check": "volatility", "status": "breach", "value": 0.5, "warn": 0.2, "hard": 0.3, "message": "b"}
    assert original == {**VERDICT, "key_risks": ["a data gap"]} and "risk_findings" not in original


def test_a_verdict_after_the_overlay_still_passes_the_verdict_contract():
    assert validate_judge_verdict(apply_overlay(VERDICT, [finding("breach")])) == []
    assert validate_judge_verdict(apply_overlay(VERDICT, [])) == []
    bad = {**VERDICT, "pre_overlay_verdict": "Maybe", "risk_findings": ["not an object"]}
    errors = validate_judge_verdict(bad)
    assert any("pre_overlay_verdict" in e for e in errors) and any("risk_findings" in e for e in errors)
    for verdict in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
        for status in ("ok", "warn", "breach", "unchecked"):
            assert validate_judge_verdict(apply_overlay({**VERDICT, "verdict": verdict}, [finding(status)])) == []
