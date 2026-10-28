"""What every broker adapter speaks: an order request, an order, a holding, funds, and the `Broker` shape an adapter fills in.

One request type serves paper trading and every live broker, so the person's ticket, the limits and the audit log never depend on which broker is behind them. This release is delivery
(CNC) and limit orders only, long only, on the cash market: nothing else can be written down as a request."""
from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

EXCHANGES = ("NSE", "BSE")
SIDES = ("BUY", "SELL")
PRODUCTS = ("CNC",)
STATUSES = ("open", "filled", "cancelled", "rejected")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&-]{0,19}$")


class OrderError(ValueError):
    """A request that cannot be an order at all (a bad side, a zero quantity, a price that is not a number)."""


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    quantity: int
    limit_price: float
    exchange: str = "NSE"
    product: str = "CNC"
    reason: str = ""  # the person's own words for the trade; kept in the audit log, never used to decide anything

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        object.__setattr__(self, "side", str(self.side).strip().upper())
        object.__setattr__(self, "exchange", str(self.exchange).strip().upper())
        object.__setattr__(self, "product", str(self.product).strip().upper())
        if not _SYMBOL.match(self.symbol):
            raise OrderError(f"{self.symbol!r} is not a symbol")
        if self.side not in SIDES:
            raise OrderError(f"side must be one of {SIDES}")
        if self.exchange not in EXCHANGES:
            raise OrderError(f"exchange must be one of {EXCHANGES}")
        if self.product not in PRODUCTS:
            raise OrderError(f"only delivery ({', '.join(PRODUCTS)}) orders can be written in this release")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise OrderError("quantity must be a whole number above zero")
        if isinstance(self.limit_price, bool) or not isinstance(self.limit_price, (int, float)) or not math.isfinite(self.limit_price) or self.limit_price <= 0:
            raise OrderError("a limit price above zero is required (market orders are not allowed)")
        if abs(self.limit_price * 100 - round(self.limit_price * 100)) > 1e-6:
            raise OrderError("the limit price must be in whole paise")
        object.__setattr__(self, "limit_price", round(float(self.limit_price), 2))

    @property
    def value(self) -> float:
        return self.quantity * self.limit_price

    def fingerprint(self) -> str:
        """What a confirm token is bound to: the order itself (the reason text is not part of it)."""
        text = f"{self.exchange}|{self.symbol}|{self.side}|{self.quantity}|{self.limit_price:.2f}|{self.product}"
        return hashlib.sha256(text.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrderRequest":
        return cls(**{k: data[k] for k in ("symbol", "side", "quantity", "limit_price", "exchange", "product", "reason") if k in data})


@dataclass
class Order:
    order_id: str
    client_order_id: str
    request: OrderRequest
    status: str = "open"
    filled_quantity: int = 0
    average_price: float = 0.0
    charges: float = 0.0
    realised: float = 0.0  # profit or loss this fill locked in, charges taken off (sells only)
    message: str = ""
    placed_at: str = ""
    filled_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["request"] = self.request.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Order":
        fields = {k: v for k, v in data.items() if k != "request"}
        return cls(request=OrderRequest.from_dict(data["request"]), **fields)


@dataclass(frozen=True)
class Holding:
    symbol: str
    exchange: str
    quantity: int
    average_price: float


@dataclass(frozen=True)
class Funds:
    cash: float
    reserved: float = 0.0  # held back by open buy orders

    @property
    def available(self) -> float:
        return self.cash - self.reserved


@dataclass(frozen=True)
class Price:
    """What a quote source gives the paper broker: the last price and the best bid and ask when it has them."""

    last: float
    bid: float | None = None
    ask: float | None = None
    source: str = ""  # where the quote came from ("stream" is live); empty means the caller did not say, and is not checked
    age_seconds: float | None = None  # how old the exchange's time on it is; None means not known, and is not checked


class Broker(Protocol):
    """What an adapter provides. `place` must be idempotent on `client_order_id`: asking twice returns the first order, never a second."""

    name: str
    live: bool  # True only for an adapter that sends orders to a real broker

    def funds(self) -> Funds: ...

    def holdings(self) -> Sequence[Holding]: ...

    def orders(self) -> Sequence[Order]: ...

    def place(self, request: OrderRequest, client_order_id: str) -> Order: ...

    def cancel(self, order_id: str) -> Order: ...


@dataclass
class Findings:
    """The checks on a request, each `(rule, passed, text)`. A breach is a check that did not pass."""

    items: list[tuple[str, bool, str]] = field(default_factory=list)

    def add(self, rule: str, passed: bool, text: str) -> None:
        self.items.append((rule, passed, text))

    @property
    def breaches(self) -> list[tuple[str, bool, str]]:
        return [i for i in self.items if not i[1]]

    @property
    def ok(self) -> bool:
        return not self.breaches
