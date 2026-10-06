from dataclasses import replace

import pytest
from bar_factory import make_bars

from athena.backtest.adjust import Adjustment, adjust_for_splits, adjustment_note
from athena.contracts import InsufficientData
from athena.trading_calendar import ist_date


def raw(closes, opens=None, volumes=None):
    """Bars with the given closes; an open differs from its close only where `opens` says so."""
    bars = make_bars(closes)
    return [
        replace(
            bar,
            open=bar.close if opens is None or opens[i] is None else opens[i],
            volume=1000.0 + i if volumes is None else volumes[i],
        )
        for i, bar in enumerate(bars)
    ]


def test_a_one_for_one_bonus_is_found_with_factor_one_half_on_the_day_after_the_break():
    bars = raw([100.0, 101.0, 102.0, 51.5, 52.0, 52.5])  # the true move from 102 to 103 is +0.98%
    adjusted, events = adjust_for_splits(bars)
    assert events == (Adjustment(ist_date(bars[3].timestamp), 0.5),)
    assert adjusted[3].close / adjusted[2].close == pytest.approx(103.0 / 102.0)


def test_the_factor_snaps_to_the_common_ratio_so_the_move_across_the_break_survives():
    bars = raw([100.0, 100.0, 52.0, 52.0], opens=[None, None, 52.0, None])  # a 1:1 bonus and a real +4% open
    adjusted, events = adjust_for_splits(bars)
    assert [event.factor for event in events] == [0.5]
    assert adjusted[2].open / adjusted[1].close == pytest.approx(1.04)


def test_a_ratio_far_from_every_common_one_is_used_as_observed():
    _, events = adjust_for_splits(raw([100.0, 42.0, 42.0]))  # 16% from 1/2 and 26% from 1/3
    assert [event.factor for event in events] == [pytest.approx(0.42)]


def test_a_ten_for_one_split_snaps_to_one_tenth():
    _, events = adjust_for_splits(raw([1292.5, 129.2, 130.0]))
    assert [event.factor for event in events] == [0.1]


def test_a_reverse_split_gives_a_factor_above_one_and_divides_the_earlier_volume_by_it():
    bars = raw([10.0, 10.2, 51.0, 51.5], volumes=[500.0, 500.0, 100.0, 100.0])
    adjusted, events = adjust_for_splits(bars)
    assert [event.factor for event in events] == [5.0]
    assert [bar.close for bar in adjusted[:2]] == pytest.approx([50.0, 51.0])
    assert [bar.volume for bar in adjusted] == pytest.approx([100.0, 100.0, 100.0, 100.0])


def test_volume_moves_opposite_to_price_so_the_traded_value_is_unchanged():
    bars = raw([100.0, 100.0, 50.0, 50.0], volumes=[300.0, 300.0, 600.0, 600.0])
    adjusted, _ = adjust_for_splits(bars)
    assert [bar.volume for bar in adjusted] == pytest.approx([600.0, 600.0, 600.0, 600.0])
    assert [a.close * a.volume for a in adjusted[:2]] == pytest.approx([b.close * b.volume for b in bars[:2]])


def test_only_the_bars_before_the_break_change_and_every_price_field_is_scaled():
    bars = raw([100.0, 101.0, 50.0, 51.0])
    adjusted, _ = adjust_for_splits(bars)
    for before, after in zip(bars[:2], adjusted[:2]):
        assert (after.open, after.high, after.low, after.close) == pytest.approx(
            (before.open / 2, before.high / 2, before.low / 2, before.close / 2)
        )
        assert (after.symbol, after.timestamp, after.source) == (before.symbol, before.timestamp, before.source)
    assert adjusted[2:] == bars[2:]


def test_ordinary_gaps_are_not_events():
    for closes in ([100.0, 85.0, 86.0], [100.0, 125.0, 124.0], [100.0, 61.0, 60.0], [100.0, 169.0, 170.0]):
        bars = raw(closes)
        assert adjust_for_splits(bars) == (bars, ())


def test_two_events_compose_on_the_bars_before_both():
    bars = raw([400.0, 400.0, 200.0, 200.0, 20.0, 20.0])  # a 1:1 bonus, then a 10:1 split
    adjusted, events = adjust_for_splits(bars)
    assert [event.factor for event in events] == [0.5, 0.1]
    assert [bar.close for bar in adjusted] == pytest.approx([20.0] * 6)
    assert [event.day for event in events] == [ist_date(bars[2].timestamp), ist_date(bars[4].timestamp)]


def test_bars_without_events_come_back_sorted_and_unchanged():
    bars = raw([100.0, 101.0, 99.0, 100.5])
    assert adjust_for_splits(list(reversed(bars))) == (bars, ())
    assert adjust_for_splits([]) == ([], ())


@pytest.mark.parametrize("field", ["open", "close"])
@pytest.mark.parametrize("value", [0.0, -1.0])
def test_a_non_positive_price_is_refused_not_divided_by(field, value):
    bars = raw([100.0, 101.0, 102.0])
    bars[1] = replace(bars[1], **{field: value})
    with pytest.raises(InsufficientData, match="non-positive prices"):
        adjust_for_splits(bars)


def test_the_note_names_each_event_and_says_where_it_came_from():
    events = (Adjustment(ist_date(raw([1.0])[0].timestamp), 0.5), Adjustment(ist_date(raw([1.0, 1.0])[0].timestamp), 0.1))
    note = adjustment_note(events)
    assert note.startswith("Prices adjusted for 2 split or bonus event(s) found from an overnight price break, not from corporate-action data: ")
    assert f"{events[0].day} (x0.5), {events[1].day} (x0.1)" in note
    assert adjustment_note(()) is None
