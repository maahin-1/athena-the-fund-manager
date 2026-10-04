from datetime import date, datetime, timedelta, timezone

import pytest

from athena.adapters.prices import (
    JugaadPriceAdapter,
    YahooPriceAdapter,
    bars_from_jugaad,
    bars_from_yahoo,
    drop_non_trading_days,
    ohlcv_chain,
)
from athena.contracts import AllSourcesFailed, DataAdapter, SchemaChangedError, UnsupportedOperation
from athena.trading_calendar import IST, TradingCalendar, ist_date

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
CAL = TradingCalendar.from_dates([date(2026, 10, 2)])


def jrow(naive, close, volume=1000):
    return {"DATE": naive, "OPEN": close - 1, "HIGH": close + 1, "LOW": close - 2, "CLOSE": close, "VOLUME": volume}


def yrow(day, close, volume=1000):
    return {
        "Date": datetime(2026, 10, day, tzinfo=IST),
        "Open": close,
        "High": close,
        "Low": close,
        "Close": close,
        "Volume": volume,
    }


def jugaad(rows=None, error=None, calls=None):
    def fetch(symbol, start, end):
        if calls is not None:
            calls.append((symbol, start, end))
        if error:
            raise error
        return rows

    return JugaadPriceAdapter(fetch_rows=fetch, clock=lambda: NOW)


def yahoo(rows=None, error=None):
    def fetch(symbol, start, end):
        if error:
            raise error
        return rows

    return YahooPriceAdapter(fetch_rows=fetch, clock=lambda: NOW)


def test_jugaad_dates_are_ist_midnight_stored_as_naive_utc():
    bars = bars_from_jugaad([jrow(datetime(2026, 9, 30, 18, 30), 954.1, 7390452)], "SBIN", NOW)
    bar = bars[0]
    assert bar.timestamp == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert ist_date(bar.timestamp) == date(2026, 10, 1)
    assert (bar.symbol, bar.close, bar.volume, bar.source, bar.as_of) == ("SBIN", 954.1, 7390452.0, "jugaad", NOW)


def test_jugaad_bars_are_sorted_ascending():
    rows = [jrow(datetime(2026, 9, 30, 18, 30), 2.0), jrow(datetime(2026, 9, 29, 18, 30), 1.0)]
    assert [b.close for b in bars_from_jugaad(rows, "X", NOW)] == [1.0, 2.0]


def test_jugaad_missing_column_is_schema_change():
    row = jrow(datetime(2026, 9, 30, 18, 30), 1.0)
    del row["VOLUME"]
    with pytest.raises(SchemaChangedError, match="VOLUME"):
        bars_from_jugaad([row], "X", NOW)


def test_jugaad_unexpected_timestamp_convention_fails_loud():
    with pytest.raises(SchemaChangedError, match="timestamp"):
        bars_from_jugaad([jrow(datetime(2026, 10, 1, 0, 0), 1.0)], "X", NOW)


def test_yahoo_dates_convert_to_utc_and_keep_ist_trading_date():
    bar = bars_from_yahoo([yrow(1, 954.1)], "SBIN", NOW)[0]
    assert bar.timestamp.utcoffset() == timedelta(0)
    assert ist_date(bar.timestamp) == date(2026, 10, 1)
    assert (bar.source, bar.close) == ("yahoo", 954.1)


def test_yahoo_missing_column_is_schema_change():
    row = yrow(1, 1.0)
    del row["Close"]
    with pytest.raises(SchemaChangedError, match="Close"):
        bars_from_yahoo([row], "X", NOW)


def test_drop_non_trading_days_removes_holiday_and_weekend_rows():
    bars = bars_from_yahoo([yrow(1, 1.0), yrow(2, 2.0, 0), yrow(3, 3.0), yrow(5, 5.0)], "X", NOW)
    kept = drop_non_trading_days(bars, CAL)
    assert [ist_date(b.timestamp) for b in kept] == [date(2026, 10, 1), date(2026, 10, 5)]


def test_adapter_default_window_and_limit():
    calls = []
    rows = [jrow(datetime(2026, 9, 29, 18, 30), 950.0), jrow(datetime(2026, 9, 30, 18, 30), 954.1)]
    bars = jugaad(rows, calls=calls).fetch_ohlcv("SBIN", "1d", limit=1)
    assert calls == [("SBIN", date(2026, 9, 5), date(2026, 10, 5))]
    assert [b.close for b in bars] == [954.1]


def test_adapter_explicit_since_and_until():
    calls = []
    jugaad([jrow(datetime(2026, 9, 29, 18, 30), 1.0)], calls=calls).fetch_ohlcv(
        "SBIN", "1d", since=date(2026, 9, 21), until=date(2026, 10, 3)
    )
    assert calls == [("SBIN", date(2026, 9, 21), date(2026, 10, 3))]


def test_adapters_reject_other_timeframes_and_quotes():
    adapter = jugaad([])
    with pytest.raises(UnsupportedOperation, match="1d"):
        adapter.fetch_ohlcv("SBIN", "1h")
    with pytest.raises(UnsupportedOperation, match="quotes"):
        adapter.fetch_quote("SBIN")


def test_adapters_describe_capabilities_and_satisfy_protocol():
    for adapter in (jugaad([]), yahoo([])):
        assert adapter.describe()["fetch_ohlcv"] is True
        assert adapter.describe()["fetch_quote"] is False
        assert isinstance(adapter, DataAdapter)


def test_yahoo_adapter_returns_bars():
    bars = yahoo([yrow(1, 954.1)]).fetch_ohlcv("SBIN", "1d")
    assert [b.close for b in bars] == [954.1]


def test_chain_falls_back_to_yahoo_and_removes_holiday_row():
    chain = ohlcv_chain(
        [jugaad(error=ConnectionError("nse down")), yahoo([yrow(1, 954.1), yrow(2, 256.5, 0), yrow(5, 955.0)])],
        CAL,
    )
    result = chain.run("SBIN")
    assert result.source == "yahoo"
    assert [ist_date(b.timestamp) for b in result.value] == [date(2026, 10, 1), date(2026, 10, 5)]
    assert result.failures == (("jugaad", "ConnectionError: nse down"),)


def test_chain_treats_holiday_only_result_as_no_data():
    holiday_only = [jrow(datetime(2026, 10, 1, 18, 30), 256.5)]  # IST 2 Oct = holiday
    chain = ohlcv_chain([jugaad(holiday_only), yahoo([yrow(1, 954.1)])], CAL)
    result = chain.run("SBIN")
    assert result.source == "yahoo"
    assert result.failures == (("jugaad", "returned no data"),)


def test_chain_limit_keeps_newest_trading_bars():
    chain = ohlcv_chain([yahoo([yrow(1, 1.0), yrow(5, 5.0), yrow(6, 6.0)])], CAL)
    result = chain.run("X", limit=2)
    assert [ist_date(b.timestamp) for b in result.value] == [date(2026, 10, 5), date(2026, 10, 6)]


def test_chain_raises_when_every_adapter_fails():
    chain = ohlcv_chain([jugaad(error=RuntimeError("a")), yahoo(error=RuntimeError("b"))], CAL)
    with pytest.raises(AllSourcesFailed):
        chain.run("SBIN")
