from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest

from athena.contracts import AthenaError
from athena.fundamentals import page, ratios
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore, open_read

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"


def stored(*names: str) -> FundamentalsStore:
    store = FundamentalsStore(":memory:")
    for i, name in enumerate(names):
        statements = parse_rows((FIXTURES / f"{name}.xml").read_text(encoding="utf-8"))
        store.save_statements(statements, f"u{i}", datetime(2024, 4, 12 + i, tzinfo=timezone.utc))
        store.save_company(statements.symbol, f"{statements.symbol} Ltd", "INE", "IT", statements.layout, statements.shares)
    return store


def row(table: list[dict], key: str) -> dict:
    return next(r for r in table if r["key"] == key)


def test_annual_columns_run_oldest_to_newest_and_rupee_rows_are_in_crores():
    with stored("tcs_fy2020", "tcs_fy2024") as store:
        doc = page.build_page(store, "TCS", "consolidated", None)
        raw = store.lines("TCS", "consolidated", "income")
    ends = doc["annual"]["ends"]
    assert ends == sorted(ends) and "2024-03-31" in ends and "2020-03-31" in ends
    fy24 = next(v for (s, e), v in raw.items() if e == date(2024, 3, 31) and (e - s).days > 300)
    sales = row(doc["annual"]["income"], "sales")
    assert sales["values"][ends.index("2024-03-31")] == pytest.approx(ratios.crore(fy24["sales"]))
    assert row(doc["annual"]["income"], "eps")["values"][ends.index("2024-03-31")] == pytest.approx(fy24["eps"])  # per share, not scaled


def test_a_missing_figure_is_none_and_never_zero():
    with stored("tcs_fy2024") as store:
        doc = page.build_page(store, "TCS", "consolidated", None)
    tables = [doc["annual"][name] for name in ("income", "balance", "cashflow")]
    assert all(v is None or isinstance(v, float) for table in tables for r in table for v in r["values"])
    quarters = doc["quarters"]
    assert quarters["ends"] == ["2024-03-31"]  # an annual filing holds its last quarter and nothing else
    assert all(len(r["values"]) == 1 for r in quarters["rows"])
    assert all(r["ttm"] is None for r in doc["annual"]["income"])  # four consecutive quarters are not held; nothing is invented
    for table in tables:  # a row is listed only when some year has it, and a year without it is None
        assert all(any(v is not None for v in r["values"]) for r in table)


def test_price_adds_market_figures_and_no_price_leaves_them_out():
    with stored("tcs_fy2024") as store:
        without = page.build_page(store, "TCS", "consolidated", None)
        with_price = page.build_page(store, "TCS", "consolidated", 3200.0, "warehouse close 2026-10-16")
    assert without["price"] is None and not any(k["label"] == "Market Cap" for k in without["key"])
    cap = next(k for k in with_price["key"] if k["label"] == "Market Cap")
    assert cap["value"] > 0 and "shares" in cap["how"]
    assert with_price["price_note"] == "warehouse close 2026-10-16"
    assert any(k["label"] == "Stock P/E" for k in with_price["key"])


def test_growth_tables_hold_none_when_the_earlier_year_is_not_stored():
    with stored("tcs_fy2024") as store:
        doc = page.build_page(store, "TCS", "consolidated", None)
    table = next(t for t in doc["growth"] if t["title"] == "Compounded Sales Growth")
    assert [r["label"] for r in table["rows"]] == ["10 Years", "5 Years", "3 Years", "TTM"]
    assert all(r["value"] is None for r in table["rows"][:3])


def test_compounded_growth_uses_the_stored_years():
    with stored("tcs_fy2020", "tcs_fy2024") as store:
        doc = page.build_page(store, "TCS", "consolidated", None)
        annual = ratios.annual(store.lines("TCS", "consolidated", "income"))
    table = next(t for t in doc["growth"] if t["title"] == "Compounded Sales Growth")
    assert table["rows"][2]["value"] is None  # 2021 is not held
    expected = ratios.cagr(annual[date(2024, 3, 31)]["sales"], annual[date(2020, 3, 31)]["sales"], 4)
    assert expected is not None and expected > 0


def test_an_unknown_symbol_says_so_and_both_bases_are_listed():
    with stored("tcs_fy2024") as store:
        assert page.build_page(store, "NOPE", "consolidated", None)["error"] == "no statements stored"
        assert page.build_page(store, "TCS", "consolidated", None)["bases"] == ["consolidated"]


def test_gaps_name_the_years_without_balance_sheets():
    with stored("tcs_fy2020", "tcs_fy2024") as store:
        doc = page.build_page(store, "TCS", "consolidated", None)
        first_income = min(ratios.annual(store.lines("TCS", "consolidated", "income")))
        first_balance = min(e for (_, e) in store.lines("TCS", "consolidated", "balance"))
    assert bool(doc["gaps"]) == (first_income < first_balance)


def test_a_bank_page_carries_bank_rows():
    with stored("hdfcbank_fy2024") as store:
        doc = page.build_page(store, "HDFCBANK", "consolidated", None)
    keys = {r["key"] for r in doc["annual"]["income"]}
    assert "pre_provision_profit" in keys and "provisions" in keys
    assert {r["key"] for r in doc["annual"]["balance"]} >= {"deposits", "advances"}


def test_the_ratio_rows_say_how_they_are_worked_out():
    with stored("tcs_fy2024") as store:
        doc = page.build_page(store, "TCS", "consolidated", None)
    roe = row(doc["ratios"]["rows"], "roe")
    assert "net profit" in roe["how"].lower()


def test_open_read_retries_a_locked_file_then_gives_up(tmp_path, monkeypatch):
    path = tmp_path / "f.duckdb"
    duckdb.connect(str(path)).close()
    calls = {"n": 0}

    def locked(*args, **kwargs):
        calls["n"] += 1
        raise duckdb.IOException("Cannot open file: being used by another process")

    monkeypatch.setattr("athena.fundamentals.store.duckdb.connect", locked)
    with pytest.raises(AthenaError, match="busy"):
        open_read(path, tries=3, pause=0, sleep=lambda s: None)
    assert calls["n"] == 3


def test_open_read_on_a_missing_file_does_not_wait(tmp_path):
    with pytest.raises(AthenaError, match="ingest"):
        open_read(tmp_path / "nothing.duckdb", tries=3, pause=0, sleep=lambda s: pytest.fail("should not retry"))


def test_a_filing_with_no_revenue_line_says_it_is_not_supported_yet():
    with FundamentalsStore(":memory:") as store:
        store._con.execute(
            "INSERT INTO statement_lines VALUES ('LIFE', 'consolidated', 'income', DATE '2025-04-01', DATE '2026-03-31', 'pbt', 100.0, 'u', now(), 'nse-xbrl')"
        )
        store.save_company("LIFE", "Some Life Insurance", "INE", "Insurance", "indas", 1.0)
        doc = page.build_page(store, "LIFE", "consolidated", None)
    assert any("insurers are not supported" in g for g in doc["gaps"])


def test_an_insurer_page_has_premium_rows_and_return_on_equity_only():
    with stored("hdfclife_fy2026") as store:
        doc = page.build_page(store, "HDFCLIFE", "consolidated", 700.0)
    keys = {r["key"] for r in doc["annual"]["income"]}
    assert {"gross_premium", "net_premium", "benefits_paid", "net_profit"} <= keys
    assert "operating_profit" not in keys and "opm" not in keys
    assert {r["key"] for r in doc["ratios"]["rows"]} == {"roe"}
    assert doc["gaps"] == [] or all("insurers" not in g for g in doc["gaps"])
    assert any(k["label"] == "Market Cap" for k in doc["key"])  # shares are implied from profit over earnings per share
