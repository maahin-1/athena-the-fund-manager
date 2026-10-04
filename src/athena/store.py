from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone

import duckdb

from athena.contracts import Record

_UTC = timezone.utc


def _to_db(value: datetime) -> datetime:
    return value.astimezone(_UTC).replace(tzinfo=None)


def _from_db(value: datetime) -> datetime:
    return value.replace(tzinfo=_UTC)


class DataStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._con = duckdb.connect(path)
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS records ("
            "dataset VARCHAR NOT NULL, key VARCHAR NOT NULL, as_of TIMESTAMP NOT NULL, "
            "source VARCHAR NOT NULL, payload VARCHAR NOT NULL)"
        )

    def put(self, record: Record) -> None:
        self._con.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?)",
            [
                record.dataset,
                record.key,
                _to_db(record.as_of),
                record.source,
                json.dumps(record.payload, sort_keys=True),
            ],
        )

    def put_many(self, records: Iterable[Record]) -> int:
        count = 0
        for record in records:
            self.put(record)
            count += 1
        return count

    def latest(self, dataset: str, key: str) -> Record | None:
        return self._select(dataset, key, None)

    def point_in_time(self, dataset: str, key: str, at: datetime) -> Record | None:
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        return self._select(dataset, key, _to_db(at))

    def _select(self, dataset: str, key: str, at: datetime | None) -> Record | None:
        sql = "SELECT dataset, key, as_of, source, payload FROM records WHERE dataset = ? AND key = ?"
        params: list = [dataset, key]
        if at is not None:
            sql += " AND as_of <= ?"
            params.append(at)
        sql += " ORDER BY as_of DESC, rowid DESC LIMIT 1"
        row = self._con.execute(sql, params).fetchone()
        if row is None:
            return None
        return Record(row[0], row[1], _from_db(row[2]), row[3], json.loads(row[4]))

    def export_parquet(self, path: str) -> None:
        escaped = path.replace("'", "''")
        self._con.execute(f"COPY records TO '{escaped}' (FORMAT PARQUET)")

    def close(self) -> None:
        self._con.close()
