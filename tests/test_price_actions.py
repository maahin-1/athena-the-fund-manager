from datetime import date

import pytest

from athena.price_integrity.actions import CorporateAction, parse_subject

DAY = date(2026, 1, 14)


def parse(text):
    return parse_subject(DAY, text)


@pytest.mark.parametrize("text, ratio", [("Bonus 1:1", 2.0), ("Bonus 1: 1", 2.0), (" Bonus 1:1", 2.0), ("Bonus 1:4", 1.25), ("Bonus 3:2", 2.5)])
def test_a_bonus_of_a_new_shares_per_b_held_multiplies_the_share_count_by_a_plus_b_over_b(text, ratio):
    got = parse(text)
    assert got.kind == "bonus" and got.ratio == pytest.approx(ratio) and got.day == DAY and got.source == "nse" and got.text == text.strip()


@pytest.mark.parametrize(
    "text, ratio",
    [
        ("Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share", 5.0),
        ("Face Value Split From Rs.10/- To Rs.5/-", 2.0),
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 10.0),
    ],
)
def test_a_face_value_split_has_the_ratio_of_the_old_face_value_to_the_new(text, ratio):
    got = parse(text)
    assert got.kind == "split" and got.ratio == pytest.approx(ratio)


def test_a_rights_issue_carries_its_ratio_and_premium_and_a_demerger_has_neither():
    rights = parse(" Rights 1:15 @ Premium Rs 1247")
    assert rights.kind == "rights" and rights.ratio == pytest.approx(1 / 15) and rights.premium == 1247.0
    demerger = parse("Demerger")
    assert demerger.kind == "demerger" and demerger.ratio is None and demerger.premium is None


@pytest.mark.parametrize("text", ["Buy Back", "Buyback", " Buyback"])
def test_buybacks_are_recognised_however_they_are_spelled(text):
    assert parse(text).kind == "buyback"


@pytest.mark.parametrize(
    "text",
    ["Dividend - Rs 6 Per Share", "Interim Dividend - Re 1 Per Share", "Annual General Meeting/Dividend Rs 0.80 Per Share", "Annual General Meeting", "Dividend - Rs 1.50 Per Sh"],
)
def test_ordinary_dividends_and_meetings_are_not_events(text):
    assert parse(text) is None


def test_other_things_are_kept_with_their_text_never_guessed_at():
    assert parse("Special Dividend - Rs 40 Per Share").kind == "other"
    assert parse("Capital Reduction").kind == "capital_reduction"
    assert parse("Scheme Of Arrangement - Bonus Ncrps 4:1").kind == "scheme"
    assert parse("Something Unheard Of").kind == "other" and parse("Something Unheard Of").text == "Something Unheard Of"
    unreadable = parse("Bonus")
    assert unreadable.kind == "bonus" and unreadable.ratio is None
    assert parse("Face Value Split").ratio is None


def test_a_corporate_action_is_a_plain_value_object():
    assert CorporateAction(DAY, "demerger", None, "Demerger") == parse("Demerger")
