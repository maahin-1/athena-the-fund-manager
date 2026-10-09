import json
import re

import pytest

from athena.risk_overlay.model import MEASURES, PRESETS, Holding, Limit, Overlay
from athena.risk_overlay.parse import (
    ProfileError,
    build_overlay,
    load_holdings,
    load_profile,
    overlay_token,
    parse_holdings,
    parse_profile,
    profile_to_dict,
)


def test_every_preset_has_every_check_and_each_warning_is_below_its_hard_limit():
    assert set(PRESETS) == {"conservative", "moderate", "aggressive"}
    for profile in PRESETS.values():
        assert tuple(profile.limits) == MEASURES
        assert all(0 < limit.warn < limit.hard for limit in profile.limits.values())


def test_a_stricter_preset_has_lower_limits_for_every_check():
    for measure in MEASURES:
        hard = [PRESETS[name].limits[measure].hard for name in ("conservative", "moderate", "aggressive")]
        assert hard == sorted(hard) and len(set(hard)) == 3


def test_a_preset_alone_is_the_preset_and_overrides_replace_or_switch_off_single_checks():
    assert parse_profile({"preset": "moderate"}) == PRESETS["moderate"]
    profile = parse_profile({"preset": "moderate", "name": "mine", "limits": {"volatility": {"warn": 0.4, "hard": 0.6}, "liquidity": None}})
    assert profile.name == "mine"
    assert profile.limits["volatility"] == Limit(0.4, 0.6) and "liquidity" not in profile.limits
    assert profile.limits["drawdown"] == PRESETS["moderate"].limits["drawdown"]


def test_a_profile_without_a_preset_is_exactly_the_limits_given_and_keeps_the_report_order():
    profile = parse_profile({"limits": {"position": {"warn": 0.1, "hard": 0.2}, "volatility": {"warn": 1, "hard": 2}}})
    assert profile.name == "custom" and list(profile.limits) == ["volatility", "position"]


def test_a_profile_turns_back_into_data_that_parses_to_the_same_profile():
    profile = PRESETS["aggressive"]
    assert parse_profile(profile_to_dict(profile)) == profile


@pytest.mark.parametrize(
    "data, expected",
    [
        ([], "a profile must be an object"),
        ({"limits": {}}, "needs at least one limit"),
        ({"preset": "reckless"}, "preset: unknown preset 'reckless'"),
        ({"preset": 3}, "preset: unknown preset 3"),
        ({"preset": "moderate", "colour": 1}, "unexpected key(s) ['colour']"),
        ({"preset": "moderate", "name": ""}, "name: must be text"),
        ({"preset": "moderate", "name": "x" * 61}, "name: must be text"),
        ({"preset": "moderate", "limits": []}, "limits: must be an object"),
        ({"preset": "moderate", "limits": {"luck": {"warn": 1, "hard": 2}}}, "limits.luck: unknown check"),
        ({"preset": "moderate", "limits": {"volatility": 0.3}}, "limits.volatility: must be an object with exactly 'warn' and 'hard'"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3}}}, "limits.volatility: must be an object with exactly"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": 0.4, "x": 1}}}, "limits.volatility: must be an object with exactly"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.5, "hard": 0.4}}}, "limits.volatility: 'warn' must be below 'hard'"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.4, "hard": 0.4}}}, "limits.volatility: 'warn' must be below 'hard'"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0, "hard": 0.4}}}, "limits.volatility.warn: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": -1, "hard": 0.4}}}, "limits.volatility.warn: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": True, "hard": 0.4}}}, "limits.volatility.warn: must be a number"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": "0.3", "hard": 0.4}}}, "limits.volatility.warn: must be a number"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": float("inf")}}}, "limits.volatility.hard: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": float("nan")}}}, "limits.volatility.hard: must be a number above zero"),
        ({"preset": "moderate", "limits": {"volatility": {"warn": 0.3, "hard": 10**400}}}, "limits.volatility.hard: is too large"),
    ],
)
def test_a_bad_profile_is_refused_with_the_path_of_the_wrong_part(data, expected):
    with pytest.raises(ProfileError, match=re.escape(expected)):
        parse_profile(data)


@pytest.mark.parametrize("data", [None, 5, "text", {"preset": []}, {"preset": {}}, {"limits": {"volatility": []}}, {"name": []}, {1: 2}])
def test_whatever_the_data_holds_only_a_profile_error_comes_out(data):
    with pytest.raises(ProfileError):
        parse_profile(data)


def test_a_profile_error_is_a_value_error_so_callers_can_catch_either():
    assert issubclass(ProfileError, ValueError)


HEADER = "symbol,value\n"


def test_holdings_are_read_with_names_cleaned_numbers_with_commas_and_repeats_added_up():
    holdings = parse_holdings(HEADER + "sbin, 50000\nNSE:TCS.NS,\"1,20,000\"\nSBIN,25000.5\n\n")
    assert holdings == (Holding("SBIN", 75000.5), Holding("TCS", 120000.0))


def test_the_header_may_have_any_case_spaces_and_a_byte_order_mark_and_empty_text_is_no_holdings():
    assert parse_holdings("﻿ Symbol , VALUE \nITC,10\n") == (Holding("ITC", 10.0),)
    assert parse_holdings("") == () and parse_holdings("   \n") == ()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ticker,amount\nSBIN,1\n", "the first row must be the header 'symbol,value'"),
        ("SBIN,1\n", "the first row must be the header 'symbol,value'"),
        (HEADER + "SBIN\n", "row 2: expected 2 columns"),
        (HEADER + "SBIN,1,2\n", "row 2: expected 2 columns"),
        (HEADER + "SBIN,1\n,5\n", "row 3: the symbol is empty"),
        (HEADER + "SBIN,lots\n", "row 2: 'lots' is not a number"),
        (HEADER + "SBIN,0\n", "row 2: the value must be a number above zero"),
        (HEADER + "SBIN,-5\n", "row 2: the value must be a number above zero"),
        (HEADER + "SBIN,nan\n", "row 2: the value must be a number above zero"),
        (HEADER + "SBIN,inf\n", "row 2: the value must be a number above zero"),
    ],
)
def test_bad_holdings_are_refused_with_the_row(text, expected):
    with pytest.raises(ProfileError, match=expected):
        parse_holdings(text)


def test_too_many_different_symbols_are_refused():
    rows = "".join(f"S{n},1\n" for n in range(501))
    with pytest.raises(ProfileError, match="at most 500 different symbols"):
        parse_holdings(HEADER + rows)
    assert len(parse_holdings(HEADER + rows[: rows.index("S500")])) == 500


@pytest.mark.parametrize("rows", ["A,1e308\nA,1e308\n", "A,1e308\nB,1e308\n", "A,2e15\n", "A,6e14\nB,6e14\n"])
def test_holdings_that_add_up_to_more_than_1e15_rupees_are_refused(rows):
    with pytest.raises(ProfileError, match=re.escape("holdings: the values add up to more than 1e15 rupees")):
        parse_holdings(HEADER + rows)
    assert parse_holdings(HEADER + "A,5e14\nB,5e14\n") == (Holding("A", 5e14), Holding("B", 5e14))


def test_holdings_text_that_is_too_long_or_has_too_many_rows_is_refused_before_it_is_read():
    with pytest.raises(ProfileError, match=re.escape("holdings: the text is longer than 1,000,000 characters")):
        parse_holdings(HEADER + "x" * 1_000_000)
    with pytest.raises(ProfileError, match=re.escape("holdings: at most 5000 rows")):
        parse_holdings(HEADER + "SBIN,1\n" * 5001)
    assert parse_holdings(HEADER + "SBIN,1\n" * 5000) == (Holding("SBIN", 5000.0),)


def test_files_that_are_too_large_are_refused_without_being_read_whole(tmp_path):
    big = tmp_path / "big.csv"
    big.write_bytes(HEADER.encode() + b"SBIN,1\n" * 150_000)
    with pytest.raises(ProfileError, match=re.escape("is larger than 1 MB")):
        load_holdings(str(big))
    profile = tmp_path / "big.json"
    profile.write_text(json.dumps({"preset": "moderate", "name": "x" * 50, "pad": "y" * 102_400}), encoding="utf-8")
    with pytest.raises(ProfileError, match=re.escape("is larger than 100 KB")):
        load_profile(str(profile))


def test_a_text_that_is_not_csv_at_all_is_a_profile_error():
    with pytest.raises(ProfileError):
        parse_holdings("symbol,value\n\"unclosed,1\n")


def test_a_profile_loads_from_a_preset_name_or_a_json_file(tmp_path):
    assert load_profile("conservative") is PRESETS["conservative"]
    path = tmp_path / "mine.json"
    path.write_text(json.dumps({"preset": "moderate", "name": "from file"}), encoding="utf-8")
    assert load_profile(str(path)).name == "from file"


def test_a_profile_that_cannot_be_loaded_says_why(tmp_path):
    with pytest.raises(ProfileError, match="is not a preset .* and cannot be read as a file"):
        load_profile(str(tmp_path / "missing.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError, match="is not valid JSON"):
        load_profile(str(broken))
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000, encoding="utf-8")
    with pytest.raises(ProfileError, match="is not valid JSON"):
        load_profile(str(deep))
    binary = tmp_path / "binary.json"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(ProfileError, match="is not a text file"):
        load_profile(str(binary))
    wrong = tmp_path / "wrong.json"
    wrong.write_text("[1]", encoding="utf-8")
    with pytest.raises(ProfileError, match="a profile must be an object"):
        load_profile(str(wrong))


def test_holdings_load_from_a_file_and_a_missing_or_binary_file_is_a_profile_error(tmp_path):
    path = tmp_path / "h.csv"
    path.write_text(HEADER + "SBIN,100\n", encoding="utf-8")
    assert load_holdings(str(path)) == (Holding("SBIN", 100.0),)
    with pytest.raises(ProfileError, match="cannot read"):
        load_holdings(str(tmp_path / "nope.csv"))
    binary = tmp_path / "b.csv"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    with pytest.raises(ProfileError, match="is not a text file"):
        load_holdings(str(binary))


def test_the_command_line_builds_an_overlay_only_when_a_profile_is_named(tmp_path):
    assert build_overlay(None, None, None) is None
    overlay = build_overlay("moderate", None, 5000.0)
    assert overlay == Overlay(PRESETS["moderate"], (), 5000.0)
    path = tmp_path / "h.csv"
    path.write_text(HEADER + "SBIN,100\n", encoding="utf-8")
    assert build_overlay("moderate", str(path), None).holdings == (Holding("SBIN", 100.0),)
    for bad in (dict(profile=None, holdings=str(path), amount=None), dict(profile=None, holdings=None, amount=10.0)):
        with pytest.raises(ProfileError, match="need --profile"):
            build_overlay(**bad)
    for amount in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ProfileError, match="--amount must be a number above zero"):
            build_overlay("moderate", None, amount)
    for amount in (1e308, 1.0000001e15):
        with pytest.raises(ProfileError, match="--amount must be at most 1e15 rupees"):
            build_overlay("moderate", None, amount)
    assert build_overlay("moderate", None, 1e15).amount == 1e15


def test_an_overlay_token_changes_with_anything_that_changes_the_checks():
    base = Overlay(PRESETS["moderate"], (Holding("SBIN", 100.0),), 5000.0)
    tokens = {
        overlay_token(None),
        overlay_token(base),
        overlay_token(Overlay(PRESETS["conservative"], base.holdings, base.amount)),
        overlay_token(Overlay(base.profile, (Holding("SBIN", 101.0),), base.amount)),
        overlay_token(Overlay(base.profile, base.holdings, 5001.0)),
        overlay_token(Overlay(base.profile, base.holdings, None)),
    }
    assert len(tokens) == 6 and overlay_token(base) == overlay_token(Overlay(PRESETS["moderate"], (Holding("SBIN", 100.0),), 5000.0))


@pytest.mark.parametrize("error", [TypeError("x"), OverflowError("x"), RecursionError("x"), ValueError("x")])
def test_an_unforeseen_failure_while_reading_a_profile_still_comes_out_as_a_profile_error(monkeypatch, error):
    def boom(data):
        raise error

    monkeypatch.setattr("athena.risk_overlay.parse._parse_profile", boom)
    with pytest.raises(ProfileError, match="this is not a valid profile"):
        parse_profile({"preset": "moderate"})


def test_an_unforeseen_failure_while_reading_holdings_still_comes_out_as_a_profile_error(monkeypatch):
    import csv

    def boom(text):
        raise csv.Error("x")

    monkeypatch.setattr("athena.risk_overlay.parse._parse_holdings", boom)
    with pytest.raises(ProfileError, match="not a readable CSV"):
        parse_holdings(HEADER + "SBIN,1\n")
