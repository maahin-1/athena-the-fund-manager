from datetime import date, datetime

import pytest

from athena.fundamentals import annual_reports as ar
from athena.fundamentals.store import FundamentalsStore
from test_annual_reports import year


class FailsOnInsert:
    """A connection that carries on as normal until a statement inserts, as a full disk or a crash would."""

    def __init__(self, real):
        self.real = real

    def execute(self, *args):
        return self.real.execute(*args)

    def executemany(self, sql, params):
        if sql.lstrip().upper().startswith("INSERT"):
            raise RuntimeError("disk full")
        return self.real.executemany(sql, params)


def count(store):
    return store._con.execute("SELECT count(*) FROM statement_lines").fetchone()[0]


def test_a_filing_that_fails_halfway_leaves_the_rows_it_would_have_replaced(tmp_path):
    with FundamentalsStore(tmp_path / "f.duckdb") as store:
        store.save_company("X", "X Ltd", "INE1", "IT", "indas", 1e6)
        first = ar.to_statements("X", "consolidated", {date(2019, 3, 31): year(1000)})
        assert store.save_statements(first, "https://x/1.xml", datetime(2020, 5, 1))
        before = count(store)
        assert before > 0
        real = store._con
        store._con = FailsOnInsert(real)
        with pytest.raises(RuntimeError):
            store.save_statements(ar.to_statements("X", "consolidated", {date(2019, 3, 31): year(2000)}), "https://x/2.xml", datetime(2020, 6, 1))
        store._con = real
        assert count(store) == before  # the delete of the older filing's rows was rolled back with the failed insert
        assert store.save_statements(ar.to_statements("X", "consolidated", {date(2019, 3, 31): year(2000)}), "https://x/2.xml", datetime(2020, 6, 1))
        assert count(store) == before  # and the same filing goes in whole afterwards
