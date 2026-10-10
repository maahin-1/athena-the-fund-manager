import pytest
from bar_factory import make_bars

from athena.evaluation.schema import validate_judge_verdict
from athena.orchestrator.price_gate import gate_verdict
from athena.price_integrity.events import PriceEvent
from athena.price_integrity.history import CheckedHistory
from athena.risk_overlay.apply import apply_overlay
from athena.trading_calendar import ist_date

VERDICT = {"verdict": "Buy", "conviction": 70, "key_risks": ["a risk"], "resolution_path": "blend"}
FLAGS = ("2026-03-02 (x0.5)",)


def test_no_flags_leave_the_verdict_untouched():
    assert gate_verdict(VERDICT, ()) == VERDICT


@pytest.mark.parametrize("call", ["Buy", "Overweight"])
def test_a_purchase_call_is_held_back_to_hold_and_says_why_and_the_specialists_call_is_kept(call):
    out = gate_verdict({**VERDICT, "verdict": call}, FLAGS)
    assert out["verdict"] == "Hold" and out["pre_overlay_verdict"] == call and out["held_back_by"] == "price data"
    assert out["key_risks"][0] == f"Held back by unreliable price data: the specialists said {call}."
    assert "2026-03-02 (x0.5)" in out["key_risks"][1] and out["key_risks"][-1] == "a risk"
    assert validate_judge_verdict(out) == []


@pytest.mark.parametrize("call", ["Hold", "Underweight", "Sell"])
def test_other_calls_keep_their_verdict_and_gain_the_warning(call):
    out = gate_verdict({**VERDICT, "verdict": call}, FLAGS)
    assert out["verdict"] == call and "pre_overlay_verdict" not in out and "held_back_by" not in out
    assert "no corporate-action record explains" in out["key_risks"][0] and out["key_risks"][-1] == "a risk"
    assert validate_judge_verdict(out) == []


def test_the_input_verdict_is_not_changed():
    before = {**VERDICT, "key_risks": list(VERDICT["key_risks"])}
    gate_verdict(before, FLAGS)
    assert before == {**VERDICT, "key_risks": ["a risk"]}


def test_a_held_back_hold_is_not_held_back_again_by_a_breached_limit():
    out = apply_overlay(gate_verdict(VERDICT, FLAGS), [])
    assert out["verdict"] == "Hold" and out["held_back_by"] == "price data" and out["pre_overlay_verdict"] == "Buy"


# ---- what CheckedHistory flags

CRASH = make_bars([100.0] * 300 + [45.0] * 20, symbol="X")  # a 55% overnight fall with no record
CRASH_DAY = ist_date(CRASH[300].timestamp)


def test_an_unexplained_break_in_the_last_year_is_flagged_with_its_date_and_size():
    history = CheckedHistory(lambda s: list(CRASH), lambda s: [])
    history("X")
    assert history.price_flags("X") == (f"{CRASH_DAY} (x0.45)",)


def test_a_break_older_than_the_window_is_not_flagged():
    history = CheckedHistory(lambda s: list(CRASH), lambda s: [])
    history("X")
    assert history.price_flags("X", bars=10) == ()  # only the last 10 bars, after the break


def test_a_recorded_event_is_explained_so_nothing_is_flagged_and_an_unfetched_symbol_has_no_flags():
    history = CheckedHistory(lambda s: list(CRASH), lambda s: [PriceEvent(CRASH_DAY, 100 / 45 + 0.0)])
    history("X")
    assert history.price_flags("X") == () and history.price_flags("NEVER") == ()
