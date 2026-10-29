import json
from datetime import date

import pytest

from athena.brokers.live.base import BrokerApiError, OutcomeUnknown
from athena.brokers.live.upstox import (CANCEL_URL, FUNDS_URL, HOLDINGS_URL, ORDERS_URL, PLACE_URL, STATUS_MAP, UpstoxBroker, authorize_url, exchange_auth_code)
from athena.brokers.model import OrderRequest

TOKEN = "tok-123-not-real"
INSTRUMENTS = [
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "INFY", "instrument_key": "NSE_EQ|INE009A01021", "isin": "INE009A01021", "exchange": "NSE", "name": "INFOSYS"},
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "TWIN", "instrument_key": "NSE_EQ|INE000000001", "isin": "INE000000001", "exchange": "NSE", "name": "A"},
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "TWIN", "instrument_key": "NSE_EQ|INE000000002", "isin": "INE000000002", "exchange": "NSE", "name": "B"},
    {"segment": "NSE_FO", "instrument_type": "FUT", "trading_symbol": "ONLYFO", "instrument_key": "NSE_FO|55555", "exchange": "NSE", "name": "F"},
    {"segment": "NSE_EQ", "instrument_type": "EQ", "trading_symbol": "BADKEY", "instrument_key": "NSE_EQ|55555", "exchange": "NSE", "name": "X"},
]


class Fake:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, headers=None, params=None, json=None, data=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "json": json, "data": data})
        reply = self.routes[(method, url)]
        return reply(self.calls[-1]) if callable(reply) else reply


class Loader:
    def __init__(self):
        self.calls = []

    def __call__(self, exchange):
        self.calls.append(exchange)
        return INSTRUMENTS


def make(tmp_path, routes, loader=None, **kw):
    transport = Fake(routes)
    broker = UpstoxBroker(settings={"UPSTOX_ACCESS_TOKEN": TOKEN}, transport=transport, ledger_dir=tmp_path, instruments_loader=loader or Loader(), today=lambda: date(2026, 10, 29), **kw)
    return broker, transport


def row(**over):
    base = {"order_id": "2310190001", "status": "open", "product": "D", "order_type": "LIMIT", "exchange": "NSE", "trading_symbol": "INFY", "transaction_type": "BUY", "quantity": 10,
            "price": 1500.5, "filled_quantity": 0, "average_price": 0, "tag": "c1", "order_timestamp": "2026-10-29 09:23:23", "exchange_timestamp": "2026-10-29 09:23:24", "status_message": None}
    base.update(over)
    return base


def book(*rows):
    return (200, {"status": "success", "data": list(rows)})


REQ = OrderRequest(symbol="INFY", side="BUY", quantity=10, limit_price=1500.5)


def test_funds_cash_is_available_plus_used_and_missing_numbers_raise(tmp_path):
    ok = (200, {"status": "success", "data": {"equity": {"available_margin": 9000.0, "used_margin": 1000.0}}})
    broker, t = make(tmp_path, {("GET", FUNDS_URL): ok})
    funds = broker.funds()
    assert (funds.cash, funds.reserved, funds.available) == (10000.0, 1000.0, 9000.0)
    assert t.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    broker, _ = make(tmp_path, {("GET", FUNDS_URL): (200, {"status": "success", "data": {"equity": {"available_margin": 9000.0}}})})
    with pytest.raises(BrokerApiError):
        broker.funds()


def test_holdings_map_and_a_bad_quantity_raises(tmp_path):
    good = (200, {"status": "success", "data": [{"trading_symbol": "INFY", "exchange": "NSE", "quantity": 7, "average_price": 1400.25, "isin": "INE009A01021"}]})
    broker, _ = make(tmp_path, {("GET", HOLDINGS_URL): good})
    h = broker.holdings()[0]
    assert (h.symbol, h.exchange, h.quantity, h.average_price) == ("INFY", "NSE", 7, 1400.25)
    bad = (200, {"status": "success", "data": [{"trading_symbol": "INFY", "exchange": "NSE", "quantity": 7.5, "average_price": 1}]})
    broker, _ = make(tmp_path, {("GET", HOLDINGS_URL): bad})
    with pytest.raises(BrokerApiError):
        broker.holdings()


@pytest.mark.parametrize("status,expected", sorted(STATUS_MAP.items()))
def test_every_documented_status_maps(tmp_path, status, expected):
    extra = {"filled_quantity": 10, "average_price": 1499.0} if expected == "filled" else {}
    broker, _ = make(tmp_path, {("GET", ORDERS_URL): book(row(status=status, status_message="no funds" if expected == "rejected" else None, **extra))})
    order = broker.orders()[0]
    assert order.status == expected
    assert order.placed_at == "2026-10-29T09:23:23+05:30"
    assert order.request.limit_price == 1500.5


def test_filled_carries_the_brokers_numbers_and_rejected_carries_the_reason(tmp_path):
    broker, _ = make(tmp_path, {("GET", ORDERS_URL): book(row(status="complete", filled_quantity=10, average_price=1499.9), row(order_id="2", status="rejected", status_message="RMS: margin"))})
    filled, rejected = broker.orders()
    assert (filled.filled_quantity, filled.average_price, filled.filled_at) == (10, 1499.9, "2026-10-29T09:23:24+05:30")
    assert rejected.message == "RMS: margin"


def test_complete_without_a_fill_is_an_error_and_an_unknown_status_raises(tmp_path):
    broker, _ = make(tmp_path, {("GET", ORDERS_URL): book(row(status="complete"))})
    with pytest.raises(BrokerApiError):
        broker.orders()
    broker, _ = make(tmp_path, {("GET", ORDERS_URL): book(row(status="partially executed maybe"))})
    with pytest.raises(BrokerApiError, match="does not know"):
        broker.orders()


def test_orders_outside_athenas_scope_are_left_out_and_a_bare_array_is_read(tmp_path):
    broker, _ = make(tmp_path, {("GET", ORDERS_URL): (200, [row(product="I"), row(order_id="3", order_type="MARKET"), row(order_id="4")])})
    assert [o.order_id for o in broker.orders()] == ["4"]


def test_place_sends_the_documented_v3_shape(tmp_path):
    routes = {("POST", PLACE_URL): (200, {"status": "success", "data": {"order_ids": ["999"]}, "metadata": {"latency": 30}}),
              ("GET", ORDERS_URL): book(row(order_id="999", tag="c1"))}
    broker, t = make(tmp_path, routes)
    order = broker.place(REQ, "c1")
    sent = t.calls[0]
    assert (sent["method"], sent["url"]) == ("POST", "https://api-hft.upstox.com/v3/order/place")
    assert sent["headers"] == {"Accept": "application/json", "Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    assert sent["json"] == {"quantity": 10, "product": "D", "validity": "DAY", "price": 1500.5, "tag": "c1", "instrument_token": "NSE_EQ|INE009A01021", "order_type": "LIMIT",
                            "transaction_type": "BUY", "disclosed_quantity": 0, "trigger_price": 0, "is_amo": False, "slice": False}
    assert (order.order_id, order.client_order_id, order.status) == ("999", "c1", "open")


def test_a_long_or_odd_client_id_becomes_a_40_char_tag():
    tag = UpstoxBroker.tag_for("x" * 80)
    assert len(tag) == 40 and tag == UpstoxBroker.tag_for("x" * 80)
    assert UpstoxBroker.tag_for("ath-0001") == "ath-0001"
    assert len(UpstoxBroker.tag_for("has space")) == 40


def test_a_second_place_with_the_same_client_id_sends_nothing(tmp_path):
    routes = {("POST", PLACE_URL): (200, {"status": "success", "data": {"order_ids": ["999"]}}), ("GET", ORDERS_URL): book(row(order_id="999"))}
    broker, t = make(tmp_path, routes)
    broker.place(REQ, "c1")
    posts = lambda: [c for c in t.calls if c["method"] == "POST"]  # noqa: E731
    assert len(posts()) == 1
    broker.place(REQ, "c1")
    assert len(posts()) == 1


def test_a_reply_without_an_order_id_raises_and_the_retry_says_outcome_unknown(tmp_path):
    routes = {("POST", PLACE_URL): (200, {"status": "success", "data": {"order_ids": []}})}
    broker, t = make(tmp_path, routes)
    with pytest.raises(BrokerApiError):
        broker.place(REQ, "c2")
    with pytest.raises(OutcomeUnknown):
        broker.place(REQ, "c2")
    assert len(t.calls) == 1


def test_an_error_body_raises_and_does_not_leak_the_token(tmp_path):
    routes = {("POST", PLACE_URL): (403, {"status": "error", "errors": [{"errorCode": "UDAPI1154", "message": f"static ip {TOKEN}"}]})}
    broker, _ = make(tmp_path, routes)
    with pytest.raises(BrokerApiError) as err:
        broker.place(REQ, "c3")
    assert err.value.status == 403 and "UDAPI1154" in str(err.value)


def test_an_unknown_ambiguous_or_non_equity_symbol_is_a_clear_error_and_sends_nothing(tmp_path):
    broker, t = make(tmp_path, {})
    for symbol, word in (("NOPE", "not an equity"), ("TWIN", "more than one"), ("ONLYFO", "not an equity"), ("BADKEY", "NSE_EQ|ISIN")):
        with pytest.raises(BrokerApiError, match=word):
            broker.place(OrderRequest(symbol=symbol, side="BUY", quantity=1, limit_price=10.0), f"id-{symbol}")
    assert t.calls == []


def test_the_instruments_are_cached_on_disk_for_the_day_and_refreshed_the_next(tmp_path):
    loader = Loader()
    broker, _ = make(tmp_path, {}, loader=loader)
    assert broker.instrument_key("INFY", "NSE") == "NSE_EQ|INE009A01021"
    broker.instrument_key("INFY", "NSE")
    again, _ = make(tmp_path, {}, loader=loader)
    again.instrument_key("INFY", "NSE")
    assert loader.calls == ["NSE"]
    cache = json.loads((tmp_path / "upstox-instruments-NSE.json").read_text(encoding="utf-8"))
    assert cache["date"] == "2026-10-29" and all(r["segment"] == "NSE_EQ" for r in cache["instruments"])
    tomorrow = UpstoxBroker(settings={"UPSTOX_ACCESS_TOKEN": TOKEN}, transport=Fake({}), ledger_dir=tmp_path, instruments_loader=loader, today=lambda: date(2026, 10, 30))
    tomorrow.instrument_key("INFY", "NSE")
    assert loader.calls == ["NSE", "NSE"]


def test_cancel_sends_a_delete_with_the_order_id_in_the_query(tmp_path):
    state = {"n": 0}

    def orders(_call):
        state["n"] += 1
        return book(row(order_id="999", status="open" if state["n"] == 1 else "cancelled"))

    routes = {("GET", ORDERS_URL): orders, ("DELETE", CANCEL_URL): (200, {"status": "success", "data": {"order_id": "999"}})}
    broker, t = make(tmp_path, routes)
    order = broker.cancel("999")
    sent = [c for c in t.calls if c["method"] == "DELETE"][0]
    assert sent["params"] == {"order_id": "999"} and sent["json"] is None and sent["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert order.status == "cancelled"


def test_cancel_refusal_raises(tmp_path):
    routes = {("GET", ORDERS_URL): book(row(order_id="999")), ("DELETE", CANCEL_URL): (400, {"status": "error", "errors": [{"errorCode": "UDAPI100010"}]})}
    broker, _ = make(tmp_path, routes)
    with pytest.raises(BrokerApiError):
        broker.cancel("999")


def test_the_login_helpers_build_the_url_and_exchange_the_code():
    url = authorize_url("KEY", "https://example.test/cb", "st8")
    assert url.startswith("https://api.upstox.com/v2/login/authorization/dialog?") and "response_type=code" in url and "client_id=KEY" in url
    seen = {}

    def transport(method, url, headers=None, params=None, json=None, data=None):
        seen.update(method=method, url=url, data=data, headers=headers)
        return 200, {"access_token": "newtok", "extended_token": None}

    assert exchange_auth_code("KEY", "SECRET", "CODE", "https://example.test/cb", transport) == "newtok"
    assert seen["data"] == {"code": "CODE", "client_id": "KEY", "client_secret": "SECRET", "redirect_uri": "https://example.test/cb", "grant_type": "authorization_code"}
    assert seen["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    with pytest.raises(BrokerApiError):
        exchange_auth_code("KEY", "SECRET", "CODE", "https://example.test/cb", lambda *a, **k: (401, {"errors": []}))


def test_hand_placed_orders_are_skipped_before_status_is_read(tmp_path):
    odd = [row(order_id="1", product="I", status="something new"), row(order_id="2", order_type="MARKET", price=0, status="something new"), row(order_id="3", exchange="NFO", status="something new")]
    broker, _ = make(tmp_path, {("GET", ORDERS_URL): book(*odd, row(order_id="4"))})
    assert [o.order_id for o in broker.orders()] == ["4"]


def test_complete_with_a_different_filled_quantity_is_an_error(tmp_path):
    broker, _ = make(tmp_path, {("GET", ORDERS_URL): book(row(status="complete", filled_quantity=4, average_price=1499.0))})
    with pytest.raises(BrokerApiError, match="filled quantity"):
        broker.orders()
