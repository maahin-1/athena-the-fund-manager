from datetime import date, datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.warehouse.members import BASE, candidate_file, parse_members, refresh_members
from athena.warehouse.warehouse import Warehouse

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 18, 20, 30, tzinfo=IST)

CSV_BANKS = "Company Name,Industry,Symbol,Series,ISIN Code\nHDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\nState Bank of India,Financial Services,SBIN,EQ,INE062A01020\n"
CSV_BROAD = "Company Name,Industry,Symbol,Series,ISIN Code\nHDFC Bank Ltd.,Financial Services,HDFCBANK,EQ,INE040A01034\nTata Consultancy Services Ltd.,Information Technology,TCS,EQ,INE467B01029\n,,,,\n"


def with_indices(*names):
    w = Warehouse(":memory:")
    for i, name in enumerate(names):
        w._con.execute("INSERT INTO index_bars VALUES (?, ?, 1, 1, 1, 1, NULL, NULL, NULL)", [name, date(2026, 10, 1) + timedelta(days=i)])
    return w


def test_the_file_is_named_from_the_index_the_way_the_exchange_names_it():
    assert candidate_file("NIFTY Bank") == "ind_niftybanklist.csv"
    assert candidate_file("Nifty Midcap150 Quality 50") == "ind_niftymidcap150quality50list.csv"
    assert candidate_file("NIFTY 50") == "ind_nifty50list.csv"


def test_a_constituent_file_is_read_for_its_symbols_and_industries_and_blank_rows_are_skipped():
    got = parse_members("NIFTY BANK", CSV_BROAD)
    assert [(m.index_name, m.symbol, m.industry) for m in got] == [("NIFTY BANK", "HDFCBANK", "Financial Services"), ("NIFTY BANK", "TCS", "Information Technology")]
    assert parse_members("X", "not a constituent file") == []


def test_refresh_stores_every_index_that_has_a_file_counts_the_rest_and_reads_a_name_in_two_cases_once():
    w = with_indices("NIFTY Bank", "Nifty Bank", "Nifty 500", "Nifty 10 yr Benchmark G-Sec")
    asked = []
    files = {BASE + "ind_niftybanklist.csv": CSV_BANKS.encode(), BASE + "ind_nifty500list.csv": CSV_BROAD.encode()}

    def get(url):
        asked.append(url)
        return files.get(url)

    summary = refresh_members(w, get, NOW)
    assert summary == {"indices": 3, "found": 2, "skipped": 1, "failed": 0, "members": 4}
    assert sum(1 for u in asked if u.endswith("ind_niftybanklist.csv")) == 1  # one file for the two spellings
    rows = w._con.execute("SELECT index_name, symbol FROM index_members ORDER BY 1, 2").fetchall()
    assert rows == [("NIFTY 500", "HDFCBANK"), ("NIFTY 500", "TCS"), ("NIFTY BANK", "HDFCBANK"), ("NIFTY BANK", "SBIN")]


def test_a_refresh_in_which_nothing_answers_keeps_the_table_it_had():
    w = with_indices("NIFTY Bank")
    refresh_members(w, lambda url: CSV_BANKS.encode(), NOW)
    with pytest.raises(AthenaError, match="no constituent file"):
        refresh_members(w, lambda url: None, NOW)
    assert w._con.execute("SELECT count(*) FROM index_members").fetchone()[0] == 2

    def refused(url):
        raise AthenaError("HTTP 403")

    with pytest.raises(AthenaError):
        refresh_members(w, refused, NOW)
    assert w._con.execute("SELECT count(*) FROM index_members").fetchone()[0] == 2


def test_one_file_that_fails_does_not_stop_the_others():
    w = with_indices("NIFTY Bank", "Nifty 500")

    def get(url):
        if url.endswith("ind_niftybanklist.csv"):
            raise AthenaError("HTTP 500")
        return CSV_BROAD.encode()

    summary = refresh_members(w, get, NOW)
    assert summary["found"] == 1 and summary["failed"] == 1
    assert w.members_status()["indices"] == 1
