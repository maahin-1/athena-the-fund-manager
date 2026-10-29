"""Upstox live adapter (REST through `LiveBroker._call`, not the SDK). Delivery limit orders on NSE/BSE, long only.

Documentation read on 2026-10-29 (see docs/brokers/upstox.md for what is and is not confirmed):

  * place order v3        POST   https://api-hft.upstox.com/v3/order/place          https://upstox.com/developer/api-documentation/v3/place-order/
  * cancel order v3       DELETE https://api-hft.upstox.com/v3/order/cancel?order_id=   https://upstox.com/developer/api-documentation/v3/cancel-order/
  * order book            GET    https://api.upstox.com/v2/order/retrieve-all       https://upstox.com/developer/api-documentation/get-order-book/
  * long-term holdings    GET    https://api.upstox.com/v2/portfolio/long-term-holdings   https://upstox.com/developer/api-documentation/get-holdings/
  * funds and margin      GET    https://api.upstox.com/v2/user/get-funds-and-margin   (docs page not readable; model from the SDK docs github.com/upstox/upstox-python/docs/UserFundMarginData.md)
  * order statuses        https://upstox.com/developer/api-documentation/appendix/order-status/
  * token exchange        POST   https://api.upstox.com/v2/login/authorization/token   https://upstox.com/developer/api-documentation/get-token/
  * authorize dialog      https://upstox.com/developer/api-documentation/authorize/
  * instruments           https://assets.upstox.com/market-quote/instruments/exchange/{NSE,complete}.json.gz   https://upstox.com/developer/api-documentation/instruments/
  * rate limits           https://upstox.com/developer/api-documentation/rate-limiting/
  * static IP / algo name https://upstox.com/developer/api-documentation/announcements/algo-trading-circular/

Why v3 for placing and cancelling: Upstox documents the v3 order APIs (host api-hft.upstox.com) as the current ones; v3 returns `data.order_ids` (a list) and `metadata.latency`. Reading uses v2 because no v3 read
endpoints are documented for these three reads.

Daily login: the access token is valid until 03:30 the next morning, whatever time it was made. Each trading day the owner opens `authorize_url(...)`, logs in, and the redirect carries a `code`; `exchange_auth_code`
turns it into the token, which the owner puts in UPSTOX_ACCESS_TOKEN. There is no refresh token for trading. Nothing here automates the browser.

Limits: order APIs (place, modify, cancel) share 10/second, 500/minute, 2000 per 30 minutes (regular algos); other APIs 50/second, 500/minute, 2000 per 30 minutes. Orders must come from a static IP the owner has
registered with Upstox (one primary, one secondary); reading funds, holdings and the order book is not restricted. The pages read name no paid plan for the order API.

Money rules: every number comes from `money()`; a missing field, an unknown status or a reply without an order id is an error, never a guess. An order is `filled` only when Upstox says `complete`."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from athena.brokers.live.base import LEDGER_DIR, BrokerApiError, LiveBroker, Transport, money, requests_transport
from athena.brokers.model import EXCHANGES, Funds, Holding, Order, OrderError, OrderRequest
from athena.llm.envfile import DEFAULT_ENV_FILE

API = "https://api.upstox.com"
HFT_API = "https://api-hft.upstox.com"
AUTHORIZE_URL = f"{API}/v2/login/authorization/dialog"  # the docs page gives the path only; the host is the one the token call uses
TOKEN_URL = f"{API}/v2/login/authorization/token"
FUNDS_URL = f"{API}/v2/user/get-funds-and-margin"
HOLDINGS_URL = f"{API}/v2/portfolio/long-term-holdings"
ORDERS_URL = f"{API}/v2/order/retrieve-all"
PLACE_URL = f"{HFT_API}/v3/order/place"
CANCEL_URL = f"{HFT_API}/v3/order/cancel"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
TAG_MAX = 40  # documented: a longer tag is refused with UDAPI1119
IST = timezone(timedelta(hours=5, minutes=30))

# Upstox status text -> Athena status. Every value in the Order Status appendix (17) is here; anything else raises.
STATUS_MAP: dict[str, str] = {
    "validation pending": "open",
    "put order req received": "open",
    "open pending": "open",
    "trigger pending": "open",
    "after market order req received": "open",
    "open": "open",
    "modify validation pending": "open",
    "modify pending": "open",
    "modify after market order req received": "open",
    "modified": "open",
    "not modified": "open",
    "cancel pending": "open",  # not cancelled until Upstox says `cancelled`
    "not cancelled": "open",
    "complete": "filled",
    "cancelled": "cancelled",
    "cancelled after market order": "cancelled",
    "rejected": "rejected",
}

InstrumentsLoader = Callable[[str], list[dict[str, Any]]]  # loader(exchange) -> the exchange's instrument records (as in NSE.json)
_TAG_OK = re.compile(r"^[A-Za-z0-9_-]{1,%d}$" % TAG_MAX)
_KEY = re.compile(r"^(NSE|BSE)_EQ\|[A-Z0-9]{12}$")
_KEEP = ("segment", "instrument_type", "trading_symbol", "instrument_key", "isin", "exchange", "name")


def authorize_url(api_key: str, redirect_uri: str, state: str) -> str:
    """The page the owner opens each morning to log in. `state` must be random; check it comes back unchanged."""
    return AUTHORIZE_URL + "?" + urlencode({"client_id": api_key, "redirect_uri": redirect_uri, "response_type": "code", "state": state})


def exchange_auth_code(api_key: str, api_secret: str, code: str, redirect_uri: str, transport: Transport | None = None) -> str:
    """Turn the `code` from the login redirect into today's access token. Returns the token only; the caller stores it."""
    send = transport or requests_transport
    status, body = send("POST", TOKEN_URL, headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}, params=None, json=None,
                        data={"code": code, "client_id": api_key, "client_secret": api_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
    if status != 200 or not isinstance(body, dict):
        raise BrokerApiError(f"upstox token exchange answered HTTP {status}", status)
    token = body.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise BrokerApiError("upstox token exchange gave no access token", status)
    return token.strip()


def _ist_today() -> date:
    return datetime.now(IST).date()


def _download_instruments(exchange: str) -> list[dict[str, Any]]:
    """The real loader: one gzip JSON file per exchange. Never used in tests."""
    try:
        reply = requests.get(INSTRUMENTS_URL.format(exchange=exchange), timeout=120)
        reply.raise_for_status()
        data = json.loads(gzip.decompress(reply.content))
    except (requests.RequestException, OSError, ValueError) as exc:
        raise BrokerApiError(f"the upstox instruments file for {exchange} could not be fetched: {type(exc).__name__}") from None
    if not isinstance(data, list):
        raise BrokerApiError(f"the upstox instruments file for {exchange} is not a list")
    return data


def _ist_iso(text: Any, what: str) -> str:
    """Upstox gives `2023-10-19 09:23:23` with no zone; the zone is assumed to be IST (the exchange's). A value that already has an offset keeps it."""
    if not isinstance(text, str) or not text.strip():
        raise BrokerApiError(f"the broker's answer has no usable {what}")
    try:
        moment = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise BrokerApiError(f"the broker's answer has no usable {what}") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=IST)
    return moment.isoformat()


def _whole(value: Any, what: str) -> int:
    number = money(value, what)
    if number != int(number) or number < 0:
        raise BrokerApiError(f"the broker's answer has no usable {what}")
    return int(number)


class UpstoxBroker(LiveBroker):
    name = "upstox"
    SETTINGS = ("UPSTOX_ACCESS_TOKEN",)
    OPTIONAL_SETTINGS = ("UPSTOX_API_KEY", "UPSTOX_API_SECRET", "UPSTOX_REDIRECT_URI")  # only the daily login needs these, not trading calls

    def __init__(self, settings: Mapping[str, str] | None = None, transport: Transport | None = None, ledger_dir: Path | str = LEDGER_DIR, env_file: Path | str = DEFAULT_ENV_FILE,
                 instruments_loader: InstrumentsLoader | None = None, today: Callable[[], date] | None = None):
        super().__init__(settings=settings, transport=transport, ledger_dir=ledger_dir, env_file=env_file)
        if not str(self.settings.get("UPSTOX_ACCESS_TOKEN", "")).strip():
            from athena.contracts import AthenaError
            raise AthenaError("missing in the settings file: UPSTOX_ACCESS_TOKEN")
        self._loader = instruments_loader or _download_instruments
        self._today = today or _ist_today
        self._cache_dir = Path(ledger_dir)

    # --- plumbing ----------------------------------------------------------------------------------------------------------------------------
    def _headers(self, body: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.settings['UPSTOX_ACCESS_TOKEN'].strip()}"}
        if body:
            headers["Content-Type"] = "application/json"
        return headers

    def _checked(self, reply: Any) -> dict[str, Any]:
        if not isinstance(reply, dict) or reply.get("status") != "success":
            raise BrokerApiError("upstox did not report success: " + (json.dumps(reply)[:300] if isinstance(reply, (dict, list)) else str(reply)[:300]))
        return reply

    # --- instruments -------------------------------------------------------------------------------------------------------------------------
    def _instruments(self, exchange: str) -> list[dict[str, Any]]:
        """Equity records of one exchange, from the day's disk cache or (once a day) the loader."""
        path = self._cache_dir / f"upstox-instruments-{exchange}.json"
        today = self._today().isoformat()
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("date") == today and isinstance(cached.get("instruments"), list):
                return cached["instruments"]
        except (OSError, ValueError, AttributeError):
            pass
        raw = self._loader(exchange)
        if not isinstance(raw, list):
            raise BrokerApiError(f"the upstox instruments for {exchange} are not a list")
        kept = [{k: r.get(k) for k in _KEEP} for r in raw if isinstance(r, dict) and r.get("segment") == f"{exchange}_EQ" and r.get("instrument_type") == "EQ"]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"date": today, "exchange": exchange, "instruments": kept}), encoding="utf-8")
        tmp.replace(path)
        return kept

    def instrument_key(self, symbol: str, exchange: str) -> str:
        """`NSE_EQ|ISIN` for one equity symbol; an unknown or ambiguous symbol is an error."""
        wanted = str(symbol).strip().upper()
        matches = {r.get("instrument_key") for r in self._instruments(exchange) if str(r.get("trading_symbol", "")).strip().upper() == wanted}
        if not matches:
            raise BrokerApiError(f"{wanted} is not an equity on {exchange} in the upstox instruments file")
        if len(matches) > 1:
            raise BrokerApiError(f"{wanted} on {exchange} matches more than one upstox instrument ({len(matches)}); refusing to guess")
        key = next(iter(matches))
        if not isinstance(key, str) or not _KEY.match(key) or not key.startswith(f"{exchange}_EQ|"):
            raise BrokerApiError(f"the upstox instrument key for {wanted} is not in the NSE_EQ|ISIN form")
        return key

    # --- reads -------------------------------------------------------------------------------------------------------------------------------
    def _funds(self) -> Funds:
        data = self._checked(self._call("GET", FUNDS_URL, headers=self._headers(), params={"segment": "SEC"})).get("data")
        equity = data.get("equity") if isinstance(data, dict) else None
        if not isinstance(equity, dict):
            raise BrokerApiError("the broker's answer has no equity funds")
        available = money(equity.get("available_margin"), "available_margin")
        used = money(equity.get("used_margin"), "used_margin")
        return Funds(cash=available + used, reserved=used)  # Upstox's available is already net of used, so cash = available + used

    def _holdings(self) -> list[Holding]:
        data = self._checked(self._call("GET", HOLDINGS_URL, headers=self._headers())).get("data")
        if not isinstance(data, list):
            raise BrokerApiError("the broker's answer has no holdings list")
        out = []
        for row in data:
            if not isinstance(row, dict):
                raise BrokerApiError("a holding in the broker's answer is not an object")
            exchange = str(row.get("exchange", "")).strip().upper()
            if exchange not in EXCHANGES:
                raise BrokerApiError(f"a holding has an exchange this release does not handle: {exchange!r}")
            symbol = str(row.get("trading_symbol") or row.get("tradingsymbol") or "").strip().upper()
            if not symbol:
                raise BrokerApiError("a holding has no trading symbol")
            out.append(Holding(symbol=symbol, exchange=exchange, quantity=_whole(row.get("quantity"), "quantity"), average_price=money(row.get("average_price"), "average_price")))
        return out

    def _orders(self) -> list[Order]:
        reply = self._call("GET", ORDERS_URL, headers=self._headers())
        rows = reply if isinstance(reply, list) else self._checked(reply).get("data")  # the docs show both a bare array and a status/data wrapper
        if not isinstance(rows, list):
            raise BrokerApiError("the broker's answer has no order list")
        out = []
        for row in rows:
            if not isinstance(row, dict):
                raise BrokerApiError("an order in the broker's answer is not an object")
            order = self._map_order(row)
            if order is not None:
                out.append(order)
        return out

    def _map_order(self, row: dict[str, Any]) -> Order | None:
        """One order-book row. Orders outside what Athena can place (non-delivery, non-limit, other exchanges) are not Athena's and are left out, not mislabelled."""
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            raise BrokerApiError("an order in the broker's answer has no order id")
        if str(row.get("product", "")).upper() != "D" or str(row.get("order_type", "")).upper() != "LIMIT" or str(row.get("exchange", "")).upper() not in EXCHANGES:
            return None  # placed by hand in the app: skipped before any status or price is read
        status_text = str(row.get("status") or "").strip().lower()
        if status_text not in STATUS_MAP:
            raise BrokerApiError(f"order {order_id} has a status this adapter does not know: {status_text!r}")
        status = STATUS_MAP[status_text]
        try:
            request = OrderRequest(symbol=str(row.get("trading_symbol") or row.get("tradingsymbol") or ""), side=str(row.get("transaction_type", "")), quantity=_whole(row.get("quantity"), "quantity"),
                                   limit_price=money(row.get("price"), "price"), exchange=str(row.get("exchange")))
        except OrderError as exc:
            raise BrokerApiError(f"order {order_id} cannot be read as a delivery limit order: {exc}") from None
        placed_at = _ist_iso(row.get("order_timestamp"), "order_timestamp")
        filled_qty = _whole(row.get("filled_quantity") or 0, "filled_quantity")
        average = money(row.get("average_price") or 0, "average_price")
        filled_at = ""
        if status == "filled":
            if filled_qty <= 0 or average <= 0 or filled_qty != request.quantity:
                raise BrokerApiError(f"order {order_id} is complete but its filled quantity {filled_qty} is not the order quantity {request.quantity}, or it has no average price")
            filled_at = _ist_iso(row.get("exchange_timestamp") or row.get("order_timestamp"), "exchange_timestamp")
        message = str(row.get("status_message") or row.get("status_message_raw") or "").strip()
        if status == "rejected" and not message:
            message = "rejected (the broker gave no reason)"
        return Order(order_id=order_id, client_order_id=str(row.get("tag") or ""), request=request, status=status, filled_quantity=filled_qty, average_price=average,
                     message=message, placed_at=placed_at, filled_at=filled_at)

    # --- writes ------------------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def tag_for(client_order_id: str) -> str:
        """The client order id when Upstox's tag rules allow it (up to 40 characters; letters, digits, - and _ are assumed safe), else a 40-character hash of it."""
        if _TAG_OK.match(client_order_id):
            return client_order_id
        return hashlib.sha256(client_order_id.encode()).hexdigest()[:TAG_MAX]

    def _place(self, request: OrderRequest, client_order_id: str) -> str:
        body = {
            "quantity": request.quantity,
            "product": "D",
            "validity": "DAY",
            "price": request.limit_price,
            "tag": self.tag_for(client_order_id),
            "instrument_token": self.instrument_key(request.symbol, request.exchange),
            "order_type": "LIMIT",
            "transaction_type": request.side,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "slice": False,
        }
        reply = self._checked(self._call("POST", PLACE_URL, headers=self._headers(body=True), json=body))
        ids = (reply.get("data") or {}).get("order_ids") if isinstance(reply.get("data"), dict) else None
        if not isinstance(ids, list) or len(ids) != 1 or not str(ids[0]).strip():
            raise BrokerApiError("upstox accepted the request but did not give exactly one order id; check its order list")
        return str(ids[0]).strip()

    def _cancel(self, order_id: str) -> None:
        self._checked(self._call("DELETE", CANCEL_URL, headers=self._headers(), params={"order_id": order_id}))
