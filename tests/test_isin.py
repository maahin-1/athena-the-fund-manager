import pytest

from athena.isin import isin_check_digit, isin_checksum_ok, isin_type_hint, looks_like_isin


@pytest.mark.parametrize("isin", ["INE062A01020", "INF204KB14I2", "INE467B01029", "INE002A01018"])
def test_real_isins_pass_the_checksum(isin):
    assert isin_checksum_ok(isin)


def test_wrong_check_digit_fails():
    assert not isin_checksum_ok("INE062A01021")


@pytest.mark.parametrize("text", ["SBIN", "INE062A0102", "INE062A010201", "ine062a01020", "1NE062A01020"])
def test_non_isin_shapes_are_rejected(text):
    assert not looks_like_isin(text)
    assert not isin_checksum_ok(text)


def test_check_digit_helper_builds_valid_isins():
    body = "INF000Z99ZZ"
    assert isin_checksum_ok(body + str(isin_check_digit(body)))


def test_type_hint_is_a_prefix_hint_only():
    assert isin_type_hint("INF204KB14I2") == "fund_or_etf"
    assert isin_type_hint("INE062A01020") == "corporate"
    assert isin_type_hint("US0378331005") is None
