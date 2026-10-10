from datetime import date

import pytest
from bar_factory import make_bars

from athena.price_integrity.events import PriceEvent
from athena.price_integrity.reconcile import describe, reconcile
from athena.trading_calendar import ist_date

FLAT = [100.0] * 30


def split_series(ratio, before=30, after=30):
    """`before` flat closes at 100, then `after` flat closes at 100/ratio: a split with no market move."""
    return make_bars([100.0] * before + [100.0 / ratio] * after)


def day_of(bars, index):
    return ist_date(bars[index].timestamp)


def test_a_recorded_split_that_matches_the_price_gap_is_adjusted_by_the_recorded_ratio():
    bars = split_series(5.0)
    result = reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)])
    assert [b.close for b in result.bars] == pytest.approx([20.0] * 60)
    assert result.bars[0].volume == pytest.approx(5000.0) and result.bars[-1].volume == 1000.0  # volume times the ratio, before only
    assert len(result.applied) == 1 and result.applied[0].observed == pytest.approx(0.2) and not result.unexplained and not result.unseen


def test_the_event_date_may_be_one_day_off_from_the_price_gap():
    bars = split_series(2.0)
    assert reconcile(bars, [PriceEvent(day_of(bars, 31), 2.0)]).applied
    assert reconcile(bars, [PriceEvent(day_of(bars, 29), 2.0)]).applied
    assert not reconcile(bars, [PriceEvent(day_of(bars, 35), 2.0)]).applied


def test_a_market_move_on_top_of_the_split_still_matches_but_a_wrong_size_does_not():
    bars = make_bars([100.0] * 30 + [22.0] * 30)  # 5:1 split plus a 10% rise on the day
    assert reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)]).applied
    bars = make_bars([100.0] * 30 + [50.0] * 30)  # a halving, but the record says 5:1
    result = reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)])
    assert not result.applied and result.unseen and [b.close for b in result.bars] == [100.0] * 30 + [50.0] * 30


def test_a_halving_with_no_record_is_left_alone_and_reported_as_unexplained():
    bars = split_series(2.0)  # looks exactly like a 1:1 bonus; only a record could tell it from a crash
    result = reconcile(bars, [])
    assert [b.close for b in result.bars] == [100.0] * 30 + [50.0] * 30
    assert [(m.day, round(m.ratio, 2)) for m in result.unexplained] == [(day_of(bars, 30), 0.5)]


def test_a_record_with_no_gap_in_the_prices_is_unseen_not_applied():
    bars = make_bars(FLAT + FLAT)
    result = reconcile(bars, [PriceEvent(day_of(bars, 30), 5.0)])
    assert result.unseen and not result.applied and [b.close for b in result.bars] == [100.0] * 60


def test_two_events_compose_and_a_reverse_split_scales_up():
    bars = make_bars([100.0] * 20 + [50.0] * 20 + [25.0] * 20)
    result = reconcile(bars, [PriceEvent(day_of(bars, 20), 2.0), PriceEvent(day_of(bars, 40), 2.0)])
    assert [b.close for b in result.bars] == pytest.approx([25.0] * 60)
    bars = make_bars([10.0] * 20 + [100.0] * 20)
    assert [b.close for b in reconcile(bars, [PriceEvent(day_of(bars, 20), 0.1)]).bars] == pytest.approx([100.0] * 40)


def test_input_is_not_changed_and_unsorted_bars_come_back_in_date_order():
    bars = split_series(5.0)
    before = [b.close for b in bars]
    result = reconcile(list(reversed(bars)), [PriceEvent(day_of(bars, 30), 5.0)])
    assert [b.close for b in bars] == before
    assert [b.timestamp for b in result.bars] == sorted(b.timestamp for b in bars)


def test_the_notes_say_what_was_adjusted_what_is_unexplained_and_what_was_unseen():
    bars = make_bars([100.0] * 20 + [50.0] * 20 + [10.0] * 20)
    result = reconcile(bars, [PriceEvent(day_of(bars, 20), 2.0), PriceEvent(date(2020, 1, 1), 3.0)])
    notes = describe(result)
    assert len(notes) == 3
    assert "adjusted for 1 recorded" in notes[0] and "Unexplained" in notes[1] and "no matching price break" in notes[2]
    assert describe(reconcile(make_bars(FLAT), [])) == ()
