from datetime import datetime, timezone

import pandas as pd
import pytest

from athena.contracts import BatchLoader, EmptyRefreshError
from athena.loaders.fundamentals import (
    DATASET,
    FundamentalsLoader,
    blank_duplicate_periods,
    earnings_rows,
    frame_to_lines,
    payload_from_frames,
)
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)
YEAR_ENDS = [pd.Timestamp("2026-03-31"), pd.Timestamp("2025-03-31"), pd.Timestamp("2024-03-31")]


def frame(rows, columns=YEAR_ENDS):
    return pd.DataFrame(rows, index=list(rows), columns=None) if False else pd.DataFrame(list(rows.values()), index=list(rows), columns=columns)


INCOME = frame({"Total Revenue": [300.0, 200.0, 100.0], "Net Income": [30.0, 20.0, 10.0], "Diluted EPS": [3.0, 2.0, float("nan")], "Unwanted Line": [1, 2, 3]})


def test_frame_to_lines_keeps_only_wanted_items_with_iso_periods_and_none_for_gaps():
    lines = frame_to_lines(INCOME, ("Total Revenue", "Diluted EPS", "Operating Income"))
    assert lines == {
        "Total Revenue": {"2026-03-31": 300.0, "2025-03-31": 200.0, "2024-03-31": 100.0},
        "Diluted EPS": {"2026-03-31": 3.0, "2025-03-31": 2.0, "2024-03-31": None},
    }  # an item the frame lacks is simply absent


def test_an_empty_or_missing_frame_gives_no_lines():
    assert frame_to_lines(None, ("Total Revenue",)) == {} and frame_to_lines(pd.DataFrame(), ("Total Revenue",)) == {}


def test_periods_with_identical_revenue_and_profit_are_blanked_and_flagged():
    quarters = {
        "Total Revenue": {"2025-12-31": 19918.2, "2025-09-30": 21372.9, "2025-06-30": 19918.2},
        "Net Income": {"2025-12-31": 4931.2, "2025-09-30": 5244.2, "2025-06-30": 4931.2},
        "Diluted EPS": {"2025-12-31": 3.94, "2025-09-30": 4.18, "2025-06-30": 3.94},
    }
    flags = blank_duplicate_periods(quarters, "quarterly")
    assert quarters["Total Revenue"] == {"2025-12-31": None, "2025-09-30": 21372.9, "2025-06-30": None}
    assert quarters["Diluted EPS"]["2025-12-31"] is None and quarters["Diluted EPS"]["2025-09-30"] == 4.18
    assert len(flags) == 1 and "2025-06-30, 2025-12-31" in flags[0] and "possible duplicate" in flags[0]


def test_distinct_and_missing_periods_are_left_alone():
    income = {"Total Revenue": {"a": 1.0, "b": 2.0, "c": None}, "Net Income": {"a": 1.0, "b": 2.0, "c": None}}
    assert blank_duplicate_periods(income, "annual") == []
    assert income["Total Revenue"] == {"a": 1.0, "b": 2.0, "c": None}


def test_earnings_rows_convert_us_eastern_stamps_to_ist_dates_and_nan_to_none():
    stamps = pd.DatetimeIndex([pd.Timestamp("2026-10-29 06:00", tz="America/New_York"), pd.Timestamp("2026-07-31 07:00", tz="America/New_York")])
    table = pd.DataFrame({"EPS Estimate": [3.34, 3.20], "Reported EPS": [float("nan"), 3.76], "Surprise(%)": [float("nan"), 17.48]}, index=stamps)
    assert earnings_rows(table) == [
        {"date": "2026-10-29", "estimate": 3.34, "reported": None, "surprise_pct": None},
        {"date": "2026-07-31", "estimate": 3.20, "reported": 3.76, "surprise_pct": 17.48},
    ]
    assert earnings_rows(None) == []


def test_payload_keeps_the_known_info_keys_and_structure():
    info = {"sector": "Technology", "sharesOutstanding": 100, "irrelevant": "x"}
    payload = payload_from_frames(info, INCOME, None, None, INCOME, None)
    assert payload["info"]["sector"] == "Technology" and payload["info"]["sharesOutstanding"] == 100 and "irrelevant" not in payload["info"]
    assert payload["info"]["marketCap"] is None
    assert set(payload["annual"]) == {"income", "balance", "cashflow"} and payload["annual"]["balance"] == {}
    assert payload["quarterly"]["income"]["Total Revenue"]["2026-03-31"] == 300.0
    assert payload["earnings_dates"] == [] and payload["quality_flags"] == []


def test_payload_flags_a_duplicated_quarter():
    quarterly = frame({"Total Revenue": [5.0, 6.0, 5.0], "Net Income": [1.0, 2.0, 1.0]})
    payload = payload_from_frames({}, INCOME, None, None, quarterly, None)
    assert len(payload["quality_flags"]) == 1 and payload["quarterly"]["income"]["Total Revenue"]["2026-03-31"] is None


def make_loader(payload, store=None):
    store = store or DataStore()
    return FundamentalsLoader(store, fetch=lambda symbol: payload, clock=lambda: NOW), store


GOOD = payload_from_frames({"sector": "Technology"}, INCOME, None, None, None, None)


def test_refresh_stores_one_record_per_symbol_and_read_returns_the_latest():
    loader, store = make_loader(GOOD)
    result = loader.refresh(symbol="TCS")
    assert (result.dataset, result.rows_written, result.as_of, result.source) == (DATASET, 1, NOW, "yfinance")
    record = loader.read(DATASET, "TCS")
    assert record.as_of == NOW and record.payload["info"]["sector"] == "Technology"
    assert store.latest(DATASET, "TCS") is not None


def test_empty_statements_fail_loud_with_a_hint_about_renamed_symbols():
    loader, _ = make_loader(payload_from_frames({}, None, None, None, None, None))
    with pytest.raises(EmptyRefreshError, match="renamed"):
        loader.refresh(symbol="ZOMATO")


def test_refresh_validates_its_arguments_and_read_needs_a_prior_refresh():
    loader, _ = make_loader(GOOD)
    with pytest.raises(ValueError):
        loader.refresh()
    with pytest.raises(ValueError):
        loader.refresh("mf.nav", symbol="TCS")
    with pytest.raises(EmptyRefreshError):
        loader.read(DATASET, "NEVER")


def test_loader_satisfies_the_batch_loader_protocol_and_describes_itself():
    loader, _ = make_loader(GOOD)
    assert isinstance(loader, BatchLoader)
    assert DATASET in loader.describe()


def test_a_source_failure_becomes_a_refresh_error_that_names_the_symbol_but_not_the_internals():
    def broken(symbol):
        raise ConnectionError("secret-host.example connection reset")

    loader = FundamentalsLoader(DataStore(), fetch=broken, clock=lambda: NOW)
    with pytest.raises(EmptyRefreshError) as caught:
        loader.refresh(symbol="TCS")
    assert "TCS" in str(caught.value) and "ConnectionError" in str(caught.value) and "secret-host" not in str(caught.value)
