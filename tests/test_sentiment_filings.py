from datetime import date, datetime, timezone
from types import SimpleNamespace

from athena.evidence import structured as st
from athena.evidence.store import EvidenceStore
from athena.sentiment import page
from athena.sentiment.reddit import RedditSearch

NOW = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 29)


class Hub:
    def fetch(self, symbol, company, since, until):
        return SimpleNamespace(items=[], notes=())


def row(**o):
    base = {
        "acqMode": "Market Purchase", "acqName": "P", "acqfromDt": "13-Aug-2026", "acqtoDt": "13-Aug-2026", "company": "Infosys Limited", "date": "14-Aug-2026 10:00", "did": "1", "pid": "1",
        "personCategory": "Promoters", "secAcq": "100", "secType": "Equity Shares", "secVal": "150000", "symbol": "INFY", "tdpTransactionType": "Buy",
    }
    return {**base, **o}


def test_the_filings_box_counts_promoter_trades_in_code_and_says_how_far_the_data_reaches(tmp_path):
    store = EvidenceStore(tmp_path / "e.duckdb")
    store.add_insider(st.parse_insider({"data": [row(), row(pid="2", tdpTransactionType="Sell"), row(pid="3", personCategory="Director"), row(pid="4", symbol="TCS")]}), NOW)
    store.add_flows(st.parse_flows([
        {"buyValue": "1", "category": "DII", "date": "28-Oct-2026", "netValue": "643.35", "sellValue": "1"},
        {"buyValue": "1", "category": "FII/FPI", "date": "28-Oct-2026", "netValue": "-100", "sellValue": "1"},
    ]), NOW)
    store.add_board(st.parse_board([{"bm_symbol": "INFY", "bm_date": "05-Nov-2026", "bm_purpose": "Board Meeting Intimation", "bm_desc": "results", "bm_timestamp": "28-Oct-2026 17:55:08", "sm_name": "Infosys Limited"}]), NOW)
    doc = page.build_page("INFY", "Infosys Limited", TODAY, Hub(), RedditSearch(None), None, store)
    box = doc["filings"]
    assert len(box["insiders"]) == 3 and {i["person"] for i in box["insiders"]} == {"P"}
    assert box["insider_counts"] == {"Buy": 1, "Sell": 1, "Pledge": 0, "Pledge Revoke": 0}  # the director's trade is listed but is not counted as a promoter's
    assert box["insider_reach"].startswith("2026-08-14") and box["board"][0]["date"] == "2026-11-05"
    assert box["flows"] == [{"day": "2026-10-28", "DII": 643.35, "FII/FPI": -100.0}]
    chips = {s["id"]: s for s in doc["sources"]}
    assert chips["nse_pit"]["tier"] == "A" and chips["nse_fiidii"]["scope"] == "market" and chips["nse_board"]["status"] == "never tried"


def test_without_a_store_the_filings_box_is_empty_and_present():
    doc = page.build_page("INFY", "Infosys Limited", TODAY, Hub(), RedditSearch(None))
    assert doc["filings"] == {"insiders": [], "insider_counts": {}, "insider_reach": "", "board": [], "flows": []}
