import pytest
from bar_factory import make_bars
from fund_fixtures import ACME, NOW, PRICE, index_history

from athena.contracts import EmptyRefreshError
from athena.loaders.fundamentals import FundamentalsLoader
from athena.orchestrator.fundamentals_source import LiveFundamentals
from athena.resolver import Resolution
from athena.store import DataStore

RESOLUTION = Resolution("equity", "ticker", "ACME", "Acme Ltd", "", "exact", 1.0, (), ())


class Counting:
    def __init__(self, payload):
        self.fetches, self.payload = [], payload

    def __call__(self, symbol):
        self.fetches.append(symbol)
        return self.payload


def make(payload=ACME):
    statements, bar_calls = Counting(payload), []

    def fetch_bars(symbol):
        bar_calls.append(symbol)
        return make_bars([PRICE - 1, PRICE])

    loader = FundamentalsLoader(DataStore(), fetch=statements, clock=lambda: NOW)
    source = LiveFundamentals(loader, index_history(), fetch_bars, clock=lambda: NOW)
    return source, statements, bar_calls


def test_the_packet_is_built_from_the_statements_the_last_close_and_the_index_history():
    source, statements, _ = make()
    packet = source(RESOLUTION)
    assert packet["instrument"] == "ACME" and packet["metrics"]["last_price"]["value"] == PRICE
    assert packet["metrics"]["index_pe"]["value"] == 19.3
    assert packet["metrics"]["pe_trailing"]["value"] == pytest.approx(20.0, abs=1e-3)
    assert statements.fetches == ["ACME"]


def test_each_symbol_is_fetched_once_until_the_request_is_cleared():
    source, statements, bar_calls = make()
    first, second = source(RESOLUTION), source(RESOLUTION)  # three specialists would each call it
    assert first is second and statements.fetches == ["ACME"] and bar_calls == ["ACME"]
    source.clear()
    source(RESOLUTION)
    assert statements.fetches == ["ACME", "ACME"]


def test_a_source_with_no_statements_fails_loud_and_does_not_cache_the_failure():
    source, statements, _ = make({"annual": {"income": {}}})
    for _ in range(2):
        with pytest.raises(EmptyRefreshError):
            source(RESOLUTION)
    assert statements.fetches == ["ACME", "ACME"]


# ---- merger and scheme filings (Phase 1j-d)

from datetime import datetime, timedelta, timezone  # noqa: E402

from athena.contracts import AthenaError  # noqa: E402
from athena.news.model import NewsItem  # noqa: E402


def with_filings(filings):
    statements = Counting(ACME)
    loader = FundamentalsLoader(DataStore(), fetch=statements, clock=lambda: NOW)
    return LiveFundamentals(loader, index_history(), lambda symbol: make_bars([PRICE - 1, PRICE]), clock=lambda: NOW, filings=filings)


def test_a_filing_that_says_a_scheme_took_effect_becomes_a_data_quality_flag_and_the_window_is_the_statements_four_years():
    asked = []
    when = datetime(2024, 2, 1, 10, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    def filings(symbol, since, until):
        asked.append((symbol, (until - since).days))
        return [NewsItem("Effective Date of the Scheme of Amalgamation amongst Acme Ltd and Beta Ltd", "NSE", when, "", "nse")]

    flags = with_filings(filings)(RESOLUTION)["data_quality_flags"]
    assert asked == [("ACME", 1460)] and len(flags) == 1 and "taking effect (2024-02-01)" in flags[0] and "not organic" in flags[0]


def test_no_scheme_filing_adds_no_flag_and_an_unreachable_source_says_so_without_failing_the_packet():
    assert with_filings(lambda *a: [])(RESOLUTION)["data_quality_flags"] == []

    def down(*a):
        raise AthenaError("NSE announcements unavailable for ACME: HTTP 403")

    packet = with_filings(down)(RESOLUTION)
    assert "mergers and schemes of arrangement could not be checked" in packet["data_quality_flags"][0] and "HTTP 403" in packet["data_quality_flags"][0]
    assert packet["metrics"]["pe_trailing"]["value"] > 0


def test_without_a_filings_source_nothing_is_added():
    assert make()[0](RESOLUTION)["data_quality_flags"] == []
