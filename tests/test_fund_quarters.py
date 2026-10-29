from datetime import date, datetime

from athena.fundamentals.store import FundamentalsStore


def line(store, basis, start, end, key, value, filed, statement="income"):
    store._con.execute("INSERT INTO statement_lines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ["INFY", basis, statement, start, end, key, value, "u", filed, "nse-xbrl"])


def test_quarterly_profit_and_sales_come_back_one_row_per_basis_and_period_with_the_first_filing_time():
    store = FundamentalsStore(":memory:")
    q2_filed, q2_refiled = datetime(2026, 10, 23, 14, 46), datetime(2026, 10, 30, 9, 0)
    line(store, "consolidated", date(2026, 7, 1), date(2026, 9, 30), "net_profit", 7.823e10, q2_filed)
    line(store, "consolidated", date(2026, 7, 1), date(2026, 9, 30), "sales", 4.2279e11, q2_filed)
    line(store, "consolidated", date(2026, 7, 1), date(2026, 9, 30), "net_profit", 7.9e10, q2_refiled)  # a later filing of the same period
    line(store, "consolidated", date(2026, 4, 1), date(2026, 9, 30), "net_profit", 1.5592e11, q2_filed)  # a half-year is not a quarter
    line(store, "standalone", date(2026, 7, 1), date(2026, 9, 30), "net_profit", 7.0e10, q2_filed)
    line(store, "consolidated", date(2026, 7, 1), date(2026, 9, 30), "eps", 19.0, q2_filed)  # not a line asked for
    got = {(q["basis"], q["end"]): q for q in store.quarter_figures("INFY")}
    assert set(got) == {("consolidated", date(2026, 9, 30)), ("standalone", date(2026, 9, 30))}
    top = got[("consolidated", date(2026, 9, 30))]
    assert top["sales"] == 4.2279e11 and top["filed"] == q2_filed and top["start"] == date(2026, 7, 1)
    assert "eps" not in top and store.quarter_figures("TCS") == []
