import json
from datetime import datetime, timedelta

import pytest

from athena.brokers.live import angelone
from athena.brokers.live.angelone import AngelOneBroker, cached_scrip_master, parse_time
from athena.brokers.live.base import BrokerApiError
from athena.brokers.model import OrderRequest

SETTINGS = {"ANGEL_API_KEY": "KEY123", "ANGEL_PUBLIC_IP": "203.0.113.5", "ANGEL_MAC_ADDRESS": "aa:bb:cc:dd:ee:ff"}
SCRIPS = [
    {"token": "3045", "symbol": "SBIN-EQ", "exch_seg": "NSE", "instrumenttype": ""},
    {"token": "500112", "symbol": "SBIN", "exch_seg": "BSE", "instrumenttype": ""},
    {"token": "999", "symbol": "ONLYBE-BE", "exch_seg": "NSE", "instrumenttype": ""},
]


class Fake:
    """transport(method, url, headers=, params=, json=, data=) -> (status, body); answers by URL tail, records calls."""

    def __init__(self, **by_path):
        self.by_path, self.calls = by_path, []

    def __call__(self, method, url, headers=None, params=None, json=None, data=None):
        self.calls.append((method, url, headers, json))
        reply = self.by_path[url.rsplit("/", 1)[1]]
        return (200, reply() if callable(reply) else reply)


def ok(data):
    return {"status": True, "message": "SUCCESS", "errorcode": "", "data": data}


def row(**kw):
    base = {"variety": "NORMAL", "ordertype": "LIMIT", "producttype": "DELIVERY", "duration": "DAY", "price": 101.0, "quantity": "5", "tradingsymbol": "SBIN-EQ", "transactiontype": "BUY",
            "exchange": "NSE", "averageprice": 0.0, "filledshares": "0", "orderid": "2501", "text": "", "orderstatus": "open", "updatetime": "26-Oct-2026 10:05:07", "ordertag": "c1"}
    base.update(kw)
    return base


def make(tmp_path, transport, **kw):
    return AngelOneBroker(SETTINGS, transport, tmp_path, jwt_token="JWT", scrip_loader=lambda: SCRIPS, **kw)


def req(**kw):
    args = dict(symbol="SBIN", side="BUY", quantity=5, limit_price=101.0)
    args.update(kw)
    return OrderRequest(**args)


def test_funds_and_holdings(tmp_path):
    fake = Fake(getRMS=ok({"availablecash": "12345.50"}), getAllHolding=ok({"holdings": [{"tradingsymbol": "SBIN-EQ", "exchange": "NSE", "quantity": 10, "averageprice": 600.5}], "totalholding": {}}))
    b = make(tmp_path, fake)
    assert b.funds().available == 12345.5
    h = b.holdings()[0]
    assert (h.symbol, h.exchange, h.quantity, h.average_price) == ("SBIN", "NSE", 10, 600.5)
    assert "getRMS" in fake.calls[0][1] and fake.calls[0][0] == "GET"


def test_funds_without_the_number_is_an_error(tmp_path):
    with pytest.raises(BrokerApiError):
        make(tmp_path, Fake(getRMS=ok({"net": "1"}))).funds()


@pytest.mark.parametrize("raw,mapped", [("open", "open"), ("OPEN PENDING", "open"), ("trigger pending", "open"), ("validation pending", "open"), ("complete", "filled"), ("cancelled", "cancelled"), ("rejected", "rejected")])
def test_order_status_mapping(tmp_path, raw, mapped):
    extra = dict(filledshares="5", averageprice=100.5) if mapped == "filled" else dict(text="RMS:Margin Exceeds") if mapped == "rejected" else {}
    [o] = make(tmp_path, Fake(getOrderBook=ok([row(orderstatus=raw, **extra)]))).orders()
    assert o.status == mapped and o.order_id == "2501" and o.client_order_id == "c1"
    assert (o.request.symbol, o.request.side, o.request.quantity, o.request.limit_price, o.request.exchange) == ("SBIN", "BUY", 5, 101.0, "NSE")
    assert o.placed_at == "2026-10-26T10:05:07+05:30"
    assert (o.filled_quantity, o.average_price) == ((5, 100.5) if mapped == "filled" else (0, 0.0))
    assert (o.message == "RMS:Margin Exceeds") == (mapped == "rejected")
    assert bool(o.filled_at) == (mapped == "filled")


def test_unknown_status_raises_and_is_never_filled(tmp_path):
    with pytest.raises(BrokerApiError, match="does not know"):
        make(tmp_path, Fake(getOrderBook=ok([row(orderstatus="executed ok")]))).orders()


def test_orders_athena_cannot_write_are_skipped_and_empty_book_is_empty(tmp_path):
    assert make(tmp_path, Fake(getOrderBook=ok([row(producttype="INTRADAY"), row(ordertype="MARKET", price=0)]))).orders() == []
    assert make(tmp_path, Fake(getOrderBook=ok(None))).orders() == []


def test_order_with_missing_field_is_an_error(tmp_path):
    bad = row()
    del bad["filledshares"]
    with pytest.raises(BrokerApiError):
        make(tmp_path, Fake(getOrderBook=ok([bad]))).orders()


def test_place_sends_the_documented_body_and_headers(tmp_path):
    fake = Fake(placeOrder=ok({"script": "SBIN-EQ", "orderid": "2501", "uniqueorderid": "u"}), getOrderBook=ok([row(orderid="2501")]))
    order = make(tmp_path, fake).place(req(), "c1")
    method, url, headers, body = fake.calls[0]
    assert (method, url) == ("POST", "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/placeOrder")
    assert body == {"variety": "NORMAL", "tradingsymbol": "SBIN-EQ", "symboltoken": "3045", "transactiontype": "BUY", "exchange": "NSE", "ordertype": "LIMIT", "producttype": "DELIVERY",
                    "duration": "DAY", "price": "101.00", "squareoff": "0", "stoploss": "0", "quantity": "5", "ordertag": "c1"}
    assert headers["Authorization"] == "Bearer JWT" and headers["X-PrivateKey"] == "KEY123"
    assert headers["X-UserType"] == "USER" and headers["X-SourceID"] == "WEB" and headers["Accept"] == "application/json" and headers["Content-Type"] == "application/json"
    assert headers["X-ClientPublicIP"] == "203.0.113.5" and headers["X-MACAddress"] == "aa:bb:cc:dd:ee:ff" and headers["X-ClientLocalIP"]
    assert order.order_id == "2501" and order.client_order_id == "c1"


def test_long_client_order_id_becomes_a_20_character_tag(tmp_path):
    fake = Fake(placeOrder=ok({"orderid": "7"}), getOrderBook=ok(None))
    make(tmp_path, fake).place(req(), "x" * 40)
    assert len(fake.calls[0][3]["ordertag"]) == 20


def test_bse_uses_the_bse_row(tmp_path):
    fake = Fake(placeOrder=ok({"orderid": "7"}), getOrderBook=ok(None))
    make(tmp_path, fake).place(req(exchange="BSE"), "c1")
    assert (fake.calls[0][3]["tradingsymbol"], fake.calls[0][3]["symboltoken"]) == ("SBIN", "500112")


def test_unknown_symbol_or_wrong_series_sends_nothing(tmp_path):
    fake = Fake(placeOrder=ok({"orderid": "7"}))
    b = make(tmp_path, fake)
    for symbol in ("NOSUCH", "ONLYBE"):
        with pytest.raises(BrokerApiError, match="not in Angel One"):
            b._place(req(symbol=symbol), "c1")
    assert fake.calls == []


def test_place_without_orderid_raises(tmp_path):
    for data in ({"script": "SBIN-EQ"}, None, {"orderid": ""}):
        with pytest.raises(BrokerApiError):
            make(tmp_path, Fake(placeOrder=ok(data)))._place(req(), "c1")


def test_status_false_body_is_an_error_with_the_message(tmp_path):
    fake = Fake(getRMS={"status": False, "message": "Invalid Token", "errorcode": "AG8001", "data": None})
    with pytest.raises(BrokerApiError, match="Invalid Token.*AG8001"):
        make(tmp_path, fake).funds()


def test_http_error_is_an_error(tmp_path):
    with pytest.raises(BrokerApiError):
        make(tmp_path, lambda *a, **k: (403, {"message": "forbidden"})).funds()


def test_duplicate_client_order_id_sends_nothing_more(tmp_path):
    fake = Fake(placeOrder=ok({"orderid": "2501"}), getOrderBook=ok([row()]))
    b = make(tmp_path, fake)
    b.place(req(), "c1")
    sends = lambda: [c for c in fake.calls if c[1].endswith("placeOrder")]  # noqa: E731
    assert len(sends()) == 1
    again = b.place(req(), "c1")
    assert len(sends()) == 1 and again.order_id == "2501"


def test_cancel_uses_the_order_books_variety(tmp_path):
    fake = Fake(getOrderBook=ok([row(variety="AMO")]), cancelOrder=ok({"orderid": "2501"}))
    make(tmp_path, fake).cancel("2501")
    cancels = [c for c in fake.calls if c[1].endswith("cancelOrder")]
    assert len(cancels) == 1 and cancels[0][3] == {"variety": "AMO", "orderid": "2501"}


def test_cancel_of_unknown_order_sends_nothing(tmp_path):
    fake = Fake(getOrderBook=ok([row()]), cancelOrder=ok({}))
    with pytest.raises(KeyError):
        make(tmp_path, fake).cancel("999")
    assert not [c for c in fake.calls if c[1].endswith("cancelOrder")]


def test_login_is_lazy_injectable_and_failure_is_an_error(tmp_path):
    fake = Fake(getRMS=ok({"availablecash": "1"}))
    b = AngelOneBroker(SETTINGS, fake, tmp_path, login_fn=lambda: "LOGGED")
    b.funds()
    assert fake.calls[0][2]["Authorization"] == "Bearer LOGGED"
    with pytest.raises(BrokerApiError):
        AngelOneBroker(SETTINGS, fake, tmp_path, login_fn=lambda: "").funds()


def test_parse_time_is_india_time_and_strict():
    assert parse_time("16-May-2024 11:18:11") == "2024-05-16T11:18:11+05:30"
    for bad in ("", None, "2024-05-16 11:18:11", "16-Foo-2024 11:18:11"):
        with pytest.raises(BrokerApiError):
            parse_time(bad)


def test_scrip_master_is_cached_per_day_and_filtered(tmp_path):
    cache, now, fetches = tmp_path / "m.json", [datetime(2026, 10, 26, 10, 0, tzinfo=angelone.IST)], []

    def fetch():
        fetches.append(1)
        return SCRIPS + [{"token": "1", "symbol": "NIFTY26OCTFUT", "exch_seg": "NFO", "instrumenttype": "FUTIDX"}]

    assert len(cached_scrip_master(cache, fetch, lambda: now[0])) == 3
    cached_scrip_master(cache, fetch, lambda: now[0])
    assert len(fetches) == 1
    now[0] += timedelta(days=1)
    cached_scrip_master(cache, fetch, lambda: now[0])
    assert len(fetches) == 2
    assert len(json.loads(cache.read_text())["rows"]) == 3  # filtered rows only


def test_after_market_statuses_map_to_open_and_unknown_on_a_delivery_limit_row_still_raises(tmp_path):
    for raw in ("after market order req received", "AMO SUBMITTED"):
        [o] = make(tmp_path, Fake(getOrderBook=ok([row(orderstatus=raw)]))).orders()
        assert o.status == "open"
    with pytest.raises(BrokerApiError, match="does not know"):
        make(tmp_path, Fake(getOrderBook=ok([row(orderstatus="brand new")]))).orders()


def test_unknown_status_on_a_hand_placed_row_is_skipped(tmp_path):
    assert make(tmp_path, Fake(getOrderBook=ok([row(producttype="INTRADAY", orderstatus="brand new")]))).orders() == []


def test_complete_with_a_different_filled_quantity_raises(tmp_path):
    with pytest.raises(BrokerApiError, match="filled quantity"):
        make(tmp_path, Fake(getOrderBook=ok([row(orderstatus="complete", filledshares="3", averageprice=100.5)]))).orders()
