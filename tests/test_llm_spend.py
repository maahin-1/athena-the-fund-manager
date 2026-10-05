import pytest

from athena.llm.errors import SpendCapExceeded
from athena.llm.spend import SpendGuard


def test_cost_uses_pessimistic_flat_prices():
    assert SpendGuard.cost(1_000_000, 0) == 2.5
    assert SpendGuard.cost(0, 1_000_000) == 10.0
    assert SpendGuard.cost(1000, 2000) == pytest.approx(0.0225)


def test_estimate_overestimates_input_tokens_and_charges_the_full_output_allowance():
    guard = SpendGuard("openai", 1.0, "unused")
    assert guard.estimate(prompt_chars=300, max_output_tokens=1000) == SpendGuard.cost(100, 1000)


def test_a_fresh_guard_has_spent_nothing(tmp_path):
    assert SpendGuard("openai", 1.0, tmp_path / "spend.json").spent() == 0.0


def test_recorded_spend_persists_across_guard_instances(tmp_path):
    path = tmp_path / "state" / "spend.json"
    first = SpendGuard("openai", 1.0, path)
    first.record(1000, 2000)
    first.record(1000, 2000)
    assert SpendGuard("openai", 1.0, path).spent() == pytest.approx(0.045)


def test_providers_are_tracked_separately(tmp_path):
    path = tmp_path / "spend.json"
    SpendGuard("openai", 1.0, path).record(1_000_000, 0)
    assert SpendGuard("other", 1.0, path).spent() == 0.0


def test_check_blocks_a_request_that_would_pass_the_cap_but_allows_one_that_fits(tmp_path):
    guard = SpendGuard("openai", 0.05, tmp_path / "spend.json")
    guard.record(1000, 2000)  # $0.0225 spent
    guard.check(0.027)  # lands at 0.0495, under the cap
    with pytest.raises(SpendCapExceeded, match="would pass the cap"):
        guard.check(0.03)


def test_an_unreadable_spend_record_fails_closed(tmp_path):
    path = tmp_path / "spend.json"
    path.write_text("{not json", encoding="utf-8")
    guard = SpendGuard("openai", 1.0, path)
    with pytest.raises(SpendCapExceeded, match="unreadable"):
        guard.check(0.0)
    with pytest.raises(SpendCapExceeded):
        guard.record(1, 1)


def test_negative_cap_is_rejected():
    with pytest.raises(ValueError):
        SpendGuard("openai", -1.0)
