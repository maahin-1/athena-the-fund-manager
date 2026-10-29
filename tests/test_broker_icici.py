import base64
import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest

from athena.brokers.live import icici
from athena.brokers.live.base import BrokerApiError
from athena.brokers.live.icici import IciciBroker
from athena.brokers.model import OrderError, OrderRequest

NOW = datetime(2026, 10, 29, 4, 30, 5, 987654, tzinfo=timezone.utc)
SETTINGS = {"ICICI_APP_KEY": "appkey", "ICICI_SECRET_KEY": "secret123", "ICICI_SESSION_TOKEN": "c2Vzc2lvbg=="}
MASTER = {"NSE": {"TATASTEEL": "TATSTE", "SBIN": "STABAN"}, "BSE": {"500112": "STABAN"}}


class Fake:
    """A scripted transport: routes by (method, path) to a callable or a fixed answer, and records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers=None, params=None, json=None, data=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "data": data})
        route = self.routes[(method, url.rsplit("/", 1)[-1])]
        return route(data) if callable(route) else route


def ok(success):
    return 200, {"Success": success, "Status": 200, "Error": None}


def make(tmp_path, routes, master=None):
    fake = Fake(routes)
    broker = IciciBroker(SETTINGS, fake, ledger_dir=tmp_path, security_master=lambda: master or MASTER, clock=lambda: NOW)
    return broker, fake


def row(**kw):
    base = {"order_id": "20261029N100001", "exchange_code": "NSE", "stock_code": "TATSTE", "product_type": "Cash", "action": "Buy", "order_type": "Limit", "quantity": "10", "price": "150.50",
            "validity": "Day", "average_price": "0", "cancelled_quantity": "0", "pending_quantity": "10", "status": "Ordered", "user_remark": "abc", "order_datetime": "29-Oct-2026 09:26"}
    base.update(kw)
    return base


def orders_route(nse, bse=()):
    return lambda data: ok(list(nse if json.loads(data)["exchange_code"] == "NSE" else bse))


def req(**kw):
    return OrderRequest(kw.pop("symbol", "TATASTEEL"), kw.pop("side", "BUY"), kw.pop("quantity", 10), kw.pop("limit_price", 150.5), **kw)


# --- signature ---
def test_checksum_known_vector():
    assert icici.checksum("2025-02-05T10:00:00.000Z", '{"exchange_code":"NSE"}', "secret123") == "99e2d43ef404afa87de413b05d6b08a8749d5e7271801b7e2946f53f3deb7f9a"


def test_timestamp_is_utc_to_the_second_with_zero_millis():
    assert icici.timestamp_of(NOW) == "2026-10-29T04:30:05.000Z"


def test_headers_carry_token_prefix_and_sign_the_exact_body_sent(tmp_path):
    broker, fake = make(tmp_path, {("GET", "funds"): ok({"allocated_equity": "1000", "block_by_trade_equity": "100"})})
    broker.funds()
    call = fake.calls[0]
    h = call["headers"]
    assert call["data"] == "{}"
    assert h["X-Timestamp"] == "2026-10-29T04:30:05.000Z" and h["X-AppKey"] == "appkey" and h["X-SessionToken"] == SETTINGS["ICICI_SESSION_TOKEN"]
    assert h["X-Checksum"] == "token " + hashlib.sha256(("2026-10-29T04:30:05.000Z" + "{}" + "secret123").encode()).hexdigest()
    assert call["url"] == "https://api.icicidirect.com/breezeapi/api/v1/funds"


# --- reads ---
def test_funds(tmp_path):
    broker, _ = make(tmp_path, {("GET", "funds"): ok({"allocated_equity": "50000.5", "block_by_trade_equity": "1000"})})
    funds = broker.funds()
    assert (funds.cash, funds.reserved, funds.available) == (50000.5, 1000.0, 49000.5)


def test_funds_missing_field_is_an_error(tmp_path):
    broker, _ = make(tmp_path, {("GET", "funds"): ok({"allocated_equity": "50000.5"})})
    with pytest.raises(BrokerApiError):
        broker.funds()


def test_holdings_use_portfolio_average_price(tmp_path):
    routes = {
        ("GET", "dematholdings"): ok([{"stock_code": "TATSTE", "quantity": "7"}, {"stock_code": "STABAN", "quantity": "0"}]),
        ("GET", "portfolioholdings"): ok([{"stock_code": "TATSTE", "quantity": "7", "average_price": "120.25"}]),
    }
    broker, fake = make(tmp_path, routes)
    (h,) = broker.holdings()
    assert (h.symbol, h.exchange, h.quantity, h.average_price) == ("TATASTEEL", "NSE", 7, 120.25)
    assert json.loads(fake.calls[1]["data"])["stock_code"] == "TATSTE"


def test_holding_without_average_price_is_an_error(tmp_path):
    routes = {("GET", "dematholdings"): ok([{"stock_code": "TATSTE", "quantity": "7"}]), ("GET", "portfolioholdings"): ok([{"stock_code": "TATSTE"}])}
    broker, _ = make(tmp_path, routes)
    with pytest.raises(BrokerApiError):
        broker.holdings()


@pytest.mark.parametrize("raw,status,filled", [
    ("Requested", "open", 0), ("Queued", "open", 0), ("Ordered", "open", 0), ("Freezed", "open", 0),
    ("Executed", "filled", 10), ("Cancelled", "cancelled", 0), ("Expired", "cancelled", 0), ("Rejected", "rejected", 0),
])
def test_order_status_mapping(tmp_path, raw, status, filled):
    cancelled = "10" if status == "cancelled" else "0"
    pending = "0" if status in ("filled", "cancelled") else "10"
    r = row(status=raw, average_price="150.5" if filled else "0", pending_quantity=pending, cancelled_quantity=cancelled)
    broker, _ = make(tmp_path, {("GET", "order"): orders_route([r])})
    (o,) = broker.orders()
    assert (o.status, o.filled_quantity) == (status, filled)
    assert o.request.symbol == "TATASTEEL" and o.request.side == "BUY" and o.request.limit_price == 150.5


def test_partial_fill_and_partial_cancel(tmp_path):
    part = row(status="Partially Executed", quantity="10", pending_quantity="6", average_price="150.4")
    cut = row(order_id="2", status="Partially Executed And Cancelled", quantity="10", pending_quantity="0", cancelled_quantity="6", average_price="150.4")
    broker, _ = make(tmp_path, {("GET", "order"): orders_route([part, cut])})
    a, b = broker.orders()
    assert (a.status, a.filled_quantity, a.average_price) == ("open", 4, 150.4)
    assert (b.status, b.filled_quantity) == ("cancelled", 4)


def test_placed_at_is_india_time_iso(tmp_path):
    broker, _ = make(tmp_path, {("GET", "order"): orders_route([row()])})
    assert broker.orders()[0].placed_at == "2026-10-29T09:26:00+05:30"


def test_unknown_status_raises(tmp_path):
    broker, _ = make(tmp_path, {("GET", "order"): orders_route([row(status="Teleported")])})
    with pytest.raises(BrokerApiError, match="does not know"):
        broker.orders()


def test_order_window_is_todays_india_date_for_both_exchanges(tmp_path):
    broker, fake = make(tmp_path, {("GET", "order"): orders_route([])})
    assert broker.orders() == []
    bodies = [json.loads(c["data"]) for c in fake.calls]
    assert [b["exchange_code"] for b in bodies] == ["NSE", "BSE"]
    assert bodies[0]["from_date"].startswith("2026-10-29T00:00:00")


# --- writes ---
def test_place_sends_exactly_the_documented_body(tmp_path):
    seen = {}

    def post(data):
        seen["body"] = data
        return ok({"order_id": "20261029N100001", "message": "Successfully Placed the order", "user_remark": "x"})

    broker, fake = make(tmp_path, {("POST", "order"): post, ("GET", "order"): orders_route([row(user_remark="")])})
    order = broker.place(req(), "ath-abc-123")
    assert json.loads(seen["body"]) == {"stock_code": "TATSTE", "exchange_code": "NSE", "product": "cash", "action": "buy", "order_type": "limit", "quantity": "10", "price": "150.50",
                                        "validity": "day", "user_remark": "A" + hashlib.sha256(b"ath-abc-123").hexdigest()[:19]}
    assert order.order_id == "20261029N100001" and order.client_order_id == "ath-abc-123"
    post_call = next(c for c in fake.calls if c["method"] == "POST")
    assert post_call["headers"]["X-Checksum"].startswith("token ")


def test_alphanumeric_client_id_is_used_as_the_remark():
    assert icici.remark_for("abc123") == "abc123"


def test_duplicate_client_order_id_sends_nothing(tmp_path):
    routes = {("POST", "order"): ok({"order_id": "20261029N100001"}), ("GET", "order"): orders_route([row()])}
    broker, fake = make(tmp_path, routes)
    broker.place(req(), "dup1")
    posts = sum(1 for c in fake.calls if c["method"] == "POST")
    again = broker.place(req(), "dup1")
    assert posts == 1 and sum(1 for c in fake.calls if c["method"] == "POST") == 1
    assert again.order_id == "20261029N100001"


def test_place_without_order_id_is_an_error(tmp_path):
    broker, _ = make(tmp_path, {("POST", "order"): ok({"message": "hm"})})
    with pytest.raises(BrokerApiError):
        broker.place(req(), "noid")


def test_unknown_stock_is_a_clear_error_and_sends_nothing(tmp_path):
    broker, fake = make(tmp_path, {("POST", "order"): ok({"order_id": "1"})})
    with pytest.raises(OrderError, match="NOSUCH.*security master"):
        broker.place(req(symbol="NOSUCH"), "x1")
    assert not any(c["method"] == "POST" for c in fake.calls)


def test_cancel_uses_delete_with_the_orders_exchange(tmp_path):
    state = {"cancelled": False}

    def listing(data):
        r = row(status="Cancelled", pending_quantity="0", cancelled_quantity="10") if state["cancelled"] else row()
        return ok([r] if json.loads(data)["exchange_code"] == "NSE" else [])

    def delete(data):
        state["cancelled"] = True
        return ok({"order_id": "20261029N100001", "message": "cancelled"})

    broker, fake = make(tmp_path, {("GET", "order"): listing, ("DELETE", "order"): delete})
    after = broker.cancel("20261029N100001")
    assert after.status == "cancelled"
    d = next(c for c in fake.calls if c["method"] == "DELETE")
    assert json.loads(d["data"]) == {"exchange_code": "NSE", "order_id": "20261029N100001"}


# --- errors ---
def test_error_body_raises(tmp_path):
    broker, _ = make(tmp_path, {("GET", "funds"): (200, {"Success": None, "Status": 500, "Error": "Invalid session"})})
    with pytest.raises(BrokerApiError, match="refused"):
        broker.funds()


def test_http_error_raises_and_explains_a_dead_session(tmp_path):
    broker, _ = make(tmp_path, {("GET", "funds"): (401, {"message": "no"})})
    with pytest.raises(BrokerApiError, match="lapses every day"):
        broker.funds()


# --- session exchange and the security master ---
def test_exchange_api_session():
    token = base64.b64encode(b"user1:sess1").decode()
    seen = {}

    def transport(method, url, headers=None, params=None, json=None, data=None):
        seen.update(method=method, url=url, headers=headers, data=data)
        return 200, {"Success": {"session_token": token}, "Status": 200, "Error": None}

    assert icici.exchange_api_session("appkey", "code9", transport) == token
    assert seen["method"] == "GET" and seen["url"].endswith("/customerdetails") and "X-Checksum" not in seen["headers"]
    assert json.loads(seen["data"]) == {"SessionToken": "code9", "AppKey": "appkey"}


def test_exchange_api_session_rejects_a_token_that_is_not_user_session():
    bad = base64.b64encode(b"nocolon").decode()
    with pytest.raises(BrokerApiError):
        icici.exchange_api_session("a", "b", lambda *a, **k: (200, {"Success": {"session_token": bad}, "Status": 200, "Error": None}))


def _zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("NSEScripMaster.txt", '"Token","ShortName","Series"," ExchangeCode"\n3499,TATSTE,EQ,TATASTEEL\n')
        z.writestr("BSEScripMaster.txt", '"Token","ShortName","Series","ExchangeCode"\n1,STABAN,A,500112\n')
    return buf.getvalue()


def test_parse_security_master():
    assert icici.parse_security_master(_zip()) == {"NSE": {"TATASTEEL": "TATSTE"}, "BSE": {"500112": "STABAN"}}


def test_master_is_cached_on_disk_and_loader_runs_once(tmp_path):
    calls = []

    def loader():
        calls.append(1)
        return MASTER

    first = icici.SecurityMaster(loader, tmp_path, lambda: NOW)
    assert first.stock_code("sbin", "NSE") == "STABAN"
    second = icici.SecurityMaster(lambda: pytest.fail("fetched again"), tmp_path, lambda: NOW)
    assert second.stock_code("TATASTEEL", "NSE") == "TATSTE" and len(calls) == 1


def test_hand_placed_orders_are_skipped_before_status_or_price_is_read(tmp_path):
    odd = [row(order_id="M1", order_type="Market", price="0", status="Teleported"), row(order_id="P1", product_type="Margin", status="Teleported"), row(order_id="K1")]
    broker, _ = make(tmp_path, {("GET", "order"): orders_route(odd)})
    assert [o.order_id for o in broker.orders()] == ["K1"]


def test_executed_with_unmatched_quantities_is_an_error_not_a_guess(tmp_path):
    bad = row(status="Executed", pending_quantity="3", cancelled_quantity="0", average_price="150.5")
    broker, _ = make(tmp_path, {("GET", "order"): orders_route([bad])})
    with pytest.raises(BrokerApiError, match="filled quantity"):
        broker.orders()


@pytest.mark.parametrize("missing", ["pending_quantity", "cancelled_quantity"])
def test_a_missing_pending_or_cancelled_quantity_is_an_error_not_zero(tmp_path, missing):
    r = row(status="Ordered")
    del r[missing]
    broker, _ = make(tmp_path, {("GET", "order"): orders_route([r])})
    with pytest.raises(BrokerApiError, match="usable"):
        broker.orders()
