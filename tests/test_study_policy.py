"""The study rules are locked (WP04): changing any of them changes the fingerprint, which fails here until the change is made on purpose and the policy document is updated with it."""
from pathlib import Path

from athena.evaluation.policy import APPROXIMATE_CURRENT, INPUT_POLICY_LABELS, POLICY, STRICT_DATED, StudyPolicy

# The chronology of the pin, all before any result was inspected (the real stores have not been opened). Every earlier fingerprint stays in the policy document with the reason it changed.
FIRST_FINGERPRINT = "a02ccf4140e7a48d"  # 14 November 2026: the four policies as first written
SECOND_FINGERPRINT = "d3681d1ffd4e7c83"  # 14 November 2026: a traded value is required on every liquidity session (a rule made explicit while writing the cohort code)
LOCKED_FINGERPRINT = "84d45dbff07362fd"  # 14 November 2026, on review: the terminal-outcome labels corrected (a sensitivity interval of two assumed scenarios, not bounds); no rule's behaviour changed
DOCUMENT = Path(__file__).parent.parent / "docs" / "audit-2026-11-12" / "WP04-STUDY-POLICY.md"


def test_the_policy_fingerprint_is_the_one_locked_before_any_comparison():
    assert POLICY.fingerprint() == LOCKED_FINGERPRINT


def test_changing_any_rule_changes_the_fingerprint():
    import dataclasses

    for change in ({"min_nominal_price": 5.0}, {"restructuring": "drop"}, {"no_end_price": "drop"}, {"substitute_today": True}, {"future_information_in_selection": True}, {"min_median_traded_value": 1.0},
                   {"terminal_interval_is_bound": True}):
        assert dataclasses.replace(POLICY, **change).fingerprint() != LOCKED_FINGERPRINT, change


def test_the_locked_rules_say_what_the_policy_document_says():
    assert POLICY.cohort_frozen and not POLICY.future_information_in_selection and not POLICY.substitute_today
    assert POLICY.missing_input == "abstain_and_report" and POLICY.restructuring == "unknown_outcome" and POLICY.no_end_price == "scenario_interval"
    assert (POLICY.terminal_loss_scenario, POLICY.terminal_last_mark_scenario) == ("zero_recovery", "last_close")
    assert POLICY.terminal_interval_is_bound is False  # the two scenarios are assumptions: recovery or liquidation proceeds can exceed the last traded price
    assert POLICY.portfolio_returns == "not_computed" and POLICY.signal_study == "association"
    document = DOCUMENT.read_text(encoding="utf-8")
    assert LOCKED_FINGERPRINT in document and "before any" in document


def test_every_earlier_fingerprint_its_reason_and_the_order_of_the_changes_are_kept_in_the_document():
    chronology = DOCUMENT.read_text(encoding="utf-8").split("## Chronology of the fingerprint")[1].split("\n## ")[0]
    positions = [chronology.index(f) for f in (FIRST_FINGERPRINT, SECOND_FINGERPRINT, LOCKED_FINGERPRINT)]
    assert positions == sorted(positions) and len({FIRST_FINGERPRINT, SECOND_FINGERPRINT, LOCKED_FINGERPRINT}) == 3
    assert "liquidity" in chronology[positions[1] : positions[2]] and "sensitivity interval" in chronology[positions[2] :]
    assert "bounded" not in POLICY.to_dict().values()  # the label that read as a guarantee is gone from every result that carries the policy


def test_the_two_input_policies_are_named_and_labelled_apart():
    assert STRICT_DATED != APPROXIMATE_CURRENT and "never filled with today's value" in INPUT_POLICY_LABELS[STRICT_DATED] and "retrospective sensitivity" in INPUT_POLICY_LABELS[APPROXIMATE_CURRENT]
    assert isinstance(POLICY, StudyPolicy) and POLICY.to_dict()["version"] == "wp04-study-policy/1"
