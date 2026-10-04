from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Coverage(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class AthenaError(Exception):
    """Base class for all errors raised deliberately by athena."""


class StaleDataError(AthenaError):
    """Data is older than its dataset's staleness limit."""


class EmptyRefreshError(AthenaError):
    """A batch loader pulled a source and found no rows."""


class SchemaChangedError(AthenaError):
    """A source's shape no longer matches what the loader expects."""


class AllSourcesFailed(AthenaError):
    """Every source in a fallback chain failed or was skipped."""


class UnsupportedOperation(AthenaError):
    """An adapter was asked for a capability its describe() map declares absent."""


@dataclass(frozen=True)
class Record:
    dataset: str
    key: str
    as_of: datetime
    source: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    as_of: datetime
    source: str


@dataclass(frozen=True)
class Bar:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    as_of: datetime
    source: str


@dataclass(frozen=True)
class RefreshResult:
    dataset: str
    rows_written: int
    as_of: datetime
    source: str


@runtime_checkable
class DataAdapter(Protocol):
    def describe(self) -> dict: ...

    def fetch_quote(self, symbol: str, **params: Any) -> Quote: ...

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: Any = None, limit: Any = None, **params: Any
    ) -> list[Bar]: ...


@runtime_checkable
class BrokerAdapter(DataAdapter, Protocol):
    def place_order(
        self, symbol: str, side: str, qty: float, order_type: str, **params: Any
    ) -> Any: ...


@runtime_checkable
class BatchLoader(Protocol):
    def describe(self) -> dict: ...

    def refresh(self, dataset: str, since: Any = None) -> RefreshResult: ...

    def read(self, dataset: str, key: str, **params: Any) -> Record: ...
