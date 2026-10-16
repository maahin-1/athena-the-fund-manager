from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
FY24 = (date(2023, 4, 1), date(2024, 3, 31))
UTC = timezone.utc


def tcs():
    return parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8"))


def test_statements_round_trip_by_period_and_statement():
    st = tcs()
    with FundamentalsStore(":memory:") as store:
        assert store.save_statements(st, "u1", datetime(2024, 4, 12, tzinfo=UTC)) > 0
        income = store.lines("TCS", "consolidated", "income")
        balance = store.lines("TCS", "consolidated", "balance")
        cash = store.lines("TCS", "consolidated", "cashflow")
        assert income[FY24]["sales"] == st.income[FY24]["sales"]
        assert balance[(date(2024, 3, 31), date(2024, 3, 31))]["total_assets"] == 1464490000000.0
        assert cash[FY24]["cfo"] == 443380000000.0
        assert store.bases("TCS") == ["consolidated"]


def test_saving_the_same_filing_twice_does_not_duplicate_rows():
    st = tcs()
    with FundamentalsStore(":memory:") as store:
        first = store.save_statements(st, "u1", datetime(2024, 4, 12, tzinfo=UTC))
        store.save_statements(st, "u1", datetime(2024, 4, 12, tzinfo=UTC))
        total = store._con.execute("SELECT count(*) FROM statement_lines").fetchone()[0]
        assert total == first


def test_a_later_filing_replaces_an_earlier_one_and_an_earlier_one_never_replaces_a_later_one():
    st = tcs()
    with FundamentalsStore(":memory:") as store:
        store.save_statements(st, "old", datetime(2024, 4, 12, tzinfo=UTC))
        revised = {**st.income[FY24], "sales": 1.0}
        newer = type(st)(**{**st.__dict__, "income": {FY24: revised}, "balance": {}, "cashflow": {}, "banking": {}})
        store.save_statements(newer, "new", datetime(2024, 5, 1, tzinfo=UTC))
        assert store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 1.0
        store.save_statements(st, "old2", datetime(2024, 4, 13, tzinfo=UTC))  # filed before the revision: ignored
        assert store.lines("TCS", "consolidated", "income")[FY24]["sales"] == 1.0


def test_filing_status_run_state_and_the_block_marker():
    with FundamentalsStore(":memory:") as store:
        assert store.filing_status("u1") is None
        store.record_filing("u1", "TCS", "consolidated", date(2024, 3, 31), True, datetime(2024, 4, 12, tzinfo=UTC), "ok")
        store.record_filing("u2", "TCS", "consolidated", date(2023, 3, 31), True, datetime(2023, 4, 12, tzinfo=UTC), "failed", "unreadable")
        assert store.filing_status("u1") == "ok" and store.filing_status("u2") == "failed"
        assert store.run("TCS") is None and store.blocked_until() is None
        until = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
        store.set_run("TCS", "blocked", filings=3, error="HTTP 403", blocked_until=until)
        assert store.run("TCS")["status"] == "blocked"
        assert store.blocked_until() == until
        store.set_run("TCS", "ok", filings=62)
        assert store.blocked_until() is None


def test_company_row_and_coverage():
    with FundamentalsStore(":memory:") as store:
        store.save_company("TCS", "Tata Consultancy Services", "INE467B01029", "IT - Software", "indas", 3.6e9)
        assert store.company("TCS")["industry"] == "IT - Software"
        store.save_statements(tcs(), "u1", datetime(2024, 4, 12, tzinfo=UTC))
        cover = store.coverage()
        assert cover and cover[0]["symbol"] == "TCS" and cover[0]["periods"] >= 2
