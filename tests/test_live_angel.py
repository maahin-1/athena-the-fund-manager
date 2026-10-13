from datetime import datetime, timedelta, timezone

import pytest
from test_live_quotes import BODY, OPEN_TIME, Reply

from athena.brokers.angel_login import Session
from athena.contracts import AthenaError
from athena.live.angel import FRESH_SECONDS, AngelFeed, Instruments, quote_from_tick
from athena.live.quotes import IST, LiveQuotes

TICK = {
    "subscription_mode": 3, "exchange_type": 1, "token": "3045", "exchange_timestamp": 1791887852000, "last_traded_price": 95700,
    "last_traded_quantity": 22, "average_traded_price": 94682, "volume_trade_for_the_day": 7120906, "open_price_of_the_day": 94100,
    "high_price_of_the_day": 95700, "low_price_of_the_day": 93860, "closed_price": 94100, "upper_circuit_limit": 98200, "lower_circuit_limit": 92480,
    "52_week_high_price": 123470, "52_week_low_price": 87210,
    "best_5_buy_data": [{"flag": 1, "quantity": 963, "price": 95700, "no of orders": 1}, {"flag": 1, "quantity": 0, "price": 0, "no of orders": 0}],
    "best_5_sell_data": [{"flag": 0, "quantity": 120, "price": 95705, "no of orders": 2}],
}
ROWS = [{"token": "3045", "symbol": "SBIN-EQ"}, {"token": "9", "symbol": "BEONLY-BE"}, {"token": "8", "symbol": "SMEX-SM"}]


class FakeSocket:
    def __init__(self, jwt, api_key, client, feed):
        self.args = (jwt, api_key, client, feed)
        self.subscribed, self.unsubscribed = [], []
        self.on_open = self.on_data = self.on_error = None

    def connect(self):
        self.on_open(self)

    def subscribe(self, correlation, mode, tokens):
        self.subscribed.append((mode, tokens[0]["tokens"][0]))

    def unsubscribe(self, correlation, mode, tokens):
        self.unsubscribed.append(tokens[0]["tokens"][0])


def fake_login(*args):
    return Session("key", "A1", "jwt", "feed")


def make_feed(clock, sockets=None):
    created = sockets if sockets is not None else []

    def factory(*args):
        created.append(FakeSocket(*args))
        return created[-1]

    creds = lambda: {"ANGEL_API_KEY": "key", "ANGEL_CLIENT_CODE": "A1", "ANGEL_PIN": "0000", "ANGEL_TOTP_SECRET": "x"}
    feed = AngelFeed(creds, Instruments(lambda: ROWS), factory, fake_login, clock=lambda: clock["t"], threaded=False)
    return feed, created


def test_a_tick_becomes_a_quote_with_prices_in_rupees_depth_and_the_exchange_time():
    quote = quote_from_tick("SBIN", TICK)
    assert quote.last == 957.0 and quote.previous_close == 941.0 and quote.open == 941.0 and quote.high == 957.0 and quote.low == 938.6
    assert quote.change == pytest.approx(16.0) and quote.volume == 7120906 and quote.value == pytest.approx(946.82 * 7120906)
    assert quote.bids == ((957.0, 963.0),) and quote.asks == ((957.05, 120.0),)  # the empty level is left out
    assert quote.year_high == 1234.7 and quote.price_band == "924.80-982.00" and quote.source == "Angel One live stream"
    assert quote.updated.tzinfo == IST and quote.updated.year == 2026


def test_a_tick_with_no_price_or_no_previous_close_gives_no_quote():
    assert quote_from_tick("X", {**TICK, "last_traded_price": 0}) is None
    assert quote_from_tick("X", {**TICK, "closed_price": 0}) is None
    assert quote_from_tick("X", {}) is None


def test_the_instrument_list_finds_the_cash_series_in_order_and_knows_when_there_is_none():
    instruments = Instruments(lambda: ROWS)
    assert instruments.token("SBIN") == "3045" and instruments.token("BEONLY") == "9" and instruments.token("SMEX") == "8"
    assert instruments.token("GHOST") is None


def test_the_feed_logs_in_subscribes_the_watched_symbol_and_serves_its_ticks():
    clock = {"t": 1000.0}
    feed, sockets = make_feed(clock)
    feed.watch("SBIN")
    ws = sockets[0]
    assert ws.args == ("jwt", "key", "A1", "feed") and ws.subscribed == [(3, "3045")]
    assert feed.latest("SBIN") is None  # nothing has arrived yet
    ws.on_data(ws, TICK)
    assert feed.latest("SBIN").last == 957.0
    ws.on_data(ws, {**TICK, "last_traded_price": 95750, "exchange_timestamp": 1791887853000})
    assert [price for _, price in feed.ticks("SBIN")] == [957.0, 957.5]
    ws.on_data(ws, {**TICK, "last_traded_price": 95750, "exchange_timestamp": 1791887853000})  # a repeat is not a new tick
    assert len(feed.ticks("SBIN")) == 2 and feed.status() == "stream on"


def test_an_old_tick_is_not_served_and_a_tick_for_an_unwatched_token_is_ignored():
    clock = {"t": 1000.0}
    feed, sockets = make_feed(clock)
    feed.watch("SBIN")
    ws = sockets[0]
    ws.on_data(ws, {**TICK, "token": "777"})
    assert feed.latest("SBIN") is None
    ws.on_data(ws, TICK)
    clock["t"] += FRESH_SECONDS + 1
    assert feed.latest("SBIN") is None


def test_a_fourth_watched_symbol_drops_the_oldest_and_a_second_symbol_is_subscribed_on_the_open_socket():
    clock = {"t": 1000.0}
    feed, sockets = make_feed(clock)
    for symbol in ("SBIN", "BEONLY", "SMEX"):
        feed.watch(symbol)
    assert [t for _, t in sockets[0].subscribed] == ["3045", "9", "8"] and len(sockets) == 1
    feed.watch("SBIN")  # watching again only refreshes its place
    assert len(sockets[0].subscribed) == 3
    feed.watch("GHOST")
    feed.watch("SBIN")
    assert feed.latest("GHOST") is None


def test_a_refused_login_leaves_no_quote_a_readable_status_and_no_secret_in_it():
    def refusing(*args):
        raise AthenaError("Angel One refused the login: Invalid totp")

    creds = lambda: {"ANGEL_API_KEY": "key", "ANGEL_CLIENT_CODE": "A1", "ANGEL_PIN": "9999", "ANGEL_TOTP_SECRET": "sekret"}
    clock = {"t": 1000.0}
    feed = AngelFeed(creds, Instruments(lambda: ROWS), FakeSocket, refusing, clock=lambda: clock["t"], threaded=False)
    feed.watch("SBIN")
    assert feed.latest("SBIN") is None and "Invalid totp" in feed.status() and "9999" not in feed.status() and "sekret" not in feed.status()
    clock["t"] += 61  # a later watch tries again
    feed.watch("SBIN")
    assert feed.failure is not None  # still refused, tried again


def test_without_the_settings_there_is_no_feed(tmp_path):
    empty = tmp_path / "settings"
    empty.write_text("ANGEL_API_KEY=k\n", encoding="utf-8")
    assert AngelFeed.from_settings(empty) is None


def test_the_quote_source_uses_a_fresh_stream_quote_and_falls_back_to_polling_when_there_is_none_or_it_breaks():
    class Stream:
        def __init__(self, quote):
            self.quote, self.watched = quote, []

        def watch(self, symbol):
            self.watched.append(symbol)

        def latest(self, symbol):
            if self.quote == "boom":
                raise RuntimeError("socket gone")
            return self.quote

        def ticks(self, symbol):
            return [(datetime(2026, 10, 13, 14, 0, tzinfo=IST), 950.0)]

        def status(self):
            return "stream on"

    polled = []
    get = lambda url, params, headers, timeout: (polled.append(1), Reply(BODY))[1]
    streamed = quote_from_tick("SBIN", TICK)
    quotes = LiveQuotes(get=get, minutes=lambda s: [], clock=lambda: OPEN_TIME, feed=Stream(streamed), background=False)
    assert quotes.quote("SBIN").source == "Angel One live stream" and polled == []
    assert quotes.stream_status() == "stream on" and (datetime(2026, 10, 13, 14, 0, tzinfo=IST), 950.0) in quotes.ticks("SBIN")
    for quiet in (None, "boom"):
        fallback = LiveQuotes(get=get, minutes=lambda s: [], clock=lambda: OPEN_TIME, feed=Stream(quiet), background=False)
        assert fallback.quote("SBIN").source == "exchange quote page, polled"
    assert polled == [1, 1]
