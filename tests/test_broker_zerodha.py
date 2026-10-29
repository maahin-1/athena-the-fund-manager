import pytest

from athena.brokers.live.base import BrokerApiError, OutcomeUnknown
from athena.brokers.live.zerodha import ZerodhaBroker, tag_for
from athena.brokers.model import OrderRequest

SETTINGS = {"KITE_API_KEY": "KEY123", "KITE_ACCESS_TOKEN": "TOK456"}


class Fake:
    """Transport that answers by (method, path) and records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers=None, params=None, json=None, data=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "data": data, "json": json})
        answer = self.routes[(method, url.removeprefix("https://api.kite.trade"))]
        return answer(data) if callable(answer) else answer


def order_row(**kw):
    row = {"order_id": "151220000000000", "status": "OPEN", "status_message": None, "order_timestamp": "2026-10-29 09:18:57", "exchange": "NSE", "tradingsymbol": "SBIN",
           "transaction_type": "BUY", "product": "CNC", "order_type": "LIMIT", "validity": "DAY", "quantity": 10, "filled_quantity": 0, "pending_quantity": 10, "price": 800.5,
           "average_price": 0, "tag": None, "variety": "regular"}
    row.update(kw)
    return row


def broker(tmp_path, routes):
    fake = Fake(routes)
    return ZerodhaBroker(SETTINGS, fake, tmp_path), fake


def orders_of(tmp_path, *rows):
    return broker(tmp_path, {("GET", "/orders"): (200, {"status": "success", "data": list(rows)})})[0].orders()


def test_funds(tmp_path):
    body = {"status": "success", "data": {"equity": {"enabled": True, "net": 99725.05, "available": {"cash": 245431.6, "live_balance": 99725.05}, "utilised": {"debits": 145706.55}}}}
    b, fake = broker(tmp_path, {("GET", "/user/margins/equity"): (200, {"status": "success", "data": body["data"]["equity"]})})
    funds = b.funds()
    assert funds.cash == 245431.6 and funds.reserved == 145706.55 and funds.available == pytest.approx(99725.05)
    call = fake.calls[0]
    assert call["headers"]["Authorization"] == "token KEY123:TOK456" and call["headers"]["X-Kite-Version"] == "3"


def test_funds_missing_field_is_error(tmp_path):
    b, _ = broker(tmp_path, {("GET", "/user/margins/equity"): (200, {"status": "success", "data": {"enabled": True, "available": {"cash": 5}, "utilised": {}}})})
    with pytest.raises(BrokerApiError):
        b.funds()


def test_holdings_add_t1_quantity(tmp_path):
    rows = [{"tradingsymbol": "INFY", "exchange": "NSE", "quantity": 4, "t1_quantity": 2, "average_price": 1500.25},
            {"tradingsymbol": "GONE", "exchange": "NSE", "quantity": 0, "t1_quantity": 0, "average_price": 1}]
    b, _ = broker(tmp_path, {("GET", "/portfolio/holdings"): (200, {"status": "success", "data": rows})})
    holdings = b.holdings()
    assert len(holdings) == 1 and (holdings[0].symbol, holdings[0].quantity, holdings[0].average_price) == ("INFY", 6, 1500.25)


def test_holding_without_price_is_error(tmp_path):
    b, _ = broker(tmp_path, {("GET", "/portfolio/holdings"): (200, {"status": "success", "data": [{"tradingsymbol": "X", "exchange": "NSE", "quantity": 1, "t1_quantity": 0}]})})
    with pytest.raises(BrokerApiError):
        b.holdings()


@pytest.mark.parametrize("raw,mapped", [
    ("OPEN", "open"), ("TRIGGER PENDING", "open"), ("UPDATE", "open"), ("PUT ORDER REQ RECEIVED", "open"), ("VALIDATION PENDING", "open"), ("OPEN PENDING", "open"),
    ("MODIFY PENDING", "open"), ("CANCEL PENDING", "open"), ("COMPLETE", "filled"), ("CANCELLED", "cancelled"), ("REJECTED", "rejected"),
])
def test_status_mapping(tmp_path, raw, mapped):
    extra = {"filled_quantity": 10, "pending_quantity": 0, "average_price": 800.0} if raw == "COMPLETE" else {}
    order = orders_of(tmp_path, order_row(status=raw, **extra))[0]
    assert order.status == mapped and order.order_id == "151220000000000"


def test_complete_order_fields_and_time(tmp_path):
    order = orders_of(tmp_path, order_row(status="COMPLETE", filled_quantity=10, average_price=799.9))[0]
    assert order.filled_quantity == 10 and order.average_price == 799.9
    assert order.placed_at == "2026-10-29T09:18:57+05:30"
    assert (order.request.symbol, order.request.side, order.request.quantity, order.request.limit_price) == ("SBIN", "BUY", 10, 800.5)


def test_partial_fill_stays_open(tmp_path):
    order = orders_of(tmp_path, order_row(status="OPEN", filled_quantity=4, pending_quantity=6, average_price=800.0))[0]
    assert order.status == "open" and order.filled_quantity == 4 and order.average_price == 800.0


def test_rejected_carries_reason(tmp_path):
    order = orders_of(tmp_path, order_row(status="REJECTED", status_message="Insufficient funds"))[0]
    assert order.status == "rejected" and order.message == "Insufficient funds"


def test_unknown_status_raises(tmp_path):
    with pytest.raises(BrokerApiError):
        orders_of(tmp_path, order_row(status="SOMETHING NEW"))


def test_missing_timestamp_raises(tmp_path):
    with pytest.raises(BrokerApiError):
        orders_of(tmp_path, order_row(order_timestamp=None))


def test_non_cnc_order_is_left_out(tmp_path):
    assert orders_of(tmp_path, order_row(product="MIS")) == []


def request():
    return OrderRequest("SBIN", "BUY", 10, 800.5, "NSE", "CNC", "test")


def test_place_sends_documented_request(tmp_path):
    placed = {}

    def on_post(data):
        placed.update(data)
        return 200, {"status": "success", "data": {"order_id": "151220000000000"}}

    b, fake = broker(tmp_path, {("POST", "/orders/regular"): on_post, ("GET", "/orders"): (200, {"status": "success", "data": [order_row()]})})
    order = b.place(request(), "client-order-1")
    post = fake.calls[0]
    assert post["method"] == "POST" and post["url"] == "https://api.kite.trade/orders/regular" and post["json"] is None
    assert post["headers"]["Authorization"] == "token KEY123:TOK456" and post["headers"]["X-Kite-Version"] == "3"
    tag = tag_for("client-order-1")
    assert placed == {"tradingsymbol": "SBIN", "exchange": "NSE", "transaction_type": "BUY", "quantity": "10", "product": "CNC", "order_type": "LIMIT", "price": "800.50", "validity": "DAY", "tag": tag}
    assert len(tag) <= 20 and tag.isalnum() and tag == tag_for("client-order-1") and tag != tag_for("client-order-2")
    assert order.order_id == "151220000000000" and order.client_order_id == "client-order-1" and order.status == "open"


def test_duplicate_place_sends_nothing(tmp_path):
    routes = {("POST", "/orders/regular"): (200, {"status": "success", "data": {"order_id": "151220000000000"}}), ("GET", "/orders"): (200, {"status": "success", "data": [order_row()]})}
    b, fake = broker(tmp_path, routes)
    b.place(request(), "dup-1")
    posts = sum(1 for c in fake.calls if c["method"] == "POST")
    again = b.place(request(), "dup-1")
    assert posts == 1 and sum(1 for c in fake.calls if c["method"] == "POST") == 1 and again.order_id == "151220000000000"


def test_place_without_order_id_raises_and_blocks_retry(tmp_path):
    b, fake = broker(tmp_path, {("POST", "/orders/regular"): (200, {"status": "success", "data": {}})})
    with pytest.raises(BrokerApiError):
        b.place(request(), "no-id")
    with pytest.raises(OutcomeUnknown):
        b.place(request(), "no-id")
    assert sum(1 for c in fake.calls if c["method"] == "POST") == 1


def test_cancel(tmp_path):
    cancelled = order_row(status="CANCELLED")
    state = {"n": 0}

    def listing(_):
        state["n"] += 1
        return 200, {"status": "success", "data": [order_row() if state["n"] == 1 else cancelled]}

    b, fake = broker(tmp_path, {("GET", "/orders"): listing, ("DELETE", "/orders/regular/151220000000000"): (200, {"status": "success", "data": {"order_id": "151220000000000"}})})
    result = b.cancel("151220000000000")
    assert result.status == "cancelled"
    assert [c["method"] for c in fake.calls].count("DELETE") == 1


def test_token_exception_tells_person_to_log_in(tmp_path):
    body = {"status": "error", "message": "Incorrect `api_key` or `access_token`.", "error_type": "TokenException"}
    b, _ = broker(tmp_path, {("GET", "/user/margins/equity"): (403, body)})
    with pytest.raises(BrokerApiError) as caught:
        b.funds()
    text = str(caught.value)
    assert caught.value.status == 403 and "expired" in text and "kite_login" in text
    assert "TOK456" not in text and "KEY123" not in text


def test_other_http_error_passes_through(tmp_path):
    b, _ = broker(tmp_path, {("GET", "/orders"): (429, {"status": "error", "message": "Too many requests", "error_type": "NetworkException"})})
    with pytest.raises(BrokerApiError) as caught:
        b.orders()
    assert caught.value.status == 429


def test_settings_names_and_repr_hide_values():
    assert ZerodhaBroker.SETTINGS == ("KITE_API_KEY", "KITE_ACCESS_TOKEN") and "KITE_API_SECRET" in ZerodhaBroker.OPTIONAL_SETTINGS
    assert "TOK456" not in repr(ZerodhaBroker(SETTINGS, Fake({})))


def test_hand_placed_orders_are_skipped_before_status_is_read(tmp_path):
    odd = [order_row(order_id="1", product="MIS", status="SOMETHING NEW"), order_row(order_id="2", order_type="MARKET", price=0, status="SOMETHING NEW"), order_row(order_id="3", exchange="NFO"), order_row(order_id="4")]
    assert [o.order_id for o in orders_of(tmp_path, *odd)] == ["4"]


def test_complete_with_a_different_filled_quantity_raises(tmp_path):
    with pytest.raises(BrokerApiError, match="filled quantity"):
        orders_of(tmp_path, order_row(status="COMPLETE", filled_quantity=4, average_price=800.0))
    [o] = orders_of(tmp_path, order_row(status="COMPLETE", filled_quantity=10, average_price=800.0))
    assert o.status == "filled" and o.filled_quantity == 10
