from datetime import date, datetime, timezone

import pytest

from athena.contracts import BatchLoader, EmptyRefreshError, SchemaChangedError
from athena.loaders.index_tri import DATASET, IndexTriLoader, parse_tri_rows
from athena.store import DataStore

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)  # Mon 09:30 IST


def trow(day, tri, ntr):
    return {"RequestNumber": "x", "Index Name": "Nifty 50", "Date": day, "TotalReturnsIndex": tri, "NTR_Value": ntr}


ROWS = [trow("30 Sep 2026", "34371.66", "29846.45"), trow("01 Oct 2026", "34070.01", "29584.52")]


def make_loader(store, rows=ROWS, calls=None):
    def fetch(index_name, start, end):
        if calls is not None:
            calls.append((index_name, start, end))
        return rows

    return IndexTriLoader(store, fetch=fetch, clock=lambda: NOW)


def test_parse_sorts_ascending_and_converts_numbers():
    assert parse_tri_rows(ROWS) == [
        (date(2026, 9, 30), 34371.66, 29846.45),
        (date(2026, 10, 1), 34070.01, 29584.52),
    ]


@pytest.mark.parametrize(
    "bad", [{"Date": "2026-10-01", "TotalReturnsIndex": "1", "NTR_Value": "1"}, {"Date": "01 Oct 2026"}]
)
def test_malformed_row_is_schema_change(bad):
    with pytest.raises(SchemaChangedError):
        parse_tri_rows([bad])


def test_refresh_writes_a_record_per_date_stamped_at_ist_midnight():
    store = DataStore()
    result = make_loader(store).refresh()
    assert result.rows_written == 2
    latest = store.latest(DATASET, "NIFTY 50")
    assert latest.as_of == datetime(2026, 9, 30, 18, 30, tzinfo=UTC)  # 1 Oct 00:00 IST
    assert latest.payload == {"tri": 34070.01, "ntr": 29584.52}
    assert latest.source == "jugaad.index_tri_raw"


def test_point_in_time_reads_the_earlier_day():
    store = DataStore()
    make_loader(store).refresh()
    earlier = store.point_in_time(DATASET, "NIFTY 50", datetime(2026, 9, 30, 0, 0, tzinfo=UTC))
    assert earlier.payload["tri"] == 34371.66


def test_default_window_is_thirty_days_and_since_overrides_it():
    calls = []
    make_loader(DataStore(), calls=calls).refresh()
    make_loader(DataStore(), calls=calls).refresh(since=date(2026, 9, 25))
    assert calls == [
        ("NIFTY 50", date(2026, 9, 5), date(2026, 10, 5)),
        ("NIFTY 50", date(2026, 9, 25), date(2026, 10, 5)),
    ]


def test_index_name_is_a_parameter():
    calls = []
    make_loader(DataStore(), calls=calls).refresh(index_name="NIFTY NEXT 50")
    assert calls[0][0] == "NIFTY NEXT 50"


def test_rerun_writes_only_newer_dates():
    store = DataStore()
    calls = []
    loader = make_loader(store, calls=calls)
    assert loader.refresh().rows_written == 2
    assert loader.refresh().rows_written == 0
    assert calls[1] == ("NIFTY 50", date(2026, 10, 1), date(2026, 10, 5))  # resumes from latest stored day
    newer = ROWS + [trow("05 Oct 2026", "34100.00", "29600.00")]
    assert make_loader(store, rows=newer).refresh().rows_written == 1
    assert store.latest(DATASET, "NIFTY 50").payload["tri"] == 34100.0


def test_empty_fetch_raises():
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore(), rows=[]).refresh()


def test_wrong_dataset_rejected_and_protocol_holds():
    loader = make_loader(DataStore())
    with pytest.raises(ValueError, match="index.tri"):
        loader.refresh("other")
    assert isinstance(loader, BatchLoader)


def test_read_missing_raises():
    with pytest.raises(EmptyRefreshError):
        make_loader(DataStore()).read(DATASET, "NIFTY 50")
