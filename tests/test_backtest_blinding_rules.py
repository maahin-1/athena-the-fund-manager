from datetime import date, datetime, timedelta, timezone

import pytest
from bar_factory import make_bars

from athena.backtest.blinding import BLIND_BASE, BLIND_SYMBOL, blind_bars
from athena.backtest.rules import MIN_REWARD_RISK, PERSONA, RULES, TREND
from athena.contracts import Bar
from athena.trading_calendar import ist_date

CLOSES = [250.0 + i * 0.3 + (4 if i % 9 == 0 else 0) for i in range(120)]
REAL = make_bars(CLOSES, symbol="SBIN")


def test_blinding_removes_the_ticker_rescales_prices_and_marks_the_source():
    blind = blind_bars(REAL)
    assert {bar.symbol for bar in blind} == {BLIND_SYMBOL} and {bar.source for bar in blind} == {"blinded"}
    assert blind[0].close == pytest.approx(BLIND_BASE) and len(blind) == len(REAL)


def test_blinding_keeps_returns_and_volume_exactly_so_a_return_based_rule_cannot_tell():
    blind = blind_bars(REAL)
    real_returns = [b.close / a.close for a, b in zip(REAL, REAL[1:])]
    blind_returns = [b.close / a.close for a, b in zip(blind, blind[1:])]
    assert blind_returns == pytest.approx(real_returns, rel=1e-12)
    assert [bar.volume for bar in blind] == [bar.volume for bar in REAL]


def test_blinding_moves_every_date_28_years_back_keeping_weekday_month_and_day():
    for real, blind in zip(REAL, blind_bars(REAL)):
        a, b = ist_date(real.timestamp), ist_date(blind.timestamp)
        assert b.year == a.year - 28 and (b.month, b.day, b.weekday()) == (a.month, a.day, a.weekday())


def test_blinding_survives_a_leap_day_and_sorts_its_output():
    def bar(day, close):
        stamp = datetime(day.year, day.month, day.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1)
        return Bar("X", stamp, close, close, close, close, 1.0, stamp, "t")

    blind = blind_bars([bar(date(2024, 3, 1), 12.0), bar(date(2024, 2, 29), 10.0)])  # given out of order
    assert [ist_date(b.timestamp) for b in blind] == [date(1996, 2, 29), date(1996, 3, 1)]
    assert [b.close for b in blind] == pytest.approx([100.0, 120.0]) and blind_bars([]) == []


def packet(**values):
    return {"metrics": {name: {"value": value} for name, value in values.items()}}


FULL_SETUP = dict(trend_alignment="aligned_up", volume_ratio_20_50=1.2, reward_risk=2.5)


def test_the_trend_rule_enters_only_when_all_three_trends_are_up_and_leaves_when_alignment_is_lost():
    assert TREND.enter(packet(trend_alignment="aligned_up"))
    for other in ("not_aligned", "aligned_down"):
        assert not TREND.enter(packet(trend_alignment=other)) and TREND.leave(packet(trend_alignment=other))
    assert not TREND.leave(packet(trend_alignment="aligned_up"))


def test_a_missing_figure_never_triggers_an_entry_or_an_exit():
    assert not TREND.enter(packet()) and not TREND.leave(packet())
    assert not PERSONA.enter(packet(trend_alignment="aligned_up")) and not PERSONA.leave(packet())


def test_the_persona_rule_needs_alignment_volume_above_one_and_reward_risk_of_two():
    assert PERSONA.enter(packet(**FULL_SETUP))
    assert not PERSONA.enter(packet(**{**FULL_SETUP, "volume_ratio_20_50": 1.0}))  # must be above 1
    assert not PERSONA.enter(packet(**{**FULL_SETUP, "reward_risk": MIN_REWARD_RISK - 0.01}))
    assert PERSONA.enter(packet(**{**FULL_SETUP, "reward_risk": MIN_REWARD_RISK}))
    assert not PERSONA.enter(packet(**{**FULL_SETUP, "trend_alignment": "not_aligned"}))


def test_the_persona_rule_leaves_exactly_when_the_trend_rule_does():
    for alignment in ("aligned_up", "not_aligned", "aligned_down"):
        assert PERSONA.leave(packet(trend_alignment=alignment)) == TREND.leave(packet(trend_alignment=alignment))


def test_the_registry_names_both_rules_and_each_has_a_description():
    assert set(RULES) == {"trend", "persona"} and all(rule.description for rule in RULES.values())
