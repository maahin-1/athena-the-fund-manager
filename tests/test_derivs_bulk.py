from datetime import date, timedelta

import pytest

from athena.derivs.store import DerivsStore
from athena.flow import board as board_module


def load(store, days=40):
    """Two symbols with two expiries held each day, one stopping early."""
    first = date(2026, 1, 1)
    expiry_a, expiry_b = date(2026, 3, 31), date(2026, 4, 28)
    for n in range(days):
        day = first + timedelta(days=n)
        rows = []
        for symbol in ("AAA", "BBB"):
            if symbol == "BBB" and n > days - 3:
                continue
            for e, base in ((expiry_a, 100.0), (expiry_b, 101.0)):
                rows.append((symbol, "FUTSTK", e, base + n * 0.5 + (3 if symbol == "BBB" else 0), 0.0, 1000.0 + n, 5000.0 + 10 * n, 0.0))
        strikes = [("AAA", expiry_a, 100.0 + k * 5, opt, 100.0 + n + k) for k in range(3) for opt in ("CE", "PE")]
        store._con.execute("INSERT OR REPLACE INTO derivs_days VALUES (?, TRUE, ?, 0, ?)", [day, len(rows), len(strikes)])
        store._con.executemany("INSERT INTO fut_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [(day, *r) for r in rows])
        store._con.executemany("INSERT INTO opt_strikes VALUES (?, ?, ?, ?, ?, ?, ?)", [(day, s, e, k, o, oi, 1.0) for s, e, k, o, oi in strikes])
    return first + timedelta(days=days - 1)


def test_one_scan_gives_the_same_series_as_one_query_per_symbol_and_a_window_changes_nothing_that_matters():
    with DerivsStore(":memory:") as store:
        last = load(store)
        everything = store.futures_all("FUTSTK")
        assert set(everything) == {"AAA", "BBB"} == set(store.symbols("FUTSTK"))
        for symbol, series in everything.items():
            assert series == store.futures(symbol, "FUTSTK")
        windowed = store.futures_all("FUTSTK", last - timedelta(days=15))
        assert len(windowed["AAA"]) == 16 and len(everything["AAA"]) == 40
        for (day_a, close_a, oi_a, vol_a), (day_b, close_b, oi_b, vol_b) in zip(everything["AAA"][-16:], windowed["AAA"]):
            assert day_a == day_b and close_a == pytest.approx(close_b, rel=1e-12) and oi_a == pytest.approx(oi_b, rel=1e-12) and vol_a == vol_b


def test_the_option_rows_for_all_symbols_match_the_per_symbol_query():
    with DerivsStore(":memory:") as store:
        last = load(store)
        since = last - timedelta(days=10)
        allrows = store.option_rows_all(since)
        assert set(allrows) == {"AAA"} and allrows["AAA"] == store.option_rows("AAA", since)


def test_the_board_built_from_the_bulk_scans_names_only_symbols_whose_series_reaches_the_latest_day():
    with DerivsStore(":memory:") as store:
        load(store)
        doc = board_module.board(store)
        assert doc["day"] == "2026-02-09" and [r["symbol"] for r in doc["rows"]] == ["AAA"]  # BBB's file stopped two days early
