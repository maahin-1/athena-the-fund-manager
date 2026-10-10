from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.nse_holidays import DATASET, NseHolidayLoader, load_calendar, parse_holidays
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)


def row(day):
    return {"tradingDate": day, "weekDay": "x", "description": "d", "Sr_no": 1}


PAYLOAD = {
    "CM": [row("20-Oct-2026"), row("02-Oct-2026"), row("25-Dec-2026"), row("01-Jan-2027")],
    "FO": [row("03-Mar-2026")],
}


def make_loader(store, payload=PAYLOAD):
    return NseHolidayLoader(store, fetch=lambda: payload, clock=lambda: NOW)


def test_parse_returns_sorted_unique_cm_dates():
    assert parse_holidays(PAYLOAD) == [
        date(2026, 10, 2),
        date(2026, 10, 20),
        date(2026, 12, 25),
        date(2027, 1, 1),
    ]


def test_missing_segment_is_schema_change():
    with pytest.raises(SchemaChangedError, match="CM"):
        parse_holidays({"FO": [row("03-Mar-2026")]})


def test_empty_segment_is_empty_refresh():
    with pytest.raises(EmptyRefreshError):
        parse_holidays({"CM": []})


@pytest.mark.parametrize("bad", [{"tradingDate": "2026-10-02"}, {"x": 1}])
def test_malformed_row_is_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_holidays({"CM": [bad]})


def test_refresh_writes_one_record_per_year():
    store = DataStore()
    result = make_loader(store).refresh()
    assert result.rows_written == 2
    assert result.dataset == DATASET
    assert store.latest(DATASET, "2026").payload == {"dates": ["2026-10-02", "2026-10-20", "2026-12-25"]}
    assert store.latest(DATASET, "2027").payload == {"dates": ["2027-01-01"]}
    assert store.latest(DATASET, "2026").as_of == NOW


def test_read_returns_record_and_missing_raises():
    store = DataStore()
    loader = make_loader(store)
    with pytest.raises(EmptyRefreshError):
        loader.read(DATASET, "2026")
    loader.refresh()
    assert loader.read(DATASET, "2026").source == "nse.holiday_list"


def test_load_calendar_across_years():
    store = DataStore()
    make_loader(store).refresh()
    cal = load_calendar(store, [2026, 2027])
    assert not cal.is_trading_day(date(2026, 10, 2))
    assert not cal.is_trading_day(date(2027, 1, 1))
    assert cal.is_trading_day(date(2026, 10, 1))


def test_load_calendar_missing_year_raises():
    store = DataStore()
    make_loader(store).refresh()
    with pytest.raises(EmptyRefreshError, match="2028"):
        load_calendar(store, [2028])


def test_wrong_dataset_is_rejected():
    with pytest.raises(ValueError, match="calendar.nse_holidays"):
        make_loader(DataStore()).refresh("other.dataset")


def test_loader_satisfies_batchloader_protocol():
    assert isinstance(make_loader(DataStore()), BatchLoader)


# ---- a flaky feed must not stop the app from starting

from athena.contracts import AthenaError, SchemaChangedError  # noqa: E402
from athena.loaders.nse_holidays import refresh_with_retry  # noqa: E402


class FlakyLoader:
    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def refresh(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise SchemaChangedError("holiday payload has no segment 'CM'")


def test_a_feed_that_fails_twice_and_then_answers_is_loaded_after_waiting_between_tries():
    loader, waits = FlakyLoader(2), []
    assert refresh_with_retry(loader, attempts=3, sleep=waits.append) is None
    assert loader.calls == 3 and waits == [1.0, 2.0]


def test_a_feed_that_never_answers_returns_the_last_error_instead_of_raising():
    loader, waits = FlakyLoader(99), []
    error = refresh_with_retry(loader, attempts=3, sleep=waits.append)
    assert isinstance(error, AthenaError) and "no segment 'CM'" in str(error) and loader.calls == 3 and len(waits) == 2


def test_a_feed_that_answers_first_time_is_not_retried_or_waited_for():
    loader, waits = FlakyLoader(0), []
    assert refresh_with_retry(loader, sleep=waits.append) is None and loader.calls == 1 and waits == []
