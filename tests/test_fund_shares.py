from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena.fundamentals import ingest, shares
from athena.fundamentals.rows import parse_rows
from athena.fundamentals.store import FundamentalsStore
from athena.fundamentals.universe import Member
from athena.pit.nse_results import FilingRef

FIXTURES = Path(__file__).parent / "fixtures" / "xbrl"
IST = timezone(timedelta(hours=5, minutes=30))
TCS = Member("TCS", "Tata Consultancy Services", "Information Technology", "INE467B01029")


def count(period, declared, from_profit=None, filed=None):
    return {"url": f"u{period}", "basis": "consolidated", "period_end": period, "filed": filed or datetime(2025, 1, 1), "declared": declared, "from_profit": from_profit}


def test_the_newest_periods_declared_count_wins_over_an_older_one():
    new = count(date(2025, 3, 31), 600e6, 590e6)  # after a bonus
    old = count(date(2024, 3, 31), 300e6, 290e6)
    assert shares.choose([new, old]) == shares.Choice(600e6, "declared", date(2025, 3, 31))


def test_a_declared_count_that_disagrees_with_the_filings_own_profit_and_eps_by_a_scale_is_set_aside():
    wrong_scale = count(date(2025, 12, 31), 6.0775e13, 6.1e8)  # one filing reported the paid-up capital 100,000 times too large
    right = count(date(2025, 3, 31), 6.0775e8, 6.1e8)
    got = shares.choose([wrong_scale, right])
    assert got.shares == 6.0775e8 and got.how == "declared" and got.period_end == date(2025, 3, 31)


def test_a_bonus_or_a_minority_share_of_profit_is_not_taken_for_a_scale_error():
    assert shares.agrees(100.0, 60.0) and shares.agrees(100.0, 300.0)
    assert not shares.agrees(1e8, 1e3) and not shares.agrees(1e3, 1e8)
    assert shares.agrees(100.0, None) and shares.agrees(100.0, 0.0)  # no EPS: nothing to disagree with


def test_a_count_with_no_eps_is_checked_against_the_companys_other_filings():
    odd = count(date(2026, 6, 30), 3.1e13, None)  # the newest filing, no EPS yet, at the wrong scale
    rest = [count(date(2026, 3, 31), 3.13e8, 3.35e8), count(date(2025, 12, 31), 3.13e8, 2.0e8), count(date(2025, 9, 30), 3.13e8, 2.3e8)]
    assert shares.choose([odd, *rest]).shares == 3.13e8
    split = count(date(2026, 6, 30), 2.5e9, None)  # about 10 times the profit-and-EPS counts: a split, not a scale error
    assert shares.choose([split, *rest]).shares == 2.5e9
    assert shares.choose([count(date(2026, 6, 30), 5e8, None)]).shares == 5e8  # nothing to compare with: taken as filed


def test_without_a_declared_count_that_survives_the_profit_over_eps_count_is_used_and_called_approximate():
    got = shares.choose([count(date(2025, 3, 31), 1e15, 5e6), count(date(2024, 3, 31), None, 4e6)])
    assert got.shares == 5e6 and "approximate" in got.how
    assert shares.choose([count(date(2025, 3, 31), None, None)]) == shares.Choice(None, "none")
    assert shares.choose([]) == shares.Choice(None, "none")


def test_a_filing_gives_its_declared_count_and_an_independent_one_from_profit_over_eps():
    st = parse_rows((FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8"))
    assert st.shares_declared == pytest.approx(3.62e9)
    assert st.shares_from_profit == pytest.approx(3.62e9, rel=0.05)
    assert shares.agrees(st.shares_declared, st.shares_from_profit)


def test_the_store_keeps_every_filings_count_and_a_later_company_save_does_not_overwrite_the_chosen_one(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        store.save_company("TCS", "TCS", "INE", "IT", "indas", 111.0)
        store.save_share_count("a", "TCS", "consolidated", date(2025, 3, 31), datetime(2025, 5, 1), 3.62e9, 3.6e9)
        store.save_share_count("b", "TCS", "consolidated", date(2024, 3, 31), datetime(2024, 5, 1), 3.0e9, 3.0e9)
        assert [c["url"] for c in store.share_counts("TCS")] == ["a", "b"]
        assert shares.refresh(store, "TCS").shares == 3.62e9
        assert store.company("TCS")["shares"] == 3.62e9
        store.save_company("TCS", "Tata Consultancy", "INE", "IT", "indas", 999.0)  # a later filing's raw count must not replace the chosen one
        assert store.company("TCS")["shares"] == 3.62e9 and store.company("TCS")["name"] == "Tata Consultancy"


def test_reading_an_older_filing_last_leaves_the_newer_periods_count(tmp_path):
    from tests.test_fund_ingest import FakeResults  # the same fake exchange the ingest tests use

    text = (FIXTURES / "tcs_fy2024.xml").read_text(encoding="utf-8")
    after_bonus = text.replace("3620000000.00", "7240000000.00").replace(">34.37<", ">17.19<").replace(">125.88<", ">62.94<")  # twice the shares, half the EPS
    new = FilingRef("TCS", date(2025, 3, 31), datetime(2025, 5, 1, tzinfo=IST), True, True, "new")
    old = FilingRef("TCS", date(2024, 3, 31), datetime(2024, 5, 1, tzinfo=IST), True, True, "old")
    path = tmp_path / "f.duckdb"
    # the old filing comes last, as it would when a run reads them out of order
    ingest.ingest_symbol(TCS, FakeResults([new, old], {"new": after_bonus, "old": text}), path, cache_dir=tmp_path / "cache", quarters=0)
    with FundamentalsStore(path) as store:
        assert {c["url"] for c in store.share_counts("TCS")} == {"new", "old"}
        assert store.company("TCS")["shares"] == pytest.approx(7.24e9)
