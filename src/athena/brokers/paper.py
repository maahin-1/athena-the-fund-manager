"""A paper broker: the same `Broker` shape as a live one, filled from a quote source, charged with the owner's cost model, kept in one JSON file.

Rules, all deliberately plain:
  * a buy fills when the best ask (the last price when there is no ask) is at or below the limit, at that price; a sell fills when the best bid (or last) is at or above it. The queue and the depth
    are not modelled: the whole order fills at the touch. A real order for a thin stock can fill worse.
  * an order that does not fill rests as open and is looked at again on `settle`. An open buy holds its cash back, an open sell holds its shares back.
  * long only: a sale needs the shares. A buy needs the cash for the order and its charges. A request that cannot be met is `rejected` with the reason, never partly done.
  * `client_order_id` makes placing idempotent: the same id returns the first order.
Charges are `athena.costs.model` delivery charges on the filled value; whether those rates have been checked against a contract note is the owner's flag in `.athena/charges.json`."""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena.brokers.model import Funds, Holding, Order, OrderRequest, Price
from athena.costs import model as costs

PAPER_PATH = Path(".athena") / "broker" / "paper.json"
OPENING_CASH = 100_000.0
Quote = Callable[[str, str], Price | None]


def _write(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class PaperBroker:
    name = "paper"
    live = False

    def __init__(
        self, quote: Quote, path: Path | str = PAPER_PATH, opening_cash: float = OPENING_CASH, charges: Mapping[str, Any] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._quote, self._path, self._clock = quote, Path(path), clock
        self._charges = charges if charges is not None else costs.load()
        self._state: dict[str, Any] = {"cash": float(opening_cash), "holdings": {}, "orders": [], "seq": 0}
        if self._path.exists():
            self._state = json.loads(self._path.read_text(encoding="utf-8"))

    # --- reading ---
    def _orders(self) -> list[Order]:
        return [Order.from_dict(o) for o in self._state["orders"]]

    def orders(self) -> list[Order]:
        return self._orders()

    def holdings(self) -> list[Holding]:
        return [Holding(k.split(":", 1)[1], k.split(":", 1)[0], int(v["quantity"]), float(v["average_price"])) for k, v in sorted(self._state["holdings"].items()) if v["quantity"] > 0]

    def _held(self, request: OrderRequest) -> int:
        return int(self._state["holdings"].get(f"{request.exchange}:{request.symbol}", {}).get("quantity", 0))

    def _reserved_cash(self) -> float:
        return sum(o.request.value + costs.leg(self._charges, "delivery", "buy", o.request.value)["total"] for o in self._orders() if o.status == "open" and o.request.side == "BUY")

    def _reserved_shares(self, request: OrderRequest) -> int:
        return sum(o.request.quantity for o in self._orders() if o.status == "open" and o.request.side == "SELL" and (o.request.exchange, o.request.symbol) == (request.exchange, request.symbol))

    def funds(self) -> Funds:
        return Funds(cash=float(self._state["cash"]), reserved=self._reserved_cash())

    # --- writing ---
    def _save(self) -> None:
        _write(self._path, self._state)

    def _store(self, order: Order) -> None:
        rows = self._state["orders"]
        for i, row in enumerate(rows):
            if row["order_id"] == order.order_id:
                rows[i] = order.to_dict()
                return
        rows.append(order.to_dict())

    def _now(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    def place(self, request: OrderRequest, client_order_id: str) -> Order:
        for existing in self._orders():
            if existing.client_order_id == client_order_id:
                return existing
        self._state["seq"] += 1
        order = Order(order_id=f"P{self._state['seq']:06d}", client_order_id=client_order_id, request=request, placed_at=self._now())
        problem = self._cannot(request)
        if problem:
            order.status, order.message = "rejected", problem
        else:
            self._try_fill(order)
        self._store(order)
        self._save()
        return order

    def _cannot(self, request: OrderRequest) -> str:
        if request.side == "BUY":
            need = request.value + costs.leg(self._charges, "delivery", "buy", request.value)["total"]
            have = self._state["cash"] - self._reserved_cash()
            if need > have + 1e-9:
                return f"needs Rs {need:,.2f} with charges but Rs {have:,.2f} is free"
        else:
            free = self._held(request) - self._reserved_shares(request)
            if request.quantity > free:
                return f"sells {request.quantity} but only {free} are free to sell (no short selling)"
        return ""

    def _try_fill(self, order: Order) -> None:
        req = order.request
        price = self._quote(req.symbol, req.exchange)
        if price is None:
            return
        if req.side == "BUY":
            touch = price.ask if price.ask else price.last
            if touch > req.limit_price + 1e-9:
                return
        else:
            touch = price.bid if price.bid else price.last
            if touch < req.limit_price - 1e-9:
                return
        value = touch * req.quantity
        fee = round(costs.leg(self._charges, "delivery", req.side.lower(), value)["total"], 2)
        key = f"{req.exchange}:{req.symbol}"
        held = self._state["holdings"].setdefault(key, {"quantity": 0, "average_price": 0.0})
        if req.side == "BUY":
            self._state["cash"] -= value + fee
            total = held["quantity"] * held["average_price"] + value
            held["quantity"] += req.quantity
            held["average_price"] = total / held["quantity"]
        else:
            self._state["cash"] += value - fee
            order.realised = (touch - held["average_price"]) * req.quantity - fee
            held["quantity"] -= req.quantity
            if held["quantity"] == 0:
                held["average_price"] = 0.0
        order.status, order.filled_quantity, order.average_price, order.charges = "filled", req.quantity, round(touch, 4), round(fee, 2)
        order.filled_at = self._now()
        order.message = "" if self._charges.get("verified") else "charges use rates not yet checked against a contract note"

    def settle(self) -> list[Order]:
        """Look again at every open order against the current quotes. Returns the orders that filled now."""
        filled = []
        for order in self._orders():
            if order.status == "open":
                self._try_fill(order)
                if order.status == "filled":
                    self._store(order)
                    filled.append(order)
        if filled:
            self._save()
        return filled

    def cancel(self, order_id: str) -> Order:
        for order in self._orders():
            if order.order_id == order_id:
                if order.status == "open":
                    order.status, order.message = "cancelled", "cancelled by the person"
                    self._store(order)
                    self._save()
                return order
        raise KeyError(f"no order {order_id}")
