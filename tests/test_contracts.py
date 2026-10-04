from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from athena.contracts import (
    AllSourcesFailed,
    AthenaError,
    BatchLoader,
    BrokerAdapter,
    Coverage,
    DataAdapter,
    EmptyRefreshError,
    Record,
    RefreshResult,
    SchemaChangedError,
    InsufficientData,
    StaleDataError,
    UnknownInstrument,
    UnsupportedOperation,
)

UTC = timezone.utc


def test_coverage_values_are_exactly_three_labels():
    assert [c.value for c in Coverage] == ["full", "partial", "insufficient"]


def test_record_rejects_naive_as_of():
    with pytest.raises(ValueError, match="timezone-aware"):
        Record("mf.nav", "119551", datetime(2026, 10, 2, 18, 0), "mftool", {"nav": 10.0})


def test_record_is_frozen():
    r = Record("mf.nav", "119551", datetime(2026, 10, 2, 18, 0, tzinfo=UTC), "mftool", {"nav": 10.0})
    with pytest.raises(FrozenInstanceError):
        r.source = "other"


@pytest.mark.parametrize(
    "exc",
    [StaleDataError, EmptyRefreshError, SchemaChangedError, AllSourcesFailed, UnsupportedOperation, UnknownInstrument, InsufficientData],
)
def test_errors_share_a_base_class(exc):
    assert issubclass(exc, AthenaError)


class _DataAdapterImpl:
    def describe(self):
        return {"fetch_quote": True, "fetch_ohlcv": True}

    def fetch_quote(self, symbol, **params):
        return None

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, **params):
        return []


class _BrokerAdapterImpl(_DataAdapterImpl):
    def place_order(self, symbol, side, qty, order_type, **params):
        return None


class _BatchLoaderImpl:
    def describe(self):
        return {"mf.ter": {"cadence": "weekly"}}

    def refresh(self, dataset, since=None):
        return RefreshResult(dataset, 0, datetime(2026, 10, 2, tzinfo=UTC), "amfi")

    def read(self, dataset, key, **params):
        return Record(dataset, key, datetime(2026, 10, 2, tzinfo=UTC), "amfi", {"ter": 1.0})


def test_protocols_are_runtime_checkable():
    assert isinstance(_DataAdapterImpl(), DataAdapter)
    assert isinstance(_BrokerAdapterImpl(), BrokerAdapter)
    assert isinstance(_BatchLoaderImpl(), BatchLoader)
    assert not isinstance(_DataAdapterImpl(), BrokerAdapter)
