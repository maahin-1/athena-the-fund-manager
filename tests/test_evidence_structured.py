from datetime import date, datetime, timezone

import pytest

from athena.contracts import AthenaError
from athena.evidence import structured as st
from athena.evidence.store import EvidenceStore

NOW = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)


def pit_row(**over):
    row = {
        "acqMode": "Market Purchase", "acqName": "A PROMOTER", "acqfromDt": "13-Feb-2026", "acqtoDt": "13-Feb-2026", "afterAcqSharesNo": "1600", "afterAcqSharesPer": "0.5", "befAcqSharesNo": "3920",
        "befAcqSharesPer": "0.9", "company": "Reliance Industries Limited", "date": "18-Feb-2026 19:06", "did": "563850", "pid": "1194033", "personCategory": "Promoters", "secAcq": "2320", "secType": "Equity Shares",
        "secVal": "3294168", "symbol": "RELIANCE", "tdpTransactionType": "Sell", "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/IT_1.xml",
    }
    row.update(over)
    return row


def board_row(**over):
    row = {
        "bm_symbol": "INFY", "bm_date": "05-Nov-2026", "bm_purpose": "Board Meeting Intimation", "bm_desc": "Board Meeting to consider results", "bm_timestamp": "28-Oct-2026 17:55:08",
        "sm_name": "Infosys Limited", "ixbrl": "https://nsearchives.nseindia.com/corporate/ixbrl/x.html",
    }
    row.update(over)
    return row


def test_an_insider_row_is_read_with_the_exchanges_own_time_in_utc_and_numbers_as_numbers():
    (t,) = st.parse_insider({"acqNameList": [], "data": [pit_row()]})
    assert t.symbol == "RELIANCE" and t.txn == "Sell" and t.category == "Promoters" and t.shares == 2320.0 and t.value == 3294168.0
    assert t.filed_at == datetime(2026, 2, 18, 13, 36, tzinfo=timezone.utc)  # 19:06 India time
    assert t.trade_from == date(2026, 2, 13) and t.link.startswith("https://") and t.after_pct == 0.5


def test_rows_without_a_symbol_or_a_readable_time_are_skipped_and_odd_numbers_become_none():
    rows = [pit_row(symbol=""), pit_row(date="not a date"), pit_row(secAcq="-", secVal="", tdpTransactionType=None, xbrl="javascript:alert(1)")]
    (t,) = st.parse_insider({"data": rows})
    assert t.shares is None and t.value is None and t.txn == "-" and t.link == ""


def test_a_reply_that_is_not_a_list_is_a_failure_not_an_empty_day():
    with pytest.raises(AthenaError):
        st.parse_insider({"data": "x"})


def test_flows_and_board_meetings_are_parsed():
    flows = st.parse_flows([{"buyValue": "17240.9", "category": "DII", "date": "28-Oct-2026", "netValue": "643.35", "sellValue": "16597.55"}, {"category": "FII/FPI", "date": "28-Oct-2026", "buyValue": "x", "sellValue": "1", "netValue": "1"}])
    assert [(f.category, f.net, f.day) for f in flows] == [("DII", 643.35, date(2026, 10, 28))]
    (m,) = st.parse_board([board_row()])
    assert m.symbol == "INFY" and m.meeting_date == date(2026, 11, 5) and m.announced_at == datetime(2026, 10, 28, 12, 25, 8, tzinfo=timezone.utc)


def test_the_store_keeps_the_first_seen_time_and_adds_each_row_once(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    trades = st.parse_insider({"data": [pit_row(), pit_row(acqName="SOMEONE ELSE")]})
    assert store.add_insider(trades, NOW) == 2
    later = datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)
    assert store.add_insider(trades, later) == 0
    got = store.insider_between(date(2026, 2, 1), date(2026, 3, 1), "RELIANCE")
    assert len(got) == 2 and all(g["first_seen_at"] == datetime(2026, 10, 29, 8, 0) for g in got)
    assert store.insider_between(date(2026, 3, 1), date(2026, 4, 1), "RELIANCE") == [] and store.insider_newest() == datetime(2026, 2, 18, 13, 36, tzinfo=timezone.utc)


def test_a_flow_day_already_held_is_not_changed_by_a_later_read(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    first = st.parse_flows([{"buyValue": "1", "category": "DII", "date": "28-Oct-2026", "netValue": "5", "sellValue": "-4"}])
    revised = st.parse_flows([{"buyValue": "1", "category": "DII", "date": "28-Oct-2026", "netValue": "9", "sellValue": "-8"}])
    assert store.add_flows(first, NOW) == 1 and store.add_flows(revised, NOW) == 0
    assert store.flows_between(date(2026, 10, 1), date(2026, 10, 31))[0]["net"] == 5.0


def test_board_meetings_are_listed_for_a_symbol_in_date_order(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    meetings = st.parse_board([board_row(), board_row(bm_date="23-Oct-2026", bm_timestamp="16-Sep-2026 18:34:49"), board_row(bm_symbol="TCS")])
    assert store.add_board(meetings, NOW) == 3
    assert [m["meeting_date"] for m in store.board_for("INFY", date(2026, 10, 1), date(2026, 11, 30))] == [date(2026, 10, 23), date(2026, 11, 5)]


def getter(replies):
    """A fake endpoint: url to payload (or an exception to raise)."""
    calls = []

    def get(url, params=None):
        calls.append((url, params))
        reply = replies[url]
        if isinstance(reply, Exception):
            raise reply
        return reply

    get.calls = calls
    return get


def test_a_pass_asks_each_due_source_and_one_failure_does_not_stop_the_others(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    get = getter({
        st.FLOWS_URL: [{"buyValue": "1", "category": "FII/FPI", "date": "28-Oct-2026", "netValue": "-3", "sellValue": "4"}],
        st.BOARD_URL: AthenaError("HTTP 403"),
        st.PIT_URL: {"data": []},
    })
    out = st.collect_structured(store, get, NOW, sleep=lambda s: None, log=lambda s: None)
    assert out == {"nse_fiidii": ("ok", 1), "nse_board": ("failed", 0), "nse_pit": ("empty", 0)}
    health = store.health()
    assert health["nse_board"]["status"] == "failed" and "403" in health["nse_board"]["error"]
    assert health["nse_pit"]["status"] == "empty" and "no insider filings" in health["nse_pit"]["error"]


def test_a_source_that_worked_recently_is_not_asked_again_until_due_unless_forced(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    get = getter({st.FLOWS_URL: [{"buyValue": "1", "category": "DII", "date": "28-Oct-2026", "netValue": "3", "sellValue": "4"}], st.BOARD_URL: [board_row()], st.PIT_URL: {"data": [pit_row()]}})
    quiet = lambda s: None  # noqa: E731
    assert len(st.collect_structured(store, get, NOW, sleep=quiet, log=quiet)) == 3
    soon = datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc)
    assert st.collect_structured(store, get, soon, sleep=quiet, log=quiet) == {}
    assert len(st.collect_structured(store, get, soon, force=True, sleep=quiet, log=quiet)) == 3
    assert set(st.collect_structured(store, get, datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc), sleep=quiet, log=quiet)) == {"nse_fiidii"}  # flows are due every 3 hours, the others every 6


def test_the_backfill_walks_the_window_in_thirty_day_chunks(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    get = getter({st.BOARD_URL: [board_row()], st.PIT_URL: {"data": [pit_row()]}})
    st.backfill(store, get, 70, NOW, sleep=lambda s: None, log=lambda s: None)
    windows = [(p["from_date"], p["to_date"]) for url, p in get.calls if url == st.PIT_URL]
    assert len(windows) == 3 and windows[0][0] == "20-08-2026" and windows[-1][1] == "29-10-2026"
