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


def test_a_renamed_company_shows_one_history_with_the_note_and_the_new_symbol_wins_a_shared_period():
    import dataclasses

    from athena.fundamentals.history import Predecessor

    table = {"NEW": (Predecessor("OLD", "OLD was renamed NEW in 2024."),)}
    with FundamentalsStore(":memory:") as store:
        for symbol, name, stamp in (("OLD", "tcs_fy2020", 12), ("NEW", "tcs_fy2024", 13), ("OLD", "tcs_fy2024", 14)):
            statements = dataclasses.replace(parse_rows((FIXTURES / f"{name}.xml").read_text(encoding="utf-8")), symbol=symbol)
            store.save_statements(statements, f"{symbol}-{name}", datetime(2024, 4, stamp, tzinfo=timezone.utc))
            store.save_company(symbol, symbol, "INE", "IT", statements.layout, statements.shares)
        alone = page.build_page(store, "NEW", "consolidated", None, table={})
        merged = page.build_page(store, "NEW", "consolidated", None, table=table)
    assert alone["annual"]["ends"] == ["2024-03-31"] and alone["history"] == []
    assert merged["annual"]["ends"] == ["2020-03-31", "2024-03-31"]
    assert [h["symbol"] for h in merged["history"]] == ["OLD"] and "renamed" in merged["history"][0]["note"]
    assert merged["history"][0]["first"] == "2020-03-31"
    assert merged["coverage"]["first"] == "2020-03-31"


def test_the_successions_form_chains_oldest_first_and_a_loop_cannot_hang():
    from athena.fundamentals.history import Predecessor, chain

    table = {"C": (Predecessor("B", ""),), "B": (Predecessor("A", ""),), "A": (Predecessor("C", ""),)}
    assert [p.symbol for p in chain("C", table)] == ["A", "B"]
    assert chain("NOBODY", table) == []


def test_the_shareholding_section_gives_percentages_by_quarter_and_the_number_of_shareholders():
    from athena.fundamentals.shareholding import parse_shareholding

    shp = Path(__file__).parent / "fixtures" / "shp"
    with stored("tcs_fy2024") as store:
        for name, stamp in (("tcs_2024", 10), ("tcs_2026", 11)):
            store.save_holding("TCS", parse_shareholding((shp / f"{name}.xml").read_text(encoding="utf-8")), f"shp-{name}", datetime(2026, 7, stamp, tzinfo=timezone.utc))
        doc = page.build_page(store, "TCS", "consolidated", None)
        bare = page.build_page(stored("tcs_fy2020"), "TCS", "consolidated", None)
    s = doc["shareholding"]
    assert s["ends"] == ["2024-03-31", "2026-06-30"]
    rows = {r["key"]: r["values"] for r in s["rows"]}
    assert rows["promoters"] == [pytest.approx(71.77, abs=0.01)] * 2
    assert rows["fii"] == [pytest.approx(12.70, abs=0.01), pytest.approx(9.06, abs=0.01)]
    assert rows["holders"] == [2203209.0, 2605182.0]
    assert [r["key"] for r in s["rows"]][:6] == ["promoters", "fii", "dii", "government", "public", "holders"]
    assert bare["shareholding"] == {"ends": [], "rows": []}  # nothing stored is shown as nothing, not as zeros


def test_the_documents_section_lists_stored_links_newest_first_and_merges_an_earlier_symbol():
    import dataclasses

    from athena.fundamentals.documents import Doc
    from athena.fundamentals.history import Predecessor

    ist = timezone.utc
    table = {"TCS": (Predecessor("OLDTCS", "renamed"),)}
    with stored("tcs_fy2024") as store:
        store.save_documents("TCS", ("annual_report",), [Doc("annual_report", "FY2025-26", "Annual report", datetime(2026, 5, 15, tzinfo=ist), "https://nsearchives.nseindia.com/a.pdf", "1 MB")])
        store.save_documents("OLDTCS", ("annual_report",), [Doc("annual_report", "FY2017-18", "Annual report", datetime(2018, 6, 20, tzinfo=ist), "https://nsearchives.nseindia.com/old.zip", "")])
        store.save_documents("TCS", ("concall",), [Doc("concall", "Transcript", "Call", datetime(2026, 10, 14, tzinfo=ist), "https://nsearchives.nseindia.com/t.pdf", "")])
        doc = page.build_page(store, "TCS", "consolidated", None, table=table)
        alone = page.build_page(store, "TCS", "consolidated", None, table={})
    d = doc["documents"]
    assert [x["title"] for x in d["annual_reports"]] == ["FY2025-26", "FY2017-18"]
    assert d["annual_reports"][0] == {"title": "FY2025-26", "category": "Annual report", "date": "2026-05-15", "url": "https://nsearchives.nseindia.com/a.pdf", "size": "1 MB"}
    assert [x["title"] for x in d["concalls"]] == ["Transcript"] and d["announcements"] == []
    assert [x["title"] for x in alone["documents"]["annual_reports"]] == ["FY2025-26"]
    assert dataclasses.is_dataclass(Doc)


def test_the_page_a_server_reads_is_plain_ascii_whatever_the_titles_hold(tmp_path, capsys):
    """A pipe on Windows carries the legacy code page, so a title with a non-breaking space or a rupee sign once made the output invalid UTF-8 and the
    page for that stock never loaded. The document is written with escapes, which every reader decodes the same way."""
    import json

    from athena.fundamentals.documents import Doc

    path = tmp_path / "f.duckdb"
    ist = timezone.utc
    statements = parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8"))
    with FundamentalsStore(path) as store:
        store.save_statements(statements, "u0", datetime(2024, 4, 12, tzinfo=timezone.utc))
        store.save_company("TCS", "TCS Ltd", "INE", "IT", statements.layout, statements.shares)
        store.save_documents("TCS", ("announcement",), [Doc("announcement", "Ministry of Railways\xa0has submitted ₹ 5,000 crore plan – notice", "Updates", datetime(2026, 10, 15, tzinfo=ist), "https://nsearchives.nseindia.com/a.pdf", "")])
    assert page.main(["TCS", "--store", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.isascii()
    titles = [d["title"] for d in json.loads(out)["documents"]["announcements"]]
    assert titles == ["Ministry of Railways\xa0has submitted ₹ 5,000 crore plan – notice"]
