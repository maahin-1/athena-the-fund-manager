from datetime import datetime, timedelta, timezone

import pytest

from athena.contracts import AthenaError
from athena.live.quotes import IST, LiveQuotes, Minute, market_is_open, parse_quote, staleness

BODY = {
    "equityResponse": [{
        "orderBook": {
            "buyPrice1": 946.8, "buyQuantity1": 5, "buyPrice2": 946.7, "buyQuantity2": 20, "buyPrice3": 0, "buyQuantity3": 0,
            "sellPrice1": 946.85, "sellQuantity1": 98, "sellPrice2": 946.95, "sellQuantity2": 128,
        },
        "metaData": {"symbol": "SBIN", "open": 941, "dayHigh": 949.4, "dayLow": 938.6, "previousClose": 941, "change": 5.85},
        "tradeInfo": {"totalTradedVolume": 5025824, "totalTradedValue": 4747041542.72, "lastPrice": 946.85, "deliveryToTradedQuantity": 59.7},
        "priceInfo": {"yearHigh": 1234.7, "yearLow": 872.1, "priceBand": "846.90-1035.10"},
        "lastUpdateTime": "13-Oct-2026 14:07:12",
    }]
}
OPEN_TIME = datetime(2026, 10, 13, 8, 40, tzinfo=timezone.utc)  # 14:10 in India, a Tuesday


class Reply:
    def __init__(self, body):
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def test_a_quote_is_read_with_change_depth_and_the_exchanges_own_time():
    quote = parse_quote("SBIN", BODY)
    assert quote.last == 946.85 and quote.previous_close == 941
    assert quote.change == pytest.approx(5.85) and quote.change_pct == pytest.approx(0.6216, abs=1e-3)
    assert quote.bids == ((946.8, 5), (946.7, 20)) and quote.asks == ((946.85, 98), (946.95, 128))  # an empty level is left out
    assert quote.updated == datetime(2026, 10, 13, 14, 7, 12, tzinfo=IST)
    assert quote.year_high == 1234.7 and quote.price_band == "846.90-1035.10" and quote.delivery_pct == 59.7


def test_a_quote_without_a_price_or_an_answer_is_an_error_not_a_zero():
    with pytest.raises(AthenaError):
        parse_quote("X", {"equityResponse": []})
    with pytest.raises(AthenaError):
        parse_quote("X", {"equityResponse": [{"metaData": {"previousClose": 10}, "tradeInfo": {}}]})
    with pytest.raises(AthenaError):
        parse_quote("X", {})


def test_the_market_is_open_on_weekday_trading_hours_in_india_only():
    assert market_is_open(OPEN_TIME)
    assert not market_is_open(datetime(2026, 10, 13, 11, 0, tzinfo=timezone.utc))  # 16:30 in India
    assert not market_is_open(datetime(2026, 10, 10, 8, 40, tzinfo=timezone.utc))  # a Saturday
    assert not market_is_open(datetime(2026, 10, 13, 3, 0, tzinfo=timezone.utc))  # 08:30 in India


def test_a_stale_update_is_called_out_while_open_and_a_closed_market_says_so():
    quote = parse_quote("SBIN", BODY)
    assert staleness(quote, OPEN_TIME) is None  # three minutes old
    assert "minutes old" in staleness(quote, OPEN_TIME + timedelta(minutes=10))
    assert "market closed" in staleness(quote, datetime(2026, 10, 13, 11, 0, tzinfo=timezone.utc))


def test_a_quote_and_the_minutes_are_remembered_briefly_and_fetched_again_after():
    calls = {"quote": 0, "minutes": 0}
    clock = {"now": OPEN_TIME}

    def get(url, params, headers, timeout):
        calls["quote"] += 1
        return Reply(BODY)

    def minutes(symbol):
        calls["minutes"] += 1
        return [Minute(OPEN_TIME.astimezone(IST), 946.0, 10.0)]

    quotes = LiveQuotes(get=get, minutes=minutes, clock=lambda: clock["now"], quote_ttl=0.5, minutes_ttl=15.0, background=False)
    quotes.quote("SBIN"), quotes.quote("SBIN"), quotes.minutes("SBIN"), quotes.minutes("SBIN")
    assert calls == {"quote": 1, "minutes": 1}
    clock["now"] += timedelta(seconds=1)
    quotes.quote("SBIN"), quotes.minutes("SBIN")
    assert calls == {"quote": 2, "minutes": 1}  # the quote is fetched again, the minute bars are not
    clock["now"] += timedelta(seconds=15)
    quotes.minutes("SBIN")
    assert calls["minutes"] == 2


def test_an_unreadable_answer_becomes_an_athena_error_with_no_detail_that_could_hold_a_secret():
    quotes = LiveQuotes(get=lambda *a, **k: Reply(ValueError("not json")), minutes=lambda s: [], clock=lambda: OPEN_TIME)
    with pytest.raises(AthenaError) as info:
        quotes.quote("NOPE")
    assert "could not be read" in str(info.value)


def test_a_stock_outside_the_main_series_is_found_by_trying_the_others_and_the_series_is_remembered():
    seen = []

    def get(url, params, headers, timeout):
        seen.append(params["series"])
        return Reply(BODY if params["series"] == "SM" else {"equityResponse": []})

    clock = {"now": OPEN_TIME}
    quotes = LiveQuotes(get=get, minutes=lambda s: [], clock=lambda: clock["now"], background=False)
    assert quotes.quote("SMEX").last == 946.85
    assert seen == ["EQ", "SM"]
    seen.clear()
    clock["now"] += timedelta(seconds=2)
    quotes.quote("SMEX")
    assert seen == ["SM"]  # the known series first, nothing else needed


def test_a_symbol_the_exchange_does_not_know_in_any_series_is_an_error_naming_what_was_tried():
    quotes = LiveQuotes(get=lambda *a, **k: Reply({"equityResponse": []}), minutes=lambda s: [], clock=lambda: OPEN_TIME, background=False)
    with pytest.raises(AthenaError) as info:
        quotes.quote("GHOST")
    assert "GHOST" in str(info.value) and "EQ" in str(info.value) and "ST" in str(info.value)


def test_every_new_exchange_answer_is_kept_as_a_tick_and_a_repeat_is_not():
    answers = [BODY, BODY, {"equityResponse": [{**BODY["equityResponse"][0], "tradeInfo": {**BODY["equityResponse"][0]["tradeInfo"], "lastPrice": 947.1},
                                               "lastUpdateTime": "13-Oct-2026 14:07:20"}]}]
    clock = {"now": OPEN_TIME}
    calls = iter(answers)
    quotes = LiveQuotes(get=lambda *a, **k: Reply(next(calls)), minutes=lambda s: [], clock=lambda: clock["now"], background=False)
    for _ in range(3):
        quotes.quote("SBIN")
        clock["now"] += timedelta(seconds=1)
    assert [price for _, price in quotes.ticks("SBIN")] == [946.85, 947.1]


def test_minute_bars_come_back_at_once_after_the_first_load_and_a_failing_source_is_an_error_not_a_hang():
    loads = []

    def minutes(symbol):
        loads.append(symbol)
        return [Minute(OPEN_TIME.astimezone(IST), 946.0, 10.0)]

    clock = {"now": OPEN_TIME}
    quotes = LiveQuotes(get=lambda *a, **k: Reply(BODY), minutes=minutes, clock=lambda: clock["now"], background=False)
    assert len(quotes.minutes("SBIN")) == 1 and len(quotes.minutes("SBIN")) == 1 and loads == ["SBIN"]

    def failing(symbol):
        raise AthenaError("Yahoo has no minute bars")

    broken = LiveQuotes(get=lambda *a, **k: Reply(BODY), minutes=failing, clock=lambda: OPEN_TIME, background=False)
    with pytest.raises(AthenaError):
        broken.minutes("SME1")
