from athena.contracts import Coverage
from athena.coverage import derive_coverage


def test_full_when_everything_present():
    label, missing = derive_coverage(["nav"], ["manager_tenure"], {"nav": [1.0], "manager_tenure": 5})
    assert label is Coverage.FULL
    assert missing == []


def test_partial_when_only_optional_missing():
    label, missing = derive_coverage(["nav"], ["manager_tenure"], {"nav": [1.0]})
    assert label is Coverage.PARTIAL
    assert missing == ["manager_tenure"]


def test_insufficient_when_critical_missing_and_lists_all_missing():
    label, missing = derive_coverage(["nav", "ter"], ["manager_tenure"], {"ter": 0.9})
    assert label is Coverage.INSUFFICIENT
    assert missing == ["nav", "manager_tenure"]


def test_none_empty_list_empty_dict_empty_string_count_as_missing():
    available = {"a": None, "b": [], "c": {}, "d": ""}
    label, missing = derive_coverage(["a", "b", "c", "d"], [], available)
    assert label is Coverage.INSUFFICIENT
    assert missing == ["a", "b", "c", "d"]


def test_zero_values_count_as_present():
    label, missing = derive_coverage(["beta", "alpha"], [], {"beta": 0.0, "alpha": 0})
    assert label is Coverage.FULL
    assert missing == []


def test_no_requirements_is_full():
    label, missing = derive_coverage([], [], {})
    assert label is Coverage.FULL
    assert missing == []
