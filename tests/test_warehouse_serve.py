from datetime import date, datetime, timedelta, timezone

from athena.contracts import AthenaError
from athena.pit.bhavcopy import Row
from athena.warehouse.serve import WarehouseFirst
from athena.warehouse.warehouse import Warehouse

CLOCK = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)  # 15:30 in India, Monday 12 October
LOADED_AT = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
DAYS = 100
PAYLOAD = {"income": {"x": 1}}


class Live:
    def __init__(self):
        self.calls = []

    def bars(self, symbol):
        self.calls.append(("bars", symbol))
        return ["live-bar"]

    def events(self, symbol):
        self.calls.append(("events", symbol))
        return ["live-event"]

    def actions(self, symbol):
        self.calls.append(("actions", symbol))
        return ["live-action"]


def fill(path, last=date(2026, 10, 9), start=date(2026, 5, 1), symbols=("ACME",)):
    with Warehouse(path) as w:
        day = start
        while day <= last:
            if day.weekday() < 5:
                w.upsert_day(day, {s: Row(s, "EQ", 100.0, 101.0, 99.0, 100.0, 1000) for s in symbols}, LOADED_AT)
            day += timedelta(days=1)
        w.put_statement("ACME", "ok", PAYLOAD, "", CLOCK - timedelta(days=3))


def served(path, live=None):
    live = live or Live()
    return WarehouseFirst(path, live.bars, live.events, live.actions, DAYS, lambda: CLOCK, wait_seconds=0), live


def test_a_recent_warehouse_serves_the_bars_and_the_exchanges_actions_without_asking_the_live_sources(tmp_path):
    path = tmp_path / "w.duckdb"
    fill(path)
    source, live = served(path)
    bars = source.bars("ACME")
    assert len(bars) > 50 and bars[-1].close == 100.0
    assert source.events("ACME") == () and source.actions("ACME") == []
    assert live.calls == []
    assert source.notes("ACME") == ("prices from the market database, last close 2026-10-09",)


def test_a_warehouse_a_week_behind_hands_over_to_the_live_sources(tmp_path):
    path = tmp_path / "w.duckdb"
    fill(path, last=date(2026, 10, 2))
    source, live = served(path)
    assert source.bars("ACME") == ["live-bar"]
    assert source.events("ACME") == ["live-event"] and source.actions("ACME") == ["live-action"]
    assert source.notes("ACME") == ()


def test_a_warehouse_that_starts_after_the_window_hands_over(tmp_path):
    path = tmp_path / "w.duckdb"
    fill(path, start=date(2026, 9, 1))
    source, live = served(path)
    assert source.bars("ACME") == ["live-bar"]


def test_a_symbol_the_warehouse_does_not_hold_or_that_stopped_trading_hands_over(tmp_path):
    path = tmp_path / "w.duckdb"
    fill(path)
    source, live = served(path)
    assert source.bars("OTHER") == ["live-bar"]
    with Warehouse(path) as w:
        w.upsert_day(date(2026, 9, 1), {"STOPPED": Row("STOPPED", "EQ", 1.0, 1.0, 1.0, 1.0, 1)}, LOADED_AT)
    assert source.bars("STOPPED") == ["live-bar"]


def test_a_missing_or_unreadable_warehouse_is_not_an_error(tmp_path):
    source, live = served(tmp_path / "nothing.duckdb")
    assert source.bars("ACME") == ["live-bar"] and source.statement("ACME") is None
    (tmp_path / "bad.duckdb").write_text("not a database")
    source, live = served(tmp_path / "bad.duckdb")
    assert source.bars("ACME") == ["live-bar"]


def test_statements_are_used_only_while_recent_and_say_when_they_were_fetched(tmp_path):
    path = tmp_path / "w.duckdb"
    fill(path)
    source, _ = served(path)
    payload, fetched = source.statement("ACME")
    assert payload == PAYLOAD and fetched == CLOCK - timedelta(days=3)
    assert source.notes("ACME") == ("statements fetched 2026-10-09",)
    with Warehouse(path) as w:
        w.put_statement("ACME", "ok", PAYLOAD, "", CLOCK - timedelta(days=45))
    assert served(path)[0].statement("ACME") is None
    assert source.statement("MISSING") is None


def test_an_error_from_a_live_source_still_reaches_the_caller(tmp_path):
    live = Live()

    def failing(symbol):
        raise AthenaError("down")

    source = WarehouseFirst(tmp_path / "none.duckdb", failing, live.events, live.actions, DAYS, lambda: CLOCK)
    try:
        source.bars("ACME")
    except AthenaError as exc:
        assert "down" in str(exc)
    else:
        raise AssertionError("expected the live error")
