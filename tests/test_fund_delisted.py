from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb

from athena.fundamentals import delisted as dl


def make(tmp_path):  # noqa: ANN001, ANN201
    wh, fu = tmp_path / "w.duckdb", tmp_path / "f.duckdb"
    con = duckdb.connect(str(wh))
    con.execute("CREATE TABLE bars (symbol VARCHAR, day DATE, close DOUBLE, volume DOUBLE, series VARCHAR)")
    con.execute("CREATE TABLE universe (symbol VARCHAR, kind VARCHAR)")
    con.execute("CREATE TABLE symbol_renames (old VARCHAR, new VARCHAR)")

    def trade(symbol: str, first: date, n: int, series: str = "EQ") -> None:
        con.executemany("INSERT INTO bars VALUES (?, ?, 10, 100, ?)", [[symbol, first + timedelta(days=i), series] for i in range(n)])

    trade("GONE", date(2022, 1, 1), 400)  # stopped in early 2023, no statements: a candidate
    trade("GONEBZ", date(2021, 1, 1), 500, "BZ")  # last traded in trade-for-trade: a candidate (the series is still the main board)
    trade("HAVE", date(2022, 1, 1), 400)  # has statements
    trade("OLDNAME", date(2022, 1, 1), 400)  # the old symbol of a company that carries on
    trade("SHORT", date(2022, 1, 1), 150)  # too few bars
    trade("ANCIENT", date(2010, 1, 1), 400)  # stopped before the window
    trade("ALIVE", date(2026, 1, 1), 400)  # still trading
    trade("SMEONE", date(2022, 1, 1), 400, "SM")  # an SME series
    trade("SOMEETF", date(2022, 1, 1), 400)
    con.execute("INSERT INTO symbol_renames VALUES ('OLDNAME', 'NEWNAME')")
    con.execute("INSERT INTO universe VALUES ('SOMEETF', 'etf')")
    con.close()
    con = duckdb.connect(str(fu))
    con.execute("CREATE TABLE statement_lines (symbol VARCHAR, key VARCHAR, value DOUBLE)")
    con.execute("INSERT INTO statement_lines VALUES ('HAVE', 'sales', 1)")
    con.close()
    return wh, fu


def test_only_main_board_companies_that_stopped_in_the_window_with_no_statements_and_no_successor_are_listed(tmp_path) -> None:  # noqa: ANN001
    wh, fu = make(tmp_path)
    got = dl.candidates(wh, fu, date(2019, 1, 1), date(2026, 10, 1))
    assert {r["symbol"] for r in got} == {"GONE", "GONEBZ"}
    assert next(r for r in got if r["symbol"] == "GONE")["bars"] == 400 and next(r for r in got if r["symbol"] == "GONEBZ")["last_series"] == "BZ"
    assert [r["symbol"] for r in got] == ["GONEBZ", "GONE"] or [r["symbol"] for r in got] == ["GONE", "GONEBZ"]  # newest stop first
    assert got == sorted(got, key=lambda r: (r["last"], r["symbol"]), reverse=True)


def test_a_wider_window_adds_the_ancient_one_and_a_stricter_bar_count_drops_the_short_ones(tmp_path) -> None:  # noqa: ANN001
    wh, fu = make(tmp_path)
    assert "ANCIENT" in {r["symbol"] for r in dl.candidates(wh, fu, date(2005, 1, 1), date(2026, 10, 1))}
    assert {r["symbol"] for r in dl.candidates(wh, fu, date(2019, 1, 1), date(2026, 10, 1), min_bars=450)} == {"GONEBZ"}


def test_the_command_prints_a_count_by_year_or_the_symbols_on_one_line(tmp_path, capsys) -> None:  # noqa: ANN001
    wh, fu = make(tmp_path)
    assert dl.main(["--warehouse", str(wh), "--fundamentals", str(fu), "--list"]) == 0
    assert set(capsys.readouterr().out.strip().split(",")) == {"GONE", "GONEBZ"}
    assert dl.main(["--warehouse", str(wh), "--fundamentals", str(fu)]) == 0
    assert "2 main-board symbols stopped trading" in capsys.readouterr().out


def test_a_warehouse_with_no_rename_table_still_works(tmp_path) -> None:  # noqa: ANN001
    wh, fu = make(tmp_path)
    con = duckdb.connect(str(wh))
    con.execute("DROP TABLE symbol_renames")
    con.close()
    assert "OLDNAME" in {r["symbol"] for r in dl.candidates(wh, fu, date(2019, 1, 1), date(2026, 10, 1))}  # without the table nothing says it is an old symbol
    _ = datetime  # (the module imports are kept for the fixtures above)
