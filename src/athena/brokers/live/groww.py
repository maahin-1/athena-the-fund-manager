"""Groww (Groww Trade API) live adapter: plain REST through `LiveBroker._call`, no SDK.

Read from the official documentation on 2026-10-29 (nothing here has touched a real account):
  * https://groww.in/trade-api/docs/curl            base URL, headers, tokens, rate limits, subscription
  * https://groww.in/trade-api/docs/curl/orders     create / cancel / list / detail
  * https://groww.in/trade-api/docs/curl/portfolio  holdings
  * https://groww.in/trade-api/docs/curl/margin     available margin
  * https://groww.in/trade-api/docs/curl/annexures  enums and order statuses
  * https://groww.in/trade-api/docs/curl/instruments  instrument master (growwapi-assets.groww.in/instruments/instrument.csv)

Facts used: every call sends `Authorization: Bearer <token>`, `Accept: application/json`, `X-API-VERSION: 1.0`. The access token expires daily at 6:00 AM, so the person puts a fresh one in
the settings file each morning (see docs/brokers/groww.md). Orders: POST /v1/order/create, POST /v1/order/cancel, GET /v1/order/list. `order_reference_id` (8 to 20 characters, alphanumeric,
at most two hyphens) carries the client order id. Answers are `{"status": "SUCCESS"|"FAILURE", "payload": {...}}`.

Not confirmed by the documentation (see the docs file): whether the NSE equity `trading_symbol` has a `-EQ` suffix, the time zone of `created_at`, the page numbering of the order list, and which
exchange a holding is on."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from athena.brokers.live.base import BrokerApiError, LiveBroker, money
from athena.brokers.model import Funds, Holding, Order, OrderRequest

BASE_URL = "https://api.groww.in"
IST = timezone(timedelta(hours=5, minutes=30))
PAGE_SIZE = 100

# Groww status -> Athena status. A status not listed here is an error, never a guess.
STATUS_MAP = {
    "OPEN": "open",  # not in the annexure list but it is what the docs' own order examples show
    "NEW": "open",
    "ACKED": "open",
    "TRIGGER_PENDING": "open",
    "APPROVED": "open",
    "MODIFICATION_REQUESTED": "open",
    "CANCELLATION_REQUESTED": "open",
    "EXECUTED": "filled",
    "DELIVERY_AWAITED": "filled",
    "COMPLETED": "filled",
    "CANCELLED": "cancelled",
    "REJECTED": "rejected",
    "FAILED": "rejected",
}
_REFERENCE = re.compile(r"^(?=(?:[^-]*-){0,2}[^-]*$)[A-Za-z0-9-]{8,20}$")


class GrowwBroker(LiveBroker):
    name = "groww"
    SETTINGS = ("GROWW_ACCESS_TOKEN",)
    OPTIONAL_SETTINGS = ()

    # --- plumbing ----------------------------------------------------------------------------------------------------------------------------
    def _headers(self, body: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings['GROWW_ACCESS_TOKEN']}", "Accept": "application/json", "X-API-VERSION": "1.0"}
        if body:
            headers["Content-Type"] = "application/json"
        return headers

    def _payload(self, method: str, path: str, params: dict[str, Any] | None = None, json: Any = None) -> dict[str, Any]:  # noqa: A002
        body = self._call(method, BASE_URL + path, headers=self._headers(json is not None), params=params, json=json)
        if not isinstance(body, dict) or body.get("status") != "SUCCESS" or not isinstance(body.get("payload"), dict):
            raise BrokerApiError(f"groww answered without a SUCCESS payload: {str(body)[:300]}")
        return body["payload"]

    # --- reads -------------------------------------------------------------------------------------------------------------------------------
    def _funds(self) -> Funds:
        payload = self._payload("GET", "/v1/margins/detail/user")
        equity = payload.get("equity_margin_details")
        if not isinstance(equity, dict):
            raise BrokerApiError("the broker's answer has no equity_margin_details")
        # What a delivery buy can use right now. Groww gives no separate "reserved by open orders" figure, so it is 0 here and the available amount is the broker's own number.
        return Funds(cash=money(equity.get("cnc_balance_available"), "cnc_balance_available"), reserved=0.0)

    def _holdings(self) -> list[Holding]:
        payload = self._payload("GET", "/v1/holdings/user")
        rows = payload.get("holdings")
        if not isinstance(rows, list):
            raise BrokerApiError("the broker's answer has no holdings list")
        out = []
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("trading_symbol", "")).strip():
                raise BrokerApiError("a holding in the broker's answer has no trading_symbol")
            qty = money(row.get("quantity"), "holding quantity")
            if qty != int(qty):
                raise BrokerApiError("a holding quantity is not a whole number")
            # The holdings answer carries no exchange field: NSE is assumed (documented in docs/brokers/groww.md).
            out.append(Holding(_symbol(row["trading_symbol"]), "NSE", int(qty), money(row.get("average_price"), "holding average_price")))
        return out

    def _orders(self) -> list[Order]:
        payload = self._payload("GET", "/v1/order/list", params={"segment": "CASH", "page_size": PAGE_SIZE})
        rows = payload.get("order_list")
        if not isinstance(rows, list):
            raise BrokerApiError("the broker's answer has no order_list")
        if len(rows) >= PAGE_SIZE:
            # Page numbering is not documented (see docs/brokers/groww.md), so a full page may hide more orders: stop rather than ignore the rest.
            raise BrokerApiError(f"groww returned {len(rows)} orders, a full page; the order list has no documented paging, so the rest cannot be read")
        return [o for o in (self._map_order(r) for r in rows) if o is not None]

    def _map_order(self, row: Any) -> Order | None:
        if not isinstance(row, dict):
            raise BrokerApiError("an order in the broker's answer is not an object")
        order_id = str(row.get("groww_order_id") or "").strip()
        if not order_id:
            raise BrokerApiError("an order in the broker's answer has no groww_order_id")
        # An order placed by hand in the app (market, intraday, other exchange) is not Athena's: skipped before any status or price is read, so it can never break the list.
        if str(row.get("product") or "").upper() != "CNC" or str(row.get("order_type") or "").upper() != "LIMIT" or str(row.get("exchange") or "").upper() not in ("NSE", "BSE"):
            return None
        raw_status = str(row.get("order_status") or "").strip().upper()
        status = STATUS_MAP.get(raw_status)
        if status is None:
            raise BrokerApiError(f"groww order {order_id} has an unknown status {raw_status!r}")
        side = str(row.get("transaction_type") or "").upper()
        qty = money(row.get("quantity"), "order quantity")
        filled = money(row.get("filled_quantity"), "filled_quantity")
        if qty != int(qty) or filled != int(filled):
            raise BrokerApiError(f"groww order {order_id} has a fractional quantity")
        average = money(row.get("average_fill_price") or 0, "average_fill_price") if filled else 0.0
        if status == "filled" and (filled <= 0 or filled != qty):
            raise BrokerApiError(f"groww order {order_id} is reported as executed but its filled quantity {filled:g} is not the order quantity {qty:g}")
        try:
            request = OrderRequest(_symbol(row.get("trading_symbol", "")), side, int(qty), money(row.get("price"), "order price"), str(row.get("exchange") or "").upper())
        except ValueError as exc:
            raise BrokerApiError(f"groww order {order_id} is not a delivery limit order this system knows: {exc}") from None
        stamp = _stamp(row.get("created_at") or row.get("exchange_time"))
        if not stamp:
            raise BrokerApiError(f"groww order {order_id} has neither created_at nor exchange_time")
        return Order(
            order_id=order_id, client_order_id=str(row.get("order_reference_id") or ""), request=request, status=status, filled_quantity=int(filled), average_price=average,
            message=str(row.get("remark") or ""), placed_at=stamp, filled_at=_stamp(row.get("exchange_time")) if status == "filled" else "",
        )

    # --- writes ------------------------------------------------------------------------------------------------------------------------------
    def _place(self, request: OrderRequest, client_order_id: str) -> str:
        body = {
            "trading_symbol": request.symbol, "quantity": request.quantity, "price": request.limit_price, "validity": "DAY", "exchange": request.exchange,
            "segment": "CASH", "product": "CNC", "order_type": "LIMIT", "transaction_type": request.side, "order_reference_id": reference_id(client_order_id),
        }
        payload = self._payload("POST", "/v1/order/create", json=body)
        order_id = str(payload.get("groww_order_id") or "").strip()
        if not order_id:
            raise BrokerApiError("groww accepted the request but gave no groww_order_id")
        return order_id

    def _cancel(self, order_id: str) -> None:
        self._payload("POST", "/v1/order/cancel", json={"segment": "CASH", "groww_order_id": order_id})


def reference_id(client_order_id: str) -> str:
    """Groww wants 8 to 20 alphanumeric characters with at most two hyphens. A client id that fits goes through unchanged; any other becomes 'AT' + a hash of it (stable, so a retry matches)."""
    if _REFERENCE.match(client_order_id):
        return client_order_id
    return "AT" + hashlib.sha256(client_order_id.encode()).hexdigest()[:18]


def _symbol(value: Any) -> str:
    text = str(value).strip().upper()
    return text[:-3] if text.endswith("-EQ") else text


def _stamp(value: Any) -> str:
    """ISO-8601 with a time zone. Groww's times carry none in the examples; they are read as India time. Missing means empty."""
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise BrokerApiError(f"the broker's answer has an unreadable time {str(value)[:40]!r}") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment.isoformat()
