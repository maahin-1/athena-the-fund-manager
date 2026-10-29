"""Live adapter for Angel One (SmartAPI REST): delivery limit orders on NSE/BSE, long only.

Every call goes through `LiveBroker._call`, so the tests inject a fake transport and nothing here touches the network on its own. The SmartAPI SDK is used for ONE thing only: the login
(`athena.brokers.angel_login.login`), which turns ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN and ANGEL_TOTP_SECRET into a JWT. Orders, funds, holdings and the order book are plain REST.

Sources, read 2026-10-29. The official docs site (https://smartapi.angelone.in/docs and /docs/Orders) is a JavaScript page that could not be read by the tools used, so the endpoint paths and
headers were taken from the official SDK source (https://github.com/angel-one/smartapi-python, package `smartapi-python` 1.5.3, `SmartApi/smartConnect.py`) and its README, and the rest from
SmartAPI forum posts (https://smartapi.angelone.in/smartapi/forum/post/17334 on the 20-character `ordertag` limit). What is and is not confirmed is listed in docs/brokers/angelone.md.

  host     https://apiconnect.angelone.in
  funds    GET  /rest/secure/angelbroking/user/v1/getRMS
  holdings GET  /rest/secure/angelbroking/portfolio/v1/getAllHolding
  orders   GET  /rest/secure/angelbroking/order/v1/getOrderBook
  place    POST /rest/secure/angelbroking/order/v1/placeOrder
  cancel   POST /rest/secure/angelbroking/order/v1/cancelOrder

Headers on every call: Authorization "Bearer <jwt>", Content-Type and Accept application/json, X-PrivateKey (the API key), X-UserType USER, X-SourceID WEB, X-ClientLocalIP, X-ClientPublicIP and
X-MACAddress. Local values: the local IP is this machine's own address from its host name (no lookup service is called; 127.0.0.1 if that fails), the public IP is ANGEL_PUBLIC_IP (set it to the
static IP registered with Angel One) and otherwise the local IP, and the MAC is ANGEL_MAC_ADDRESS or this machine's own.

Angel answers HTTP 200 with {"status": bool, "message", "errorcode", "data"}: a body whose status is not true is an error. Times in the order book carry no zone; they are read as India time (+05:30)."""
from __future__ import annotations

import json
import re
import socket
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from athena.brokers.live.base import LEDGER_DIR, BrokerApiError, LiveBroker, Transport, json_text, money
from athena.brokers.model import Funds, Holding, Order, OrderError, OrderRequest
from athena.contracts import AthenaError
from athena.llm.envfile import DEFAULT_ENV_FILE

ROOT = "https://apiconnect.angelone.in"
PATH_FUNDS = "/rest/secure/angelbroking/user/v1/getRMS"
PATH_HOLDINGS = "/rest/secure/angelbroking/portfolio/v1/getAllHolding"
PATH_ORDERS = "/rest/secure/angelbroking/order/v1/getOrderBook"
PATH_PLACE = "/rest/secure/angelbroking/order/v1/placeOrder"
PATH_CANCEL = "/rest/secure/angelbroking/order/v1/cancelOrder"
MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
MASTER_CACHE = Path(".athena") / "broker" / "angelone-scrip-master.json"
IST = timezone(timedelta(hours=5, minutes=30))
TAG_LIMIT = 20  # Angel rejects an ordertag longer than this
SUFFIXES = ("-EQ", "-BE", "-BZ", "-SM", "-ST")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_TIME = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{1,2}):(\d{2}):(\d{2})$")

# Order-book status text (lower case) -> Athena status. Anything not listed raises; it is never guessed as filled.
_OPEN = {"open", "open pending", "trigger pending", "validation pending", "put order req received", "modify pending", "modify validation pending", "modified", "cancel pending", "not modified", "not cancelled",
         # after-market orders: neither the SDK nor the docs name Angel's AMO strings; these are the exchange-OMS ones the sibling brokers report (see docs/brokers/angelone.md)
         "after market order req received", "modify after market order req received", "amo req received", "amo submitted"}
STATUS_MAP = {**{s: "open" for s in _OPEN}, "complete": "filled", "cancelled": "cancelled", "rejected": "rejected"}

ScripLoader = Callable[[], Sequence[Mapping[str, Any]]]


def cached_scrip_master(cache: Path | str = MASTER_CACHE, fetch: Callable[[], Any] | None = None, now: Callable[[], datetime] | None = None) -> list[dict[str, Any]]:
    """The NSE/BSE cash-equity rows of OpenAPIScripMaster.json, cached on disk and refreshed when the cache was written on an earlier India-time day. `fetch` returns the parsed file."""
    path, clock = Path(cache), now or (lambda: datetime.now(IST))
    today = clock().astimezone(IST).date()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["day"] == today.isoformat() and isinstance(saved["rows"], list) and saved["rows"]:
            return saved["rows"]
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no cache, a damaged one, or one from an earlier day: fetch again
    rows = (fetch or _fetch_master)()
    if not isinstance(rows, list):
        raise BrokerApiError("the Angel One instrument file is not a list")
    keep = [r for r in rows if isinstance(r, dict) and r.get("exch_seg") in ("NSE", "BSE") and not r.get("instrumenttype")]
    if not keep:
        raise BrokerApiError("the Angel One instrument file has no NSE/BSE equity rows")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"day": today.isoformat(), "rows": keep}), encoding="utf-8")
    tmp.replace(path)
    return keep


def _fetch_master() -> Any:
    try:
        return requests.get(MASTER_URL, timeout=120).json()
    except (requests.RequestException, ValueError) as exc:
        raise BrokerApiError(f"the Angel One instrument file could not be fetched: {type(exc).__name__}") from None


def parse_time(text: Any) -> str:
    """'16-May-2024 11:18:11' (India time, no zone in the text) as ISO-8601 with +05:30."""
    m = _TIME.match(str(text or "").strip())
    month = _MONTHS.get(m.group(2).lower()) if m else None
    if not m or not month:
        raise BrokerApiError("the broker's answer has no usable update time")
    try:
        return datetime(int(m.group(3)), month, int(m.group(1)), int(m.group(4)), int(m.group(5)), int(m.group(6)), tzinfo=IST).isoformat()
    except ValueError:
        raise BrokerApiError("the broker's answer has no usable update time") from None


def _whole(value: Any, what: str) -> int:
    number = money(value, what)
    if number != int(number) or number < 0:
        raise BrokerApiError(f"the broker's answer has no usable {what}")
    return int(number)


def _bare(symbol: str) -> str:
    for suffix in SUFFIXES:
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


class AngelOneBroker(LiveBroker):
    name = "angelone"
    SETTINGS = ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")
    OPTIONAL_SETTINGS = ("ANGEL_PUBLIC_IP", "ANGEL_MAC_ADDRESS")

    def __init__(self, settings: Mapping[str, str] | None = None, transport: Transport | None = None, ledger_dir: Path | str = LEDGER_DIR, env_file: Path | str = DEFAULT_ENV_FILE,
                 jwt_token: str = "", scrip_loader: ScripLoader | None = None, login_fn: Callable[[], str] | None = None, root: str = ROOT):
        super().__init__(settings, transport, ledger_dir, env_file)
        self._jwt = jwt_token
        self._login_fn = login_fn
        self._scrip_loader = scrip_loader or cached_scrip_master
        self._scrips: list[Mapping[str, Any]] | None = None
        self._root = root.rstrip("/")
        self._varieties: dict[str, str] = {}

    # --- login, headers ---------------------------------------------------------------------------------------------------------------------
    def _token(self) -> str:
        if not self._jwt:
            if self._login_fn is not None:
                self._jwt = self._login_fn()
            else:
                from athena.brokers.angel_login import login  # the SDK is imported there, and only here

                s = self.settings
                try:
                    self._jwt = login(s["ANGEL_API_KEY"], s["ANGEL_CLIENT_CODE"], s["ANGEL_PIN"], s["ANGEL_TOTP_SECRET"]).jwt_token
                except (AthenaError, KeyError) as exc:
                    raise BrokerApiError(f"angelone login failed: {exc}") from None
            if not self._jwt:
                raise BrokerApiError("angelone login gave no token")
        return self._jwt

    def _headers(self) -> dict[str, str]:
        s = self.settings
        try:
            local = socket.gethostbyname(socket.gethostname())
        except OSError:
            local = "127.0.0.1"
        mac = s.get("ANGEL_MAC_ADDRESS") or ":".join(re.findall("..", "%012x" % uuid.getnode()))
        return {
            "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json", "Accept": "application/json",
            "X-PrivateKey": s["ANGEL_API_KEY"], "X-UserType": "USER", "X-SourceID": "WEB",
            "X-ClientLocalIP": local, "X-ClientPublicIP": s.get("ANGEL_PUBLIC_IP") or local, "X-MACAddress": mac,
        }

    def _api(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        reply = self._call(method, self._root + path, headers=self._headers(), json=dict(body) if body is not None else None)
        if not isinstance(reply, Mapping):
            raise BrokerApiError(f"angelone answered something that is not a JSON object: {json_text(reply)}")
        if reply.get("status") is not True:
            raise BrokerApiError(f"angelone refused: {reply.get('message') or 'no reason given'} (errorcode {reply.get('errorcode') or 'none'})")
        return reply.get("data")

    # --- funds, holdings, orders ------------------------------------------------------------------------------------------------------------
    def _funds(self) -> Funds:
        data = self._api("GET", PATH_FUNDS)
        if not isinstance(data, Mapping):
            raise BrokerApiError("angelone's funds answer has no data")
        # `availablecash` is what can be used now; blocked margin is already taken off it, so nothing more is reserved here.
        return Funds(cash=money(data.get("availablecash"), "available cash"), reserved=0.0)

    def _holdings(self) -> list[Holding]:
        data = self._api("GET", PATH_HOLDINGS)
        if not isinstance(data, Mapping) or not isinstance(data.get("holdings"), list):
            raise BrokerApiError("angelone's holdings answer has no holdings list")
        out = []
        for row in data["holdings"]:
            if not isinstance(row, Mapping) or not str(row.get("tradingsymbol") or "").strip() or row.get("exchange") not in ("NSE", "BSE"):
                raise BrokerApiError("angelone's holdings answer has a row without a symbol or an NSE/BSE exchange")
            out.append(Holding(_bare(str(row["tradingsymbol"]).strip().upper()), str(row["exchange"]), _whole(row.get("quantity"), "holding quantity"), money(row.get("averageprice"), "holding average price")))
        return out

    def _orders(self) -> list[Order]:
        data = self._api("GET", PATH_ORDERS)
        if data is None:
            return []  # Angel sends data null when there are no orders today
        if not isinstance(data, list):
            raise BrokerApiError("angelone's order book is not a list")
        orders, varieties = [], {}
        for row in data:
            if not isinstance(row, Mapping):
                raise BrokerApiError("angelone's order book has a row that is not an object")
            if str(row.get("producttype", "")).upper() != "DELIVERY" or str(row.get("ordertype", "")).upper() != "LIMIT" or row.get("exchange") not in ("NSE", "BSE"):
                continue  # not an order Athena can write down (placed in the app: market, intraday, other exchange)
            order = self._order(row)
            orders.append(order)
            varieties[order.order_id] = str(row.get("variety") or "")
        self._varieties = varieties
        return orders

    def _order(self, row: Mapping[str, Any]) -> Order:
        order_id = str(row.get("orderid") or "").strip()
        if not order_id:
            raise BrokerApiError("angelone's order book has a row without an order id")
        raw = str(row.get("orderstatus") or row.get("status") or "").strip().lower()
        status = STATUS_MAP.get(raw)
        if status is None:
            raise BrokerApiError(f"angelone reported an order status Athena does not know: {raw!r} (order {order_id})")
        try:
            request = OrderRequest(_bare(str(row.get("tradingsymbol") or "").upper()), str(row.get("transactiontype") or ""), _whole(row.get("quantity"), "order quantity"), money(row.get("price"), "order price"), str(row["exchange"]))
        except OrderError as exc:
            raise BrokerApiError(f"angelone's order {order_id} cannot be read as an Athena order: {exc}") from None
        when = parse_time(row.get("updatetime"))
        filled = _whole(row.get("filledshares"), "filled quantity")
        if status == "filled" and filled != request.quantity:
            raise BrokerApiError(f"angelone's order {order_id} is complete but its filled quantity {filled} is not the order quantity {request.quantity}")
        return Order(
            order_id=order_id, client_order_id=str(row.get("ordertag") or ""), request=request, status=status,
            filled_quantity=filled, average_price=money(row.get("averageprice"), "average price"),
            message=str(row.get("text") or "") if status == "rejected" else "", placed_at=when, filled_at=when if status == "filled" else "",
        )

    # --- place, cancel ----------------------------------------------------------------------------------------------------------------------
    def _token_for(self, request: OrderRequest) -> tuple[str, str]:
        """(tradingsymbol, symboltoken) from the instrument file: an NSE/BSE cash-equity row, preferring the '-EQ' series. Unknown or ambiguous is an error."""
        if self._scrips is None:
            self._scrips = list(self._scrip_loader())
        rows = [r for r in self._scrips if r.get("exch_seg") == request.exchange and not r.get("instrumenttype") and str(r.get("symbol", "")).upper() in (f"{request.symbol}-EQ", request.symbol)]
        eq = [r for r in rows if str(r.get("symbol", "")).upper() == f"{request.symbol}-EQ"]
        rows = eq or rows
        if not rows:
            raise BrokerApiError(f"{request.symbol} is not in Angel One's {request.exchange} equity instrument list (the '-EQ' series); it will not be guessed")
        tokens = {(str(r["symbol"]), str(r.get("token") or "").strip()) for r in rows}
        if len(tokens) != 1 or not next(iter(tokens))[1]:
            raise BrokerApiError(f"{request.symbol} on {request.exchange} matches more than one instrument or none with a token")
        return next(iter(tokens))

    @staticmethod
    def _tag(client_order_id: str) -> str:
        """The ordertag: the client order id itself when it fits Angel's 20 characters, else the first 20 of its SHA-256 (the id stays in Athena's ledger)."""
        import hashlib

        return client_order_id if 0 < len(client_order_id) <= TAG_LIMIT else hashlib.sha256(client_order_id.encode()).hexdigest()[:TAG_LIMIT]

    def _place(self, request: OrderRequest, client_order_id: str) -> str:
        if request.side not in ("BUY", "SELL") or request.product != "CNC":
            raise BrokerApiError("only delivery BUY or SELL limit orders can be sent")
        symbol, token = self._token_for(request)
        body = {
            "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token, "transactiontype": request.side, "exchange": request.exchange,
            "ordertype": "LIMIT", "producttype": "DELIVERY", "duration": "DAY", "price": f"{request.limit_price:.2f}", "squareoff": "0", "stoploss": "0",
            "quantity": str(request.quantity), "ordertag": self._tag(client_order_id),
        }
        data = self._api("POST", PATH_PLACE, body)
        order_id = str(data.get("orderid") or "").strip() if isinstance(data, Mapping) else ""
        if not order_id:
            raise BrokerApiError("angelone accepted the request but its answer has no orderid; check its order book before placing again")
        return order_id

    def _cancel(self, order_id: str) -> None:
        if order_id not in self._varieties:
            self._orders()
        variety = self._varieties.get(order_id)
        if not variety:
            raise BrokerApiError(f"angelone's order book gives no variety for order {order_id}; it will not be guessed")
        self._api("POST", PATH_CANCEL, {"variety": variety, "orderid": order_id})


__all__ = ["AngelOneBroker", "cached_scrip_master", "parse_time", "STATUS_MAP"]
