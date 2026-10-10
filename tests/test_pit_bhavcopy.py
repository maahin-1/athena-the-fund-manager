import io
import zipfile
from datetime import date, datetime, timezone

import pytest

from athena.contracts import AthenaError, SchemaChangedError
from athena.pit.bhavcopy import MIN_GAP, RETRY_WAITS, ArchiveClient, BhavcopyPriceAdapter, BhavcopyStore, parse_bhavcopy, urls_for

OLD = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n"
    "TCS,EQ,3220,3310,3214.1,3302.25,3298.4,3215.45,2672406,8766501093.15,30-JUN-2023,205777,INE467B01029,\n"
    "SBIN,N2,10000,10010,9990,10005,10005,10000,50,500000,30-JUN-2023,3,INE062A08165,\n"
    "SBIN,EQ,580,590,575,588,588,579,9000000,5e9,30-JUN-2023,1000,INE062A01020,\n"
    "ACME,BE,10,11,9,10.5,10.5,10,100,1000,30-JUN-2023,5,INE000000001,\n"
)
NEW = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,"
    "HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    "2024-09-30,2024-09-30,CM,NSE,STK,11536,INE467B01029,TCS,EQ,,,,,TATA CONSULTANCY SERV LT,4286.90,4298.75,4260.05,4268.50,4271.00,4308.70,,4268.50,,,2503416,1e10,212084,F1,1,,,,,\n"
)


def zipped(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("file.csv", text)
    return buffer.getvalue()


def test_the_old_layout_gives_one_equity_row_per_symbol_and_drops_bond_series():
    rows = parse_bhavcopy(OLD)
    assert set(rows) == {"TCS", "SBIN", "ACME"}
    assert rows["SBIN"].series == "EQ" and rows["SBIN"].close == 588  # not the 10,005 bond row
    assert rows["ACME"].series == "BE"
    assert (rows["TCS"].open, rows["TCS"].high, rows["TCS"].low, rows["TCS"].close, rows["TCS"].volume) == (3220, 3310, 3214.1, 3302.25, 2672406)


def test_the_new_layout_is_read_the_same_way():
    row = parse_bhavcopy(NEW)["TCS"]
    assert (row.open, row.close, row.volume) == (4286.90, 4268.50, 2503416)


def test_an_unknown_header_is_a_schema_change_not_an_empty_day():
    with pytest.raises(SchemaChangedError):
        parse_bhavcopy("A,B,C\n1,2,3\n")


def test_a_value_that_is_not_a_number_is_a_schema_change():
    with pytest.raises(SchemaChangedError):
        parse_bhavcopy(OLD.replace("3302.25", "n/a"))


def test_the_layout_in_use_is_tried_first():
    assert "historical/EQUITIES/2023/JUN/cm30JUN2023bhav" in urls_for(date(2023, 6, 30))[0]
    assert "BhavCopy_NSE_CM_0_0_0_20240930" in urls_for(date(2024, 9, 30))[0]
    assert "historical" in urls_for(date(2024, 7, 5))[0]
    assert "BhavCopy" in urls_for(date(2024, 7, 8))[0]


class FakeResponse:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeSession:
    def __init__(self, *statuses):
        self.headers, self.statuses, self.urls = {}, list(statuses), []

    def get(self, url, timeout=None):
        self.urls.append(url)
        return self.statuses.pop(0)


def client(*statuses):
    sleeps = []
    session = FakeSession(*statuses)
    return ArchiveClient(session, sleeps.append, lambda: 0.0), session, sleeps


def test_a_missing_file_is_none():
    c, _, _ = client(FakeResponse(404))
    assert c.get("u") is None


def test_a_refused_request_is_retried_after_the_pauses_then_an_error():
    c, session, sleeps = client(FakeResponse(403), FakeResponse(403), FakeResponse(403))
    with pytest.raises(AthenaError, match="HTTP 403"):
        c.get("u")
    assert len(session.urls) == 3 and set(RETRY_WAITS) <= set(sleeps)


def test_a_refusal_that_clears_gives_the_file():
    c, _, _ = client(FakeResponse(503), FakeResponse(200, b"ok"))
    assert c.get("u") == b"ok"


def test_requests_are_spaced():
    c, _, sleeps = client(FakeResponse(200, b"a"), FakeResponse(200, b"b"))
    c.get("u")
    c.get("u")
    assert sleeps and max(sleeps) == MIN_GAP


def test_a_day_is_fetched_once_and_a_holiday_is_remembered(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return zipped(OLD) if "30JUN2023" in url else None

    store = BhavcopyStore(directory=tmp_path, fetch=fetch)
    assert "TCS" in store.day(date(2023, 6, 30))
    assert "TCS" in store.day(date(2023, 6, 30))
    assert store.day(date(2023, 6, 26)) is None
    assert store.day(date(2023, 6, 26)) is None
    assert len([u for u in calls if "30JUN2023" in u]) == 1
    assert len([u for u in calls if "26JUN2023" in u or "20230626" in u]) == 2  # both addresses once, never again


def test_the_second_address_is_tried_when_the_first_has_no_file(tmp_path):
    store = BhavcopyStore(directory=tmp_path, fetch=lambda url: zipped(NEW) if "BhavCopy" in url else None)
    assert store.day(date(2024, 7, 5))["TCS"].close == 4268.50


def test_a_non_zip_reply_is_an_error(tmp_path):
    store = BhavcopyStore(directory=tmp_path, fetch=lambda url: b"<html>blocked</html>")
    with pytest.raises(AthenaError):
        store.day(date(2023, 6, 30))


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def adapter(tmp_path, files, trading=None):
    store = BhavcopyStore(directory=tmp_path, fetch=lambda url: next((zipped(text) for key, text in files.items() if key in url), None))
    return BhavcopyPriceAdapter(store, trading, lambda: NOW)


def test_bars_carry_the_printed_prices_the_source_name_and_the_ist_midnight_stamp(tmp_path):
    bars = adapter(tmp_path, {"30JUN2023": OLD}).fetch_ohlcv("TCS", "1d", since=date(2023, 6, 29), until=date(2023, 6, 30))
    assert len(bars) == 1  # 29 June has no file in this fixture
    bar = bars[0]
    assert (bar.source, bar.close, bar.volume) == ("bhavcopy", 3302.25, 2672406)
    assert bar.timestamp == datetime(2023, 6, 29, 18, 30, tzinfo=timezone.utc)


def test_a_symbol_not_in_the_file_gives_no_bar_for_that_day(tmp_path):
    assert adapter(tmp_path, {"30JUN2023": OLD}).fetch_ohlcv("NOPE", "1d", since=date(2023, 6, 30), until=date(2023, 6, 30)) == []


def test_weekends_are_not_asked_for(tmp_path):
    asked = []
    store = BhavcopyStore(directory=tmp_path, fetch=lambda url: asked.append(url))
    BhavcopyPriceAdapter(store, None, lambda: NOW).fetch_ohlcv("TCS", "1d", since=date(2023, 7, 1), until=date(2023, 7, 2))
    assert asked == []


def test_a_missing_file_on_a_trading_day_is_an_error_not_a_holiday(tmp_path):
    with pytest.raises(AthenaError, match="trading day"):
        adapter(tmp_path, {}, trading=lambda day: True).fetch_ohlcv("TCS", "1d", since=date(2023, 6, 30), until=date(2023, 6, 30))


def test_a_missing_file_on_a_listed_holiday_is_fine(tmp_path):
    assert adapter(tmp_path, {}, trading=lambda day: False).fetch_ohlcv("TCS", "1d", since=date(2023, 6, 30), until=date(2023, 6, 30)) == []


def test_limit_keeps_the_latest_bars(tmp_path):
    files = {"30JUN2023": OLD, "29JUN2023": OLD}
    bars = adapter(tmp_path, files).fetch_ohlcv("TCS", "1d", since=date(2023, 6, 29), until=date(2023, 6, 30), limit=1)
    assert [b.timestamp for b in bars] == [datetime(2023, 6, 29, 18, 30, tzinfo=timezone.utc)]  # the 30 June session, not the 29th
