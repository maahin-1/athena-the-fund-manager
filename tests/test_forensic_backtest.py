from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from athena.contracts import Bar
from athena.forensic.statements import backtest as bt
from athena.forensic.statements.outcomes import MIN_LIQUID_BARS, fifths, outcome, thirds, volatility

IST = timezone(timedelta(hours=5, minutes=30))
ENTRY = date(2025, 7, 1)
END = date(2026, 11, 1)


def bars(path: list[tuple[date, float]]) -> list[Bar]:
    stamps = [datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST) for d, _ in path]
    return [Bar("X", t, c, c, c, c, 1000.0, t, "test") for t, (_d, c) in zip(stamps, path)]


def flat_bench(start: float = 100.0) -> dict[date, float]:
    return {ENTRY + timedelta(days=i): start for i in range(-10, 500)}


def test_fall_is_the_deepest_close_below_the_entry_close() -> None:
    path = [(date(2025, 6, 30), 100.0), (date(2025, 8, 1), 80.0), (date(2025, 12, 1), 40.0), (date(2026, 10, 30), 90.0)]
    o = outcome("X", bars(path), ENTRY, END, flat_bench(), liquid_bars=60)
    assert o is not None
    assert round(o.max_fall, 2) == 0.60
    assert round(o.final_return, 2) == -0.10
    assert not o.ceased


def test_a_stock_that_only_rises_has_no_fall() -> None:
    path = [(date(2025, 6, 30), 100.0), (date(2026, 1, 5), 150.0), (date(2026, 10, 30), 200.0)]
    o = outcome("X", bars(path), ENTRY, END, flat_bench(), liquid_bars=60)
    assert o is not None and o.max_fall == 0.0 and round(o.final_return, 2) == 1.0


def test_stale_entry_or_thin_trading_is_left_out() -> None:
    assert outcome("X", bars([(date(2025, 6, 1), 100.0)]), ENTRY, END, flat_bench(), liquid_bars=60) is None
    assert outcome("X", bars([(date(2025, 6, 30), 100.0)]), ENTRY, END, flat_bench(), liquid_bars=MIN_LIQUID_BARS - 1) is None


def test_trading_that_stops_is_marked_and_a_stock_with_no_bar_after_entry_has_no_path() -> None:
    stopped = outcome("X", bars([(date(2025, 6, 30), 100.0), (date(2026, 2, 1), 60.0)]), ENTRY, END, flat_bench(), liquid_bars=60)
    assert stopped is not None and stopped.ceased and round(stopped.max_fall, 2) == 0.40
    gone = outcome("X", bars([(date(2025, 6, 30), 100.0)]), ENTRY, END, flat_bench(), liquid_bars=60)
    assert gone is not None and gone.ceased and gone.max_fall is None


def test_excess_and_relative_fall_use_the_benchmark_over_the_same_dates() -> None:
    bench = {ENTRY + timedelta(days=i): 100.0 for i in range(-10, 200)}
    bench.update({ENTRY + timedelta(days=i): 120.0 for i in range(200, 500)})  # the market rises 20% later
    path = [(date(2025, 6, 30), 100.0), (date(2026, 10, 30), 100.0)]
    o = outcome("X", bars(path), ENTRY, END, bench, liquid_bars=60)
    assert o is not None and round(o.excess_return, 2) == -0.20
    assert round(o.max_relative_fall, 3) == round(1 - 1 / 1.2, 3)


def test_trade_for_trade_needs_eq_at_entry_and_be_or_bz_after() -> None:
    base = bars([(date(2025, 6, 30), 100.0), (date(2026, 10, 30), 100.0)])
    assert outcome("X", base, ENTRY, END, flat_bench(), series_at_entry="EQ", series_in_window=["EQ", "BE"], liquid_bars=60).trade_for_trade
    assert not outcome("X", base, ENTRY, END, flat_bench(), series_at_entry="BE", series_in_window=["BE"], liquid_bars=60).trade_for_trade
    assert not outcome("X", base, ENTRY, END, flat_bench(), series_at_entry="EQ", series_in_window=["EQ"], liquid_bars=60).trade_for_trade


def test_unexplained_break_in_the_window_is_marked_but_one_before_it_is_not() -> None:
    base = bars([(date(2025, 6, 30), 100.0), (date(2026, 10, 30), 100.0)])
    assert outcome("X", base, ENTRY, END, flat_bench(), unexplained_days=[date(2026, 1, 1)], liquid_bars=60).unexplained_break
    assert not outcome("X", base, ENTRY, END, flat_bench(), unexplained_days=[date(2024, 1, 1)], liquid_bars=60).unexplained_break


def test_volatility_needs_a_year_and_cuts_split_evenly() -> None:
    assert volatility([100.0 + i for i in range(50)]) is None
    assert volatility([100.0] * 200) == 0.0
    ranks = fifths({f"S{i}": float(i) for i in range(100)})
    assert sorted(set(ranks.values())) == [0, 1, 2, 3, 4] and ranks["S0"] == 0 and ranks["S99"] == 4
    assert sorted(set(thirds({f"S{i}": float(i) for i in range(9)}).values())) == [0, 1, 2]


def test_wilson_interval_contains_the_rate_and_is_wide_for_few() -> None:
    lo, hi = bt.wilson(5, 20)
    assert lo < 0.25 < hi and hi - lo > 0.2
    assert bt.wilson(0, 0) == (0.0, 1.0)


def test_standardised_ratio_is_one_when_both_groups_fall_alike_and_above_one_when_high_does_worse() -> None:
    low = [(s, i < 2) for s in (0, 1) for i in range(20)]  # 10% bad in each stratum
    alike = [(0, True), (0, False), (0, False), (0, False), (0, False), (0, False), (0, False), (0, False), (0, False), (0, False)]  # 10% bad
    r = bt.standardised(alike, low, draws=300)
    assert r is not None and r["ratio"] == 1.0
    worse = [(0, True)] * 5 + [(0, False)] * 5
    r = bt.standardised(worse, low, draws=300)
    assert r is not None and r["ratio"] == 5.0 and r["ci95"][0] > 1.0


def test_standardising_removes_a_size_mix_effect() -> None:
    # stratum 1 is risky for everybody (40% bad); the high group sits mostly there but falls at the same rate within each stratum
    low = [(0, i < 2) for i in range(20)] + [(1, i < 8) for i in range(20)]
    high = [(0, i < 1) for i in range(10)] + [(1, i < 16) for i in range(40)]
    r = bt.standardised(high, low, draws=300)
    assert r is not None and 0.9 <= r["ratio"] <= 1.1
    assert sum(b for _, b in high) / len(high) > sum(b for _, b in low) / len(low)  # the raw rates would have said "worse"


def test_strata_where_the_low_group_is_too_thin_are_left_out() -> None:
    low = [(0, False)] * 20 + [(1, True)] * 3
    high = [(0, False)] * 5 + [(1, True)] * 5
    r = bt.standardised(high, low, draws=100)
    assert r is not None and r["high_n"] == 5
    assert bt.standardised([(1, True)], [(1, True)] * 3, draws=100) is None
