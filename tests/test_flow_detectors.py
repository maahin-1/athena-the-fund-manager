from datetime import date, timedelta

import pytest

from athena.flow import detectors as d

DAYS = [date(2026, 1, 1) + timedelta(days=i) for i in range(70)]


def quiet(n=70):
    """Open interest and price wobbling a little around fixed levels, so a move of one percent is large and one of a tenth of a percent is not."""
    rows = []
    for i in range(n):
        wobble = ((i * 7) % 5 - 2) / 1000
        rows.append(d.FutDay(DAYS[i], 100.0 * (1 + wobble), 1000.0 * (1 + wobble * 2), 10.0))
    return rows


def with_last(price_change, oi_change):
    rows = quiet()
    prev = rows[-2]
    rows[-1] = d.FutDay(DAYS[69], prev.close * (1 + price_change), prev.oi * (1 + oi_change), 50.0)
    return rows


@pytest.mark.parametrize(
    "price,oi,kind",
    [(0.03, 0.05, d.BUILD_UP), (-0.03, 0.05, d.SHORT_BUILD_UP), (0.03, -0.05, d.SHORT_COVERING), (-0.03, -0.05, d.LONG_UNWINDING)],
)
def test_price_and_open_interest_together_give_one_of_four_readings_when_both_moves_are_large_for_the_stock(price, oi, kind):
    events = d.futures_events("ABC", with_last(price, oi))
    assert [e.kind for e in events if e.kind != "oi_spike"] == [kind] and events[0].day == DAYS[69]
    assert events[0].facts["price_z"] * price > 0 and events[0].facts["oi_z"] * oi > 0


def test_a_small_move_or_a_large_move_in_only_one_of_the_two_is_not_a_reading():
    assert d.futures_events("ABC", with_last(0.001, 0.001)) == []
    assert d.futures_events("ABC", with_last(0.03, 0.0005)) == []  # price moved, open interest did not
    only_oi = d.futures_events("ABC", with_last(0.0005, 0.05))  # open interest moved, price did not: no reading, though a move this large for a quiet stock is a spike
    assert [e.kind for e in only_oi] == ["oi_spike"]


def test_a_huge_jump_in_open_interest_is_a_spike_even_when_the_price_did_not_move():
    events = d.futures_events("ABC", with_last(0.0, 0.5))
    assert [e.kind for e in events] == ["oi_spike"] and events[0].facts["oi_z"] > d.SPIKE_Z


def test_too_little_history_or_a_flat_series_finds_nothing():
    assert d.futures_events("ABC", quiet()[:20]) == []
    flat = [d.FutDay(DAYS[i], 100.0, 1000.0) for i in range(70)]
    assert d.futures_events("ABC", flat) == []


EXP = date(2026, 3, 26)


def chain(day, calls, puts):
    """Option rows for one day: {strike: oi} for calls and for puts, one expiry."""
    return [d.OptRow(day, EXP, k, "CE", oi) for k, oi in calls.items()] + [d.OptRow(day, EXP, k, "PE", oi) for k, oi in puts.items()]


def test_put_call_ratio_is_put_open_interest_over_call_open_interest_for_the_nearest_expiry():
    rows = chain(DAYS[0], {100: 300, 110: 200}, {100: 250, 90: 250}) + [d.OptRow(DAYS[0], date(2026, 4, 30), 100, "PE", 9999)]
    assert d.put_call_ratio(rows, DAYS[0]) == pytest.approx(1.0)
    assert d.put_call_ratio(rows, DAYS[0], date(2026, 4, 30)) is None  # no calls for that expiry
    assert d.put_call_ratio(rows, DAYS[5]) is None


def test_max_pain_is_the_strike_where_the_writers_pay_least():
    rows = chain(DAYS[0], {90: 100, 100: 100, 110: 100}, {90: 100, 100: 100, 110: 100})
    assert d.max_pain(rows, DAYS[0], EXP) == 100
    heavy_calls_low = chain(DAYS[0], {90: 1000, 100: 100, 110: 100}, {90: 100, 100: 100, 110: 100})
    assert d.max_pain(heavy_calls_low, DAYS[0], EXP) == 90
    assert d.max_pain(rows, DAYS[0], date(2026, 5, 1)) is None


def test_a_put_call_ratio_far_from_its_usual_range_is_an_event_and_a_usual_one_is_not():
    days = DAYS[:40]
    rows = []
    for i, day in enumerate(days[:-1]):
        wobble = (i % 3 - 1) * 10
        rows += chain(day, {100: 1000}, {100: 1000 + wobble})
    rows += chain(days[-1], {100: 1000}, {100: 1800})
    events = d.options_events("ABC", rows, days, 100.0)
    assert [e.kind for e in events if e.kind.startswith("put_call")] == ["put_call_up"] and events[0].facts["put_call_ratio"] == pytest.approx(1.8)
    calm = rows[:-2] + chain(days[-1], {100: 1000}, {100: 1005})
    assert not [e for e in d.options_events("ABC", calm, days, 100.0) if e.kind.startswith("put_call")]


def test_the_max_pain_strike_moving_a_few_percent_of_the_price_is_an_event():
    days = DAYS[:40]
    rows = []
    for i, day in enumerate(days):
        if i < 35:
            rows += chain(day, {90: 100, 100: 100, 110: 100}, {90: 100, 100: 100, 110: 100})
        else:
            rows += chain(day, {90: 100, 100: 100, 110: 100}, {90: 100, 100: 100, 110: 800})  # puts piled on the high strike: max pain moves up
    events = [e for e in d.options_events("ABC", rows, days, 100.0) if e.kind.startswith("max_pain")]
    assert len(events) == 1 and events[0].kind == "max_pain_up" and events[0].facts["max_pain"] > events[0].facts["max_pain_before"]


def test_each_event_is_described_in_one_line_with_its_figures():
    line = d.describe(d.futures_events("ABC", with_last(0.03, 0.05))[0])
    assert "long build up" in line and "price +3.0%" in line and "open interest +5.0%" in line
    assert "jumped" in d.describe(d.futures_events("ABC", with_last(0.0, 0.5))[0])
