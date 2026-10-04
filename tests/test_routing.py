import pytest

from athena.routing import ROUTING, route


def test_equity_and_etf_routes():
    assert route("equity") == ("valuation", "moat_quality", "quant_technical", "earnings_intelligence")
    assert route("etf") == ("etf_analyst", "quant_technical")


def test_every_asset_class_has_a_route():
    assert set(ROUTING) == {"equity", "etf", "mutual_fund", "bond"}


def test_unknown_asset_class_is_an_error():
    with pytest.raises(ValueError, match="crypto"):
        route("crypto")
