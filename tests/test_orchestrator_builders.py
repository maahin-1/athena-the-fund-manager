from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from athena.contracts import Bar
from athena.orchestrator.builders import HISTORY_DAYS, RequestCache, history_fetcher, technical_packet_builder
from athena.resolver import Resolution

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)


def bars(count=80):
    days, day = [], date(2026, 10, 2)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return [
        Bar("SBIN", datetime(d.year, d.month, d.day, 18, 30, tzinfo=timezone.utc) - timedelta(days=1),
            100 + i, 101 + i, 99 + i, 100 + i, 1000.0, NOW, "t")
        for i, d in enumerate(reversed(days))
    ]


class FakeChain:
    def __init__(self, value):
        self.value, self.calls = value, []

    def run(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return SimpleNamespace(value=self.value, source="fake")


def test_history_fetcher_asks_the_chain_for_the_last_760_days_in_ist_dates():
    chain = FakeChain(bars())
    assert history_fetcher(chain, clock=lambda: NOW)("SBIN") == chain.value
    symbol, kwargs = chain.calls[0]
    assert symbol == "SBIN" and kwargs == {"since": date(2026, 10, 5) - timedelta(days=HISTORY_DAYS)}
    assert HISTORY_DAYS == 760


def test_history_fetcher_honours_a_custom_window():
    chain = FakeChain(bars())
    history_fetcher(chain, days=30, clock=lambda: NOW)("SBIN")
    assert chain.calls[0][1]["since"] == date(2026, 9, 5)


def test_technical_packet_builder_builds_a_packet_for_the_resolved_symbol():
    resolution = Resolution("equity", "ticker", "SBIN", "State Bank of India", "", "exact", 1.0, (), ())
    fetched = []
    build = technical_packet_builder(lambda symbol: fetched.append(symbol) or bars(), clock=lambda: NOW)
    packet = build(resolution)
    assert fetched == ["SBIN"] and packet["instrument"] == "SBIN" and packet["as_of"] == NOW.isoformat()
    assert packet["metrics"]["last_close"]["value"] == 179.0


def test_request_cache_downloads_each_symbol_once_until_cleared():
    fetched = []

    def fetch(symbol):
        fetched.append(symbol)
        return bars()

    cache = RequestCache(fetch)
    cache("SBIN"), cache("SBIN"), cache("TCS")
    assert fetched == ["SBIN", "TCS"]
    cache.clear()
    cache("SBIN")
    assert fetched == ["SBIN", "TCS", "SBIN"]
