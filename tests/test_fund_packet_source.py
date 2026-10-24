from datetime import date, datetime, timezone

import pytest

from athena.fundamentals import packet_source
from athena.fundamentals.store import FundamentalsStore
from athena.metrics.fundamentals import build_fundamentals_packet

FILED = datetime(2026, 5, 10)
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)
YEARS = [date(2023, 3, 31), date(2024, 3, 31), date(2025, 3, 31), date(2026, 3, 31)]
QUARTERS = [(date(2025, 6, 30), date(2025, 4, 1)), (date(2025, 9, 30), date(2025, 7, 1)), (date(2025, 12, 31), date(2025, 10, 1)), (date(2026, 3, 31), date(2026, 1, 1))]


def put(store, symbol, basis, statement, start, end, **lines):
    store._con.executemany(
        "INSERT INTO statement_lines VALUES (?, ?, ?, ?, ?, ?, ?, 'u', ?, 'test')", [(symbol, basis, statement, start, end, k, v, FILED) for k, v in lines.items()]
    )


def company(store, symbol, layout="indas", shares=100_000_000.0, basis="consolidated"):
    store.save_company(symbol, "", "", "", layout, shares)
    for i, end in enumerate(YEARS):
        start = date(end.year - 1, 4, 1)
        put(store, symbol, basis, "income", start, end, sales=1000.0e7 * (1 + i), operating_profit=200.0e7 * (1 + i), depreciation=20.0e7, interest=10.0e7, pbt=170.0e7 * (1 + i),
            net_profit=120.0e7 * (1 + i), eps=12.0 * (1 + i))  # net profit over 10 crore shares
        put(store, symbol, basis, "balance", end, end, equity_capital=100.0e7, reserves=900.0e7 * (1 + i), borrowings=300.0e7, total_assets=2000.0e7, current_assets=800.0e7, current_liabilities=400.0e7)
        put(store, symbol, basis, "cashflow", start, end, cfo=150.0e7, capex=-60.0e7)
    for end, start in QUARTERS:
        put(store, symbol, basis, "income", start, end, sales=250.0e7, net_profit=30.0e7, eps=3.0)
    put(store, symbol, basis, "balance", date(2025, 9, 30), date(2025, 9, 30), equity_capital=100.0e7, reserves=1.0e7)  # a half-year balance sheet


def test_the_filings_become_the_payload_the_packet_builder_reads(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        company(store, "ABC")
        payload, read = packet_source.payload_from_store(store, "ABC", table={})
    assert payload["info"]["sharesOutstanding"] == 100_000_000.0 and "sector" not in payload["info"]
    assert set(payload["annual"]["income"]["Net Income"]) == {d.isoformat() for d in YEARS}
    assert payload["annual"]["income"]["EBIT"]["2026-03-31"] == pytest.approx(690.0e7)  # profit before tax plus interest
    assert set(payload["annual"]["balance"]["Stockholders Equity"]) == {d.isoformat() for d in YEARS}  # the half-year sheet is not a fiscal-year one
    assert payload["annual"]["cashflow"]["Depreciation And Amortization"]["2026-03-31"] == 20.0e7
    assert len(payload["quarterly"]["income"]["Diluted EPS"]) == 4
    assert read.tzinfo is not None


def test_the_packet_is_computed_from_the_filings_and_names_them_as_the_source(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        company(store, "ABC")
        payload, _ = packet_source.payload_from_store(store, "ABC", table={})
    packet = build_fundamentals_packet("ABC", NOW, payload, 120.0, NOW)
    pe = packet["metrics"]["pe_trailing"]
    assert pe["value"] == pytest.approx(120.0 / 12.0) and pe["source"] == packet_source.SOURCE  # four quarters of 3 a share
    assert packet["metrics"]["pb"]["value"] == pytest.approx(120.0 / ((100.0e7 + 3600.0e7) / 1e8), abs=1e-3)
    assert packet["latest_quarter"] == "2026-03-31" and packet["latest_annual_period"] == "2026-03-31"
    assert "owner_earnings_yield" in packet["metrics"] and packet["is_lender"] is False


def test_an_eps_that_disagrees_with_the_chosen_share_count_is_set_aside_not_used(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        company(store, "ABC", shares=1_000_000_000.0)  # ten times the count the EPS was reported on
        payload, _ = packet_source.payload_from_store(store, "ABC", table={})
    packet = build_fundamentals_packet("ABC", NOW, payload, 120.0, NOW)
    assert "pe_trailing" not in packet["metrics"] and "share count" in packet["missing_reasons"]["pe_trailing"]


def test_a_lender_is_marked_financial_and_uses_its_total_income(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        company(store, "BANKX", layout="bank")
        put(store, "BANKX", "consolidated", "income", date(2025, 4, 1), date(2026, 3, 31), total_income=3000.0e7)
        payload, _ = packet_source.payload_from_store(store, "BANKX", table={})
    assert payload["info"]["sector"] == "Financial Services"
    assert payload["annual"]["income"]["Total Revenue"]["2026-03-31"] == 3000.0e7
    packet = build_fundamentals_packet("BANKX", NOW, payload, 120.0, NOW)
    assert packet["is_lender"] is True and "fcf_yield" in packet["not_applicable"]


def test_standalone_is_used_when_there_is_no_consolidated_filing_and_an_unknown_symbol_gives_none(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        company(store, "SOLO", basis="standalone")
        assert packet_source.payload_from_store(store, "SOLO", table={}) is not None
        assert packet_source.payload_from_store(store, "NOPE", table={}) is None
