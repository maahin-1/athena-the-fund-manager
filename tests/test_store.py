from datetime import datetime, timezone

import duckdb
import pytest

from athena.contracts import Record
from athena.store import DataStore

UTC = timezone.utc


def rec(day, nav, source="mftool", key="119551"):
    return Record("mf.nav", key, datetime(2026, 10, day, 18, 0, tzinfo=UTC), source, {"nav": nav})


def test_latest_returns_none_when_empty():
    assert DataStore().latest("mf.nav", "119551") is None


def test_put_and_latest_round_trip_payload_and_timezone():
    store = DataStore()
    store.put(rec(2, 10.5))
    got = store.latest("mf.nav", "119551")
    assert got == rec(2, 10.5)
    assert got.as_of.tzinfo is not None


def test_latest_picks_greatest_as_of_regardless_of_insert_order():
    store = DataStore()
    store.put_many([rec(5, 12.0), rec(2, 10.5), rec(3, 11.0)])
    assert store.latest("mf.nav", "119551").payload == {"nav": 12.0}


def test_put_many_returns_count():
    assert DataStore().put_many([rec(2, 1.0), rec(3, 2.0)]) == 2


def test_point_in_time_returns_newest_record_at_or_before():
    store = DataStore()
    store.put_many([rec(2, 10.5), rec(3, 11.0), rec(5, 12.0)])
    at = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
    assert store.point_in_time("mf.nav", "119551", at).payload == {"nav": 11.0}


def test_point_in_time_before_first_record_is_none():
    store = DataStore()
    store.put(rec(2, 10.5))
    assert store.point_in_time("mf.nav", "119551", datetime(2026, 10, 1, tzinfo=UTC)) is None


def test_point_in_time_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        DataStore().point_in_time("mf.nav", "119551", datetime(2026, 10, 4))


def test_same_as_of_later_insert_wins():
    store = DataStore()
    store.put(rec(2, 10.5, source="first"))
    store.put(rec(2, 10.6, source="second"))
    assert store.latest("mf.nav", "119551").source == "second"


def test_keys_and_datasets_are_isolated():
    store = DataStore()
    store.put(rec(2, 10.5, key="A"))
    assert store.latest("mf.nav", "B") is None
    assert store.latest("mf.ter", "A") is None


def test_file_store_persists_across_reopen(tmp_path):
    path = str(tmp_path / "s.duckdb")
    first = DataStore(path)
    first.put(rec(2, 10.5))
    first.close()
    second = DataStore(path)
    assert second.latest("mf.nav", "119551") == rec(2, 10.5)
    second.close()


def test_export_parquet_writes_all_rows(tmp_path):
    store = DataStore()
    store.put_many([rec(2, 10.5), rec(3, 11.0)])
    out = str(tmp_path / "records.parquet")
    store.export_parquet(out)
    count = duckdb.connect().execute("SELECT count(*) FROM read_parquet(?)", [out]).fetchone()[0]
    assert count == 2


def test_latest_records_returns_newest_per_key_sorted():
    store = DataStore()
    store.put_many([rec(2, 1.0, key="B"), rec(5, 3.0, key="B"), rec(3, 2.0, key="A")])
    store.put(Record("mf.ter", "A", datetime(2026, 10, 3, tzinfo=UTC), "x", {"ter": 1}))
    got = store.latest_records("mf.nav")
    assert [(r.key, r.payload["nav"]) for r in got] == [("A", 2.0), ("B", 3.0)]
    assert store.latest_records("missing") == []
