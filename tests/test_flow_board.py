import io
import json
from contextlib import redirect_stdout
from datetime import date, timedelta

from athena import terminal_helper
from athena.derivs.store import DerivsStore
from athena.flow import board


def fill(store, days=70, stale_symbol="OLD"):
    """ABC (a stock) and NIFTY (an index) on every day; OLD stops one day early; ABC jumps in price and open interest on the last day."""
    expiry = date(2030, 1, 30)
    all_days = [date(2026, 1, 1) + timedelta(days=i) for i in range(days)]
    for i, day in enumerate(all_days):
        wobble = ((i * 7) % 5 - 2) / 1000
        rows = []
        for symbol, instrument, last in (("ABC", "FUTSTK", days - 1), ("NIFTY", "FUTIDX", days - 1), (stale_symbol, "FUTSTK", days - 2)):
            if i > last:
                continue
            close, oi = 100.0 * (1 + wobble), 1000.0 * (1 + wobble * 2)
            if symbol == "ABC" and i == days - 1:
                close, oi = 100.0 * 1.03, 1000.0 * 1.06
            rows.append((symbol, instrument, expiry, close, close, 10.0 + i, oi, 0.0))
        store.put_day(day, {"futures": rows, "totals": [], "strikes": []})
    return all_days


def test_the_board_lists_the_underlyings_with_futures_on_the_latest_day_with_their_changes_and_events(tmp_path):
    with DerivsStore(tmp_path / "d.duckdb") as store:
        days = fill(store)
        doc = board.board(store)
    assert doc["day"] == days[-1].isoformat() and doc["universe"] == 2
    by = {r["symbol"]: r for r in doc["rows"]}
    assert set(by) == {"ABC", "NIFTY"}  # OLD left the futures market a day earlier
    assert by["ABC"]["kind"] == "stock" and by["NIFTY"]["kind"] == "index"
    assert by["ABC"]["price_change"] > 0.025 and by["ABC"]["oi_change"] > 0.05
    assert [e["kind"] for e in by["ABC"]["events"] if e["kind"] != "oi_spike"] == ["long_build_up"]
    assert by["NIFTY"]["events"] == [] and by["NIFTY"]["put_call"] is None  # no option rows were loaded
    assert doc["rows"][0]["symbol"] == "ABC"  # the stock with events comes first
    assert doc["events"]["long_build_up"] == 1


def test_an_empty_or_missing_store_gives_an_empty_board_or_an_error_not_a_crash(tmp_path):
    with DerivsStore(tmp_path / "empty.duckdb") as store:
        assert board.board(store) == {"day": None, "rows": [], "events": {}, "universe": 0}
    out = io.StringIO()
    with redirect_stdout(out):
        assert board.main(["--store", str(tmp_path / "nothing.duckdb")]) == 0
    assert json.loads(out.getvalue()) == {"error": "no derivatives history is stored"}


def test_the_command_prints_one_json_document_and_the_helper_serves_it(tmp_path):
    with DerivsStore(tmp_path / "d.duckdb") as store:
        fill(store)
    out = io.StringIO()
    with redirect_stdout(out):
        assert board.main(["--store", str(tmp_path / "d.duckdb")]) == 0
    assert json.loads(out.getvalue())["universe"] == 2
    ops = terminal_helper.operations()
    assert "flow_board" in ops
    reply = terminal_helper.handle(ops, {"id": 7, "op": "flow_board", "args": ["--store", str(tmp_path / "d.duckdb")]})
    assert reply["ok"] and json.loads(reply["out"])["day"] is not None
