from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.index_close import (
    PRICE_DATASET,
    RATE_DATASET,
    RATE_INDEX,
    IndexCloseLoader,
    parse_close_rows,
)
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # Mon 09:30 IST


def crow(day, close, name="Nifty 50"):
    return {"INDEX_NAME": name, "HistoricalDate": day, "OPEN": "-", "HIGH": "-", "LOW": "-", "CLOSE": close}


ROWS = [crow("01 Oct 2026", "22421.95"), crow("30 Sep 2026", "22620.45")]


def make_loader(store, rows=ROWS, calls=None, dataset=PRICE_DATASET, default_index="NIFTY 50"):
    def fetch(index_name, start, end):
        if calls is not None:
            calls.append((index_name, start, end))
        return rows

    return IndexCloseLoader(store, dataset=dataset, default_index=default_index, fetch=fetch, clock=lambda: NOW)


def test_parse_sorts_ascending_and_ignores_dash_columns():
    assert parse_close_rows(ROWS) == [(date(2026, 9, 30), 22620.45), (date(2026, 10, 1), 22421.95)]


@pytest.mark.parametrize("bad", [{"HistoricalDate": "2026-10-01", "CLOSE": "1"}, {"HistoricalDate": "01 Oct 2026"}, {"HistoricalDate": "01 Oct 2026", "CLOSE": "-"}])
def test_malformed_row_is_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_close_rows([bad])


def test_refresh_stores_close_at_ist_midnight():
    store = DataStore()
    assert make_loader(store).refresh().rows_written == 2
    latest = store.latest(PRICE_DATASET, "NIFTY 50")
    assert latest.as_of == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)
    assert latest.payload == {"close": 22421.95}


def test_rate_instance_uses_its_own_dataset_and_index():
    store = DataStore()
    calls = []
    loader = make_loader(store, rows=[crow("01 Oct 2026", "2608.02", RATE_INDEX)], calls=calls, dataset=RATE_DATASET, default_index=RATE_INDEX)
    loader.refresh()
    assert calls[0][0] == RATE_INDEX
    assert store.latest(RATE_DATASET, RATE_INDEX).payload == {"close": 2608.02}
    assert store.latest(PRICE_DATASET, "NIFTY 50") is None


def test_default_window_since_and_incremental_rerun():
    store = DataStore()
    calls = []
    loader = make_loader(store, calls=calls)
    loader.refresh()
    assert loader.refresh().rows_written == 0
    make_loader(DataStore(), calls=calls).refresh(since=date(2026, 9, 25))
    assert calls == [
        ("NIFTY 50", date(2026, 9, 5), date(2026, 10, 5)),
        ("NIFTY 50", date(2026, 10, 1), date(2026, 10, 5)),
        ("NIFTY 50", date(2026, 9, 25), date(2026, 10, 5)),
    ]
    newer = ROWS + [crow("05 Oct 2026", "22500.00")]
    assert make_loader(store, rows=newer).refresh().rows_written == 1


def test_empty_fetch_raises():
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), rows=[]).refresh()


def test_wrong_dataset_and_protocol():
    loader = make_loader(DataStore())
    with pytest.raises(ValueError, match="index.price"):
        loader.refresh("other")
    assert isinstance(loader, BatchLoader)
    with pytest.raises(EmptyRefreshError):
        loader.read(PRICE_DATASET, "NIFTY 50")
