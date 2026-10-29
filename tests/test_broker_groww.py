import json

import pytest

from athena.brokers.live.base import BrokerApiError
from athena.brokers.live.groww import STATUS_MAP, GrowwBroker, reference_id
from athena.brokers.model import OrderRequest

TOKEN = "tok-SECRET-123"
CID = "ATH-20261029-0001"


class Fake:
    def __init__(self, routes):
        self.routes = routes  # (method, path) -> (status, body)
        self.calls = []

    def __call__(self, method, url, headers=None, params=None, json=None, data=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "params": params, "json": json})
        return self.routes[(method, url.replace("https://api.groww.in", ""))]


def ok(payload):
    return 200, {"status": "SUCCESS", "payload": payload}


def row(**kw):
    base = {
        "groww_order_id": "GMK1", "trading_symbol": "SBIN", "order_status": "OPEN", "remark": "Order placed successfully", "quantity": 10, "price": 800.5, "trigger_price": 0,
        "filled_quantity": 0, "remaining_quantity": 10, "average_fill_price": 0, "validity": "DAY", "exchange": "NSE", "order_type": "LIMIT", "transaction_type": "BUY",
        "segment": "CASH", "product": "CNC", "created_at": "2026-10-29T10:15:30", "exchange_time": "2026-10-29T10:15:31", "order_reference_id": CID,
    }
    base.update(kw)
    return base


def make(tmp_path, routes):
    fake = Fake(routes)
    return GrowwBroker({"GROWW_ACCESS_TOKEN": TOKEN}, fake, tmp_path), fake


def listing(*rows):
    return {("GET", "/v1/order/list"): ok({"order_list": list(rows)})}


def request():
    return OrderRequest("SBIN", "BUY", 10, 800.5)


def test_funds(tmp_path):
    b, fake = make(tmp_path, {("GET", "/v1/margins/detail/user"): ok({"clear_cash": 5000.0, "equity_margin_details": {"cnc_balance_available": 4321.5}})})
    funds = b.funds()
    assert funds.available == 4321.5
    h = fake.calls[0]["headers"]
    assert h["Authorization"] == f"Bearer {TOKEN}" and h["X-API-VERSION"] == "1.0" and h["Accept"] == "application/json"
    assert TOKEN not in repr(b)


def test_funds_missing_field_raises(tmp_path):
    b, _ = make(tmp_path, {("GET", "/v1/margins/detail/user"): ok({"equity_margin_details": {}})})
    with pytest.raises(BrokerApiError):
        b.funds()


def test_holdings(tmp_path):
    b, _ = make(tmp_path, {("GET", "/v1/holdings/user"): ok({"holdings": [{"isin": "INE", "trading_symbol": "RELIANCE", "quantity": 10, "average_price": 100}]})})
    [h] = b.holdings()
    assert (h.symbol, h.exchange, h.quantity, h.average_price) == ("RELIANCE", "NSE", 10, 100.0)


def test_holdings_bad_quantity(tmp_path):
    b, _ = make(tmp_path, {("GET", "/v1/holdings/user"): ok({"holdings": [{"trading_symbol": "X", "quantity": "n/a", "average_price": 1}]})})
    with pytest.raises(BrokerApiError):
        b.holdings()


@pytest.mark.parametrize("raw,expected", sorted(STATUS_MAP.items()))
def test_every_documented_status(tmp_path, raw, expected):
    filled = 10 if expected == "filled" else 0
    b, _ = make(tmp_path, listing(row(order_status=raw, filled_quantity=filled, average_fill_price=799.0 if filled else 0)))
    [o] = b.orders()
    assert o.status == expected
    assert o.filled_quantity == filled


def test_documented_statuses_are_complete():
    assert set(STATUS_MAP) == {"OPEN", "NEW", "ACKED", "TRIGGER_PENDING", "APPROVED", "REJECTED", "FAILED", "EXECUTED", "DELIVERY_AWAITED", "CANCELLED", "CANCELLATION_REQUESTED",
                               "MODIFICATION_REQUESTED", "COMPLETED"}


def test_order_mapping_details(tmp_path):
    b, fake = make(tmp_path, listing(row(order_status="EXECUTED", filled_quantity=10, average_fill_price=799.25, trading_symbol="SBIN-EQ")))
    [o] = b.orders()
    assert o.average_price == 799.25 and o.request.symbol == "SBIN" and o.client_order_id == CID
    assert o.placed_at == "2026-10-29T10:15:30+05:30"
    assert fake.calls[0]["params"]["segment"] == "CASH"


def test_unknown_status_raises(tmp_path):
    b, _ = make(tmp_path, listing(row(order_status="SOMETHING_NEW")))
    with pytest.raises(BrokerApiError, match="unknown status"):
        b.orders()


def test_executed_without_fill_raises(tmp_path):
    b, _ = make(tmp_path, listing(row(order_status="EXECUTED", filled_quantity=0)))
    with pytest.raises(BrokerApiError):
        b.orders()


def test_order_without_id_raises(tmp_path):
    b, _ = make(tmp_path, listing(row(groww_order_id="")))
    with pytest.raises(BrokerApiError):
        b.orders()


def test_place_sends_documented_request(tmp_path):
    routes = {("POST", "/v1/order/create"): ok({"groww_order_id": "GMK1", "order_status": "OPEN", "order_reference_id": CID, "remark": "ok"}), **listing(row())}
    b, fake = make(tmp_path, routes)
    order = b.place(request(), CID)
    first = fake.calls[0]
    assert first["method"] == "POST" and first["url"] == "https://api.groww.in/v1/order/create"
    assert first["json"] == {
        "trading_symbol": "SBIN", "quantity": 10, "price": 800.5, "validity": "DAY", "exchange": "NSE", "segment": "CASH", "product": "CNC", "order_type": "LIMIT",
        "transaction_type": "BUY", "order_reference_id": CID,
    }
    assert first["headers"]["Content-Type"] == "application/json" and first["headers"]["Authorization"].startswith("Bearer ")
    assert order.order_id == "GMK1" and order.client_order_id == CID and order.status == "open"


def test_place_is_idempotent(tmp_path):
    routes = {("POST", "/v1/order/create"): ok({"groww_order_id": "GMK1", "order_status": "OPEN"}), **listing(row())}
    b, fake = make(tmp_path, routes)
    b.place(request(), CID)
    before = [c for c in fake.calls if c["method"] == "POST"]
    again = b.place(request(), CID)
    after = [c for c in fake.calls if c["method"] == "POST"]
    assert len(before) == len(after) == 1 and again.order_id == "GMK1"


def test_place_without_order_id_raises(tmp_path):
    b, _ = make(tmp_path, {("POST", "/v1/order/create"): ok({"order_status": "OPEN"})})
    with pytest.raises(BrokerApiError):
        b.place(request(), CID)


def test_place_failure_status_raises(tmp_path):
    b, _ = make(tmp_path, {("POST", "/v1/order/create"): (200, {"status": "FAILURE", "error": {"code": "GA001", "message": "bad"}})})
    with pytest.raises(BrokerApiError):
        b.place(request(), CID)


def test_http_error_is_redacted(tmp_path):
    b, _ = make(tmp_path, {("GET", "/v1/margins/detail/user"): (401, {"status": "FAILURE", "error": {"message": f"bad token {TOKEN}"}})})
    with pytest.raises(BrokerApiError) as err:
        b.funds()
    assert err.value.status == 401


def test_cancel(tmp_path):
    routes = {("POST", "/v1/order/cancel"): ok({"groww_order_id": "GMK1", "order_status": "CANCELLATION_REQUESTED"}), **listing(row())}
    b, fake = make(tmp_path, routes)
    b.cancel("GMK1")
    post = [c for c in fake.calls if c["method"] == "POST"][0]
    assert post["url"].endswith("/v1/order/cancel") and post["json"] == {"segment": "CASH", "groww_order_id": "GMK1"}


def test_reference_id_rules():
    assert reference_id(CID) == CID
    odd = reference_id("a_b/c")
    assert 8 <= len(odd) <= 20 and odd.isalnum() and odd == reference_id("a_b/c")
    assert json.dumps(odd)


def test_hand_placed_orders_are_skipped_before_status_or_price_is_read(tmp_path):
    odd = [row(groww_order_id="M1", order_type="MARKET", price=0, order_status="WEIRD_NEW"), row(groww_order_id="I1", product="MIS"), row(groww_order_id="F1", exchange="MCX"), row(groww_order_id="K1")]
    b, _ = make(tmp_path, listing(*odd))
    assert [o.order_id for o in b.orders()] == ["K1"]


def test_missing_times_raise_instead_of_an_empty_placed_at(tmp_path):
    b, _ = make(tmp_path, listing(row(created_at=None, exchange_time=None)))
    with pytest.raises(BrokerApiError, match="created_at"):
        b.orders()


def test_a_full_page_of_orders_raises_because_paging_is_undocumented(tmp_path):
    b, _ = make(tmp_path, listing(*[row(groww_order_id=f"G{i}") for i in range(100)]))
    with pytest.raises(BrokerApiError, match="full page"):
        b.orders()


def test_executed_with_a_different_filled_quantity_raises(tmp_path):
    b, _ = make(tmp_path, listing(row(order_status="EXECUTED", filled_quantity=4, average_fill_price=799.0)))
    with pytest.raises(BrokerApiError, match="filled quantity"):
        b.orders()
