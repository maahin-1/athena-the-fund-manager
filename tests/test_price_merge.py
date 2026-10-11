from datetime import date

from athena.price_integrity.actions import CorporateAction
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.merge import merge_events

DAY = date(2026, 1, 14)


def nse(kind, ratio, day=DAY, text="row"):
    return CorporateAction(day, kind, ratio, text)


def test_a_yahoo_event_that_nse_confirms_with_the_same_ratio_is_kept_once():
    merged = merge_events([PriceEvent(DAY, 5.0)], [nse("split", 5.0)])
    assert merged.events == (PriceEvent(DAY, 5.0),) and not merged.conflicts and not merged.notes


def test_the_dates_may_differ_by_a_weekend():
    merged = merge_events([PriceEvent(date(2026, 1, 16), 2.0)], [nse("bonus", 2.0, day=date(2026, 1, 19))])
    assert len(merged.events) == 1 and not merged.conflicts


def test_a_disagreeing_ratio_is_a_conflict_and_neither_source_is_applied():
    merged = merge_events([PriceEvent(DAY, 5.0)], [nse("split", 10.0, text="Face Value Split From Rs 10 To Re 1")])
    assert merged.events == () and len(merged.conflicts) == 1
    assert "disagree" in merged.conflicts[0] and "x10" in merged.conflicts[0] and "x5" in merged.conflicts[0]


def test_an_nse_split_or_bonus_yahoo_does_not_have_is_applied_with_nses_parsed_ratio():
    merged = merge_events([], [nse("split", 10.0), nse("bonus", 2.0, day=date(2025, 5, 5))])
    assert [(e.day, e.ratio, e.source, e.kind) for e in merged.events] == [(date(2025, 5, 5), 2.0, "nse", "bonus"), (DAY, 10.0, "nse", "split")]


def test_an_nse_event_with_no_readable_ratio_and_no_yahoo_match_is_noted_not_applied():
    merged = merge_events([], [nse("bonus", None, text="Bonus")])
    assert merged.events == () and len(merged.notes) == 1 and "no readable ratio" in merged.notes[0]


def test_a_yahoo_event_nse_does_not_list_stands_and_other_nse_kinds_are_left_to_reconcile():
    merged = merge_events([PriceEvent(DAY, 5.0)], [nse("demerger", None), nse("rights", 1 / 15), nse("buyback", None)])
    assert merged.events == (PriceEvent(DAY, 5.0),) and not merged.conflicts and not merged.notes


def test_an_nse_row_that_cannot_confirm_because_its_text_has_no_ratio_leaves_yahoos_event_alone():
    merged = merge_events([PriceEvent(DAY, 5.0)], [nse("split", None, text="Face Value Split")])
    assert merged.events == (PriceEvent(DAY, 5.0),) and not merged.conflicts


def test_a_split_and_a_bonus_on_the_same_day_are_one_event_with_the_combined_ratio():
    merged = merge_events([], [nse("split", 5.0, text="10 to 2"), nse("bonus", 2.0, text="Bonus 1:1")])
    assert [(e.day, e.ratio, e.kind) for e in merged.events] == [(DAY, 10.0, "bonus+split")] and not merged.conflicts


def test_yahoos_combined_event_agrees_with_the_two_nse_rows_instead_of_conflicting_with_each():
    merged = merge_events([PriceEvent(DAY, 10.0)], [nse("split", 5.0), nse("bonus", 2.0)])
    assert merged.events == (PriceEvent(DAY, 10.0),) and not merged.conflicts and not merged.notes


def test_a_same_day_pair_with_one_unreadable_ratio_is_not_applied_and_is_noted():
    merged = merge_events([], [nse("split", 5.0), nse("bonus", None, text="Bonus")])
    assert merged.events == () and len(merged.notes) == 1


def test_a_split_and_a_bonus_on_different_days_stay_two_events():
    merged = merge_events([], [nse("split", 5.0), nse("bonus", 2.0, day=date(2026, 3, 2))])
    assert [(e.ratio, e.kind) for e in merged.events] == [(5.0, "split"), (2.0, "bonus")]
