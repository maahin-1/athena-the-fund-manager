from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import datetime
from typing import Any

from athena.clock import utc_now
from athena.contracts import EmptyRefreshError, Record, RefreshResult, SchemaChangedError
from athena.store import DataStore

EQUITY_DATASET = "master.nse_equity"
ETF_DATASET = "master.nse_etf"
SOURCE = "nse.archives"
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
ETF_URL = "https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv"

_SPECS: dict[str, tuple[str, str, dict[str, str]]] = {
    EQUITY_DATASET: (
        EQUITY_URL,
        "SYMBOL",
        {
            "name": "NAME OF COMPANY",
            "series": "SERIES",
            "isin": "ISIN NUMBER",
            "listing_date": "DATE OF LISTING",
        },
    ),
    ETF_DATASET: (
        ETF_URL,
        "Symbol",
        {
            "name": "SecurityName",
            "isin": "ISINNumber",
            "underlying": "Underlying Asset",
            "underlying_class": "ETF Underlying",
            "underlying_key": "Underlying Key",
        },
    ),
}


def default_fetch_text(url: str) -> str:
    import requests

    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    return response.text


def parse_master(
    text: str, key_column: str, columns: dict[str, str]
) -> dict[str, dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise EmptyRefreshError("master file is empty")
    present = {name.strip() for name in reader.fieldnames}
    missing = [col for col in (key_column, *columns.values()) if col not in present]
    if missing:
        raise SchemaChangedError(f"master file is missing columns {missing}")
    parsed: dict[str, dict[str, str]] = {}
    for raw in reader:
        row = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        key = row.get(key_column, "")
        if not key:
            continue
        parsed[key] = {field: row[col] for field, col in columns.items()}
    if not parsed:
        raise EmptyRefreshError("master file has no rows")
    return parsed


class NseMasterLoader:
    def __init__(
        self,
        store: DataStore,
        fetch_text: Callable[[str], str] = default_fetch_text,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._fetch_text = fetch_text
        self._clock = clock

    def describe(self) -> dict:
        return {
            dataset: {"cadence": "weekly", "source": SOURCE, "url": spec[0]}
            for dataset, spec in _SPECS.items()
        }

    def refresh(self, dataset: str, since: Any = None) -> RefreshResult:
        if dataset not in _SPECS:
            raise ValueError(f"unknown dataset {dataset!r}; expected one of {sorted(_SPECS)}")
        url, key_column, columns = _SPECS[dataset]
        parsed = parse_master(self._fetch_text(url), key_column, columns)
        now = self._clock()
        for symbol, payload in parsed.items():
            self._store.put(Record(dataset, symbol, now, SOURCE, payload))
        return RefreshResult(dataset, len(parsed), now, SOURCE)

    def read(self, dataset: str, key: str, **params: Any) -> Record:
        record = self._store.latest(dataset, key)
        if record is None:
            raise EmptyRefreshError(f"no {dataset} data for {key}; run refresh first")
        return record
