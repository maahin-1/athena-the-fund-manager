"""Zerodha (Kite Connect v3 REST) live adapter: funds, holdings, today's orders, place and cancel a delivery (CNC) limit order.

Official docs read on 2026-10-29 (nothing here has touched a real account):
  * https://kite.trade/docs/connect/v3/orders/      place `POST /orders/:variety` (form body), cancel `DELETE /orders/:variety/:order_id`, `GET /orders`, statuses, `tag` (alphanumeric, max 20)
  * https://kite.trade/docs/connect/v3/portfolio/   `GET /portfolio/holdings`
  * https://kite.trade/docs/connect/v3/user/        `GET /user/margins/:segment`, `Authorization: token api_key:access_token`, `X-Kite-Version: 3`
  * https://kite.trade/docs/connect/v3/exceptions/  error body `{status, message, error_type}`; TokenException comes with HTTP 403
Rate limits could not be read (the docs page returned 404 / no content), so none are enforced here.

Choices the docs leave open:
  * Funds: cash = `available.cash`, reserved = `utilised.debits`; the docs' example (245431.6 - 145706.55 = 99725.05) shows that equals `available.live_balance` and `net`. Collateral is not counted as cash.
  * Holdings: quantity = `quantity` (settled, "T+2") + `t1_quantity` ("quantity on T+1 day after order execution"), so shares bought but not yet credited still count as owned.
    Pledged shares (`collateral_quantity`) are not added: the docs do not say whether `quantity` already includes them.
  * `tag`: the docs allow alphanumeric, max 20 characters, so the tag is "A" + the first 19 hex characters of sha256(client_order_id): short, stable, never a secret.
  * `order_timestamp` is a string like `2021-05-31 09:18:57` with no zone; the docs do not name one. It is ASSUMED to be India time (UTC+05:30) and written out as ISO-8601 with that offset.
  * Orders that are not CNC (e.g. a person's own intraday orders) cannot be an Athena `OrderRequest`; they are left out of the list.
  * Cancel always uses variety `regular`, the only variety this adapter places.
Plans: orders work on the free Personal plan; market data (quotes, historical candles) needs the paid Connect plan."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from athena.brokers.live.base import BrokerApiError, LiveBroker, money
from athena.brokers.model import Funds, Holding, Order, OrderError, OrderRequest

BASE_URL = "https://api.kite.trade"
VARIETY = "regular"
IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_HELP = "the Zerodha access token expired or is invalid (it lapses every morning); run: python -m athena.brokers.kite_login --listen"
_ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

# Kite status -> Athena status. The docs say the list is not exhaustive: anything else is an error, never a guess.
_OPEN = {"OPEN", "TRIGGER PENDING", "UPDATE", "MODIFIED", "PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "OPEN PENDING", "MODIFY VALIDATION PENDING", "MODIFY PENDING", "CANCEL PENDING", "AMO REQ RECEIVED"}
_STATUS = {**{s: "open" for s in _OPEN}, "COMPLETE": "filled", "CANCELLED": "cancelled", "REJECTED": "rejected"}


def tag_for(client_order_id: str) -> str:
    return "A" + hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()[:19]


def _whole(value: Any, what: str) -> int:
    number = money(value, what)
    if number != int(number) or number < 0:
        raise BrokerApiError(f"the broker's answer has no usable {what}")
    return int(number)


def _placed_at(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrokerApiError("the broker's answer has no usable order_timestamp")
    try:
        moment = datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError:
        raise BrokerApiError("the broker's answer has no usable order_timestamp") from None
    return (moment if moment.tzinfo else moment.replace(tzinfo=IST)).isoformat()


class ZerodhaBroker(LiveBroker):
    name = "zerodha"
    SETTINGS = ("KITE_API_KEY", "KITE_ACCESS_TOKEN")
    OPTIONAL_SETTINGS = ("KITE_API_SECRET",)  # only the daily login step needs it

    # --- the one request path ----------------------------------------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {"X-Kite-Version": "3", "Authorization": f"token {self.settings['KITE_API_KEY']}:{self.settings['KITE_ACCESS_TOKEN']}"}

    def _kite(self, method: str, path: str, **kw: Any) -> Any:
        try:
            body = self._call(method, BASE_URL + path, headers=self._headers(), **kw)
        except BrokerApiError as exc:
            if exc.status == 403:
                raise BrokerApiError(f"zerodha answered HTTP 403 (TokenException): {TOKEN_HELP}", 403) from None
            raise
        if not isinstance(body, dict) or body.get("status") != "success" or not isinstance(body.get("data"), (dict, list)):
            raise BrokerApiError("zerodha's answer is not a success answer in the documented shape")
        return body["data"]

    # --- reads -------------------------------------------------------------------------------------------------------------------------------
    def _funds(self) -> Funds:
        equity = self._kite("GET", "/user/margins/equity")
        if not isinstance(equity, dict) or not isinstance(equity.get("available"), dict) or not isinstance(equity.get("utilised"), dict):
            raise BrokerApiError("the broker's answer has no usable equity margins")
        if equity.get("enabled") is False:
            raise BrokerApiError("the equity segment is not enabled on this Zerodha account")
        return Funds(cash=money(equity["available"].get("cash"), "available cash"), reserved=money(equity["utilised"].get("debits"), "utilised debits"))

    def _holdings(self) -> list[Holding]:
        rows = self._kite("GET", "/portfolio/holdings")
        if not isinstance(rows, list):
            raise BrokerApiError("the broker's answer has no holdings list")
        out = []
        for row in rows:
            if not isinstance(row, dict):
                raise BrokerApiError("the broker's answer has an unreadable holding")
            quantity = _whole(row.get("quantity"), "holding quantity") + _whole(row.get("t1_quantity", 0), "holding t1_quantity")
            if quantity == 0:
                continue
            symbol, exchange = row.get("tradingsymbol"), row.get("exchange")
            if not isinstance(symbol, str) or not isinstance(exchange, str) or not symbol or not exchange:
                raise BrokerApiError("the broker's answer has a holding with no symbol or exchange")
            out.append(Holding(symbol, exchange, quantity, money(row.get("average_price"), "holding average price")))
        return out

    def _orders(self) -> list[Order]:
        rows = self._kite("GET", "/orders")
        if not isinstance(rows, list):
            raise BrokerApiError("the broker's answer has no order list")
        return [o for o in (self._map(r) for r in rows) if o is not None]

    def _map(self, row: Any) -> Order | None:
        if not isinstance(row, dict):
            raise BrokerApiError("the broker's answer has an unreadable order")
        # An order the person placed by hand (intraday, market, other exchange) is skipped before its status or price is read, so it can never break the list.
        if str(row.get("product", "")).upper() != "CNC" or str(row.get("order_type", "")).upper() != "LIMIT" or str(row.get("exchange", "")).upper() not in ("NSE", "BSE"):
            return None  # not an order this system can write down
        raw = str(row.get("status", "")).strip().upper()
        status = _STATUS.get(raw)
        if status is None:
            raise BrokerApiError(f"zerodha reported an order status this adapter does not know: {raw[:40] or 'none'}")
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            raise BrokerApiError("the broker's answer has an order with no order_id")
        filled = _whole(row.get("filled_quantity", 0), "filled quantity")
        quantity = _whole(row.get("quantity"), "order quantity")
        if status == "filled" and filled != quantity:
            raise BrokerApiError(f"zerodha order {order_id} is complete but its filled quantity {filled} is not the order quantity {quantity}")
        try:
            request = OrderRequest(str(row.get("tradingsymbol", "")), str(row.get("transaction_type", "")), quantity, money(row.get("price"), "order price"), str(row.get("exchange", "")), "CNC")
        except OrderError as exc:
            raise BrokerApiError(f"order {order_id} cannot be read as a delivery limit order: {exc}") from None
        average = money(row.get("average_price"), "average price") if filled > 0 else 0.0
        message = row.get("status_message")
        return Order(order_id=order_id, client_order_id="", request=request, status=status, filled_quantity=filled, average_price=average, message=message if isinstance(message, str) else "", placed_at=_placed_at(row.get("order_timestamp")))

    # --- writes ------------------------------------------------------------------------------------------------------------------------------
    def _place(self, request: OrderRequest, client_order_id: str) -> str:
        form = {
            "tradingsymbol": request.symbol, "exchange": request.exchange, "transaction_type": request.side, "quantity": str(request.quantity),
            "product": "CNC", "order_type": "LIMIT", "price": f"{request.limit_price:.2f}", "validity": "DAY", "tag": tag_for(client_order_id),
        }
        data = self._kite("POST", f"/orders/{VARIETY}", data=form)
        order_id = data.get("order_id") if isinstance(data, dict) else None
        if not isinstance(order_id, str) or not order_id.strip():
            raise BrokerApiError("zerodha's answer to the order has no order_id; check its order list before placing again")
        return order_id.strip()

    def _cancel(self, order_id: str) -> None:
        if not _ORDER_ID.match(order_id):
            raise BrokerApiError("that is not a usable order id")
        self._kite("DELETE", f"/orders/{VARIETY}/{order_id}")
