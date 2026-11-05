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


def _row(symbol: str, elevated: set[str], computed: set[str], max_fall: float, size: int = 0, vol: int = 0) -> bt.Row:
    o = bt.Outcome(symbol, 100.0, max_fall, 0.0, 0.0, 0.0, False, False, False, 0.02, 1e7, END)
    return bt.Row(symbol, len(elevated), len(computed), frozenset(elevated), frozenset(computed), size, vol, o)


def test_composite_verdict_follows_the_rule_fixed_in_the_spec() -> None:
    def res(ratio: float, lo: float) -> dict:
        return {"ratio": ratio, "ci95": [lo, ratio + 1]}

    assert bt.composite_verdict(res(1.5, 1.1), res(1.4, 1.05)) == "supported at this date"
    assert bt.composite_verdict(res(1.5, 1.1), res(1.3, 0.9)) == "leaning"  # at least 1.2 on both, lower end above 1 on one
    assert bt.composite_verdict(res(1.5, 1.1), res(1.1, 0.95)) == "not replicated"  # volatility ratio under 1.2 and its interval reaches 1
    assert bt.composite_verdict(res(1.1, 1.01), res(1.1, 1.02)) == "supported at this date"  # the rule is about the interval, as written
    assert bt.composite_verdict(res(1.3, 0.9), res(1.3, 0.8)) == "not replicated"
    assert bt.composite_verdict(res(0.9, 0.5), res(1.6, 1.2)) == "not replicated"
    assert bt.composite_verdict(None, res(1.6, 1.2)) == "not computable"
    assert bt.composite_verdict({"ratio": None, "ci95": [None, None]}, res(1.6, 1.2)) == "not computable"


def test_composite_is_either_cash_flow_measure_and_needs_one_of_them_computed() -> None:
    a, b = "cfo_to_profit", "accruals_ratio"
    rows = [_row(f"H{i}", {a}, {a, b, "x"}, 0.6 if i < 5 else 0.0) for i in range(10)]
    rows += [_row(f"B{i}", {b}, {b}, 0.6) for i in range(10)]  # accruals alone elevated counts
    rows += [_row(f"L{i}", set(), {a, b}, 0.6 if i < 2 else 0.0) for i in range(30)]
    rows += [_row(f"N{i}", {"x"}, {"x"}, 0.6) for i in range(20)]  # neither cash-flow measure computed: out
    out = bt.composite_test(rows)
    assert out["elevated_n"] == 20 and out["comparison_n"] == 30 and out["neither_computed"] == 20
    fifty = out["fell_50"]["size"]
    assert fifty["high_n"] == 20 and fifty["high_bad"] == 15
    assert fifty["ratio"] > 1.2
    assert out["both_elevated_n"] == 0
    assert out["verdict"] in {"supported at this date", "leaning", "not replicated", "not computable"}


def test_strata_where_the_low_group_is_too_thin_are_left_out() -> None:
    low = [(0, False)] * 20 + [(1, True)] * 3
    high = [(0, False)] * 5 + [(1, True)] * 5
    r = bt.standardised(high, low, draws=100)
    assert r is not None and r["high_n"] == 5
    assert bt.standardised([(1, True)], [(1, True)] * 3, draws=100) is None
