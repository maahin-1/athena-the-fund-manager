import copy
from datetime import datetime, timezone

from fund_fixtures import ACME, PRICE, index_history

from athena.metrics.fundamentals import LENDER_REASON, build_fundamentals_packet

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def packet(payload=ACME, price=PRICE):
    return build_fundamentals_packet("ACME", NOW, payload, price, NOW, index_history())


def test_a_company_has_no_not_applicable_figures():
    assert packet()["not_applicable"] == []


def test_lender_only_gaps_are_listed_as_not_applicable():
    bank = copy.deepcopy(ACME)
    bank["info"]["sector"] = "Financial Services"
    p = packet(bank)
    assert {"fcf_yield", "owner_earnings_yield", "accruals_ratio", "cash_conversion", "debt_to_equity"} <= set(p["not_applicable"])
    assert set(p["not_applicable"]) <= set(p["missing"])
    assert all(p["missing_reasons"][name] == LENDER_REASON for name in p["not_applicable"])


def test_a_genuine_gap_is_not_marked_not_applicable():
    p = packet(price=None)
    assert "pe_trailing" in p["missing"] and "pe_trailing" not in p["not_applicable"]
