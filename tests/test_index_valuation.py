from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.freshness import DEFAULT_LIMITS, Limit
from athena.loaders.index_valuation import DATASET, IndexValuationLoader, parse_valuation_rows, valuation_history
from athena.store import DataStore

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)  # Mon 09:30 IST


def vrow(day, pe, pb, dy, name="Nifty 50"):
    return {"RequestNumber": "x", "Index Name": name, "pe": pe, "pb": pb, "divYield": dy, "DATE": day}


ROWS = [vrow("05 Oct 2026", "19.3", "2.77", "1.23"), vrow("01 Oct 2026", "19.1", "2.75", ".72")]


def make_loader(store, rows=ROWS, calls=None):
    def fetch(index_name, start, end):
        if calls is not None:
            calls.append((index_name, start, end))
        return rows

    return IndexValuationLoader(store, fetch=fetch, clock=lambda: NOW)


def test_parse_sorts_ascending_and_reads_a_leading_dot_yield():
    assert parse_valuation_rows(ROWS) == [(date(2026, 10, 1), 19.1, 2.75, 0.72), (date(2026, 10, 5), 19.3, 2.77, 1.23)]


@pytest.mark.parametrize("bad", [{"DATE": "2026-10-05", "pe": "1", "pb": "1", "divYield": "1"}, {"DATE": "05 Oct 2026", "pe": "1"}, {"DATE": "05 Oct 2026", "pe": "-", "pb": "1", "divYield": "1"}])
def test_a_malformed_row_is_a_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_valuation_rows([bad])


def test_refresh_stores_each_day_and_history_reads_them_by_trading_date():
    store = DataStore()
    result = make_loader(store).refresh()
    assert (result.dataset, result.rows_written, result.as_of) == (DATASET, 2, NOW)
    history = valuation_history(store)
    assert history[date(2026, 10, 5)] == {"pe": 19.3, "pb": 2.77, "div_yield": 1.23}
    assert history[date(2026, 10, 1)]["pe"] == 19.1


def test_a_second_refresh_asks_from_the_last_stored_day_and_writes_only_new_rows():
    store, calls = DataStore(), []
    loader = make_loader(store, calls=calls)
    loader.refresh()
    assert loader.refresh().rows_written == 0  # nothing newer than 5 Oct
    assert calls[1][1] == date(2026, 10, 5)


def test_an_explicit_since_and_index_name_are_honoured():
    store, calls = DataStore(), []
    make_loader(store, calls=calls).refresh(since=date(2015, 1, 1), index_name="NIFTY BANK")
    assert calls[0][0] == "NIFTY BANK" and calls[0][1] == date(2015, 1, 1)
    assert valuation_history(store, "NIFTY BANK") and valuation_history(store) == {}


def test_empty_response_fails_loud_and_wrong_dataset_is_rejected():
    store = DataStore()
    with pytest.raises(EmptyRefreshError):
        make_loader(store, rows=[]).refresh()
    with pytest.raises(ValueError):
        make_loader(store).refresh("index.tri")
    with pytest.raises(EmptyRefreshError):
        make_loader(store).read(DATASET, "NEVER")


def test_the_loader_satisfies_the_protocol_and_the_dataset_has_a_freshness_limit():
    loader = make_loader(DataStore())
    assert isinstance(loader, BatchLoader) and DATASET in loader.describe()
    assert DEFAULT_LIMITS[DATASET] == Limit(1, "business_days")
