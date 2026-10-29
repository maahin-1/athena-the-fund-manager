"""ICICI Direct (ICICI Securities, Breeze Connect API) live adapter: funds, holdings, today's orders, place and cancel a delivery (cash) limit order.

Docs and SDK source read on 2026-10-29 (nothing here has touched a real account; the breeze-connect package is NOT used, its request scheme is reproduced):
  * https://api.icicidirect.com/breezeapi/documents/index.html   endpoints, request bodies, statuses, checksum, session flow, limits
  * https://github.com/Idirect-Tech/Breeze-Python-SDK  (breeze_connect/breeze_connect.py, config.py)   base URL, header code, body-as-`data`, security master parsing
  * login page: https://api.icicidirect.com/apiuser/login?api_key=<url-encoded app key>

How a call is signed (docs + SDK `generate_headers`): body = compact JSON (`separators=(',', ':')`, `{}` when there is nothing to send), timestamp = UTC `YYYY-MM-DDTHH:MM:SS.000Z`,
X-Checksum = "token " + sha256(timestamp + body + secret_key).hexdigest(), plus X-Timestamp, X-AppKey, X-SessionToken, Content-Type application/json. The body is sent as raw `data` for every verb
including GET and DELETE, and it is the very same string that was signed. The server honours a request only when its clock is within 60 seconds of the timestamp.

Daily session: the daily login gives an `API_Session` code in the browser address bar. `exchange_api_session` sends it with the app key to `customerdetails`; the answer's `session_token`
(base64 of "user_id:session_key") is the ICICI_SESSION_TOKEN used for every later call (the SDK decodes it and re-encodes the same two parts, so the value is used as given).

Choices the docs leave open:
  * Funds: cash = `allocated_equity`, reserved = `block_by_trade_equity` (the per-segment block field; its exact name is inferred from the documented `block_by_trade_*` family and is an error if absent).
  * Holdings: `dematholdings` has quantity but no price, so each held stock's `average_price` comes from `portfolioholdings` (one more call per stock; the shared 100 calls per minute limit applies).
    Quantity = the plain `quantity` field (the docs do not say how `demat_avail_quantity` / `blocked_quantity` relate to it). The from_date used for `portfolioholdings` (2000-01-01) is a guess. Holdings are reported on NSE (the only exchange the docs list for `portfolioholdings`).
  * Order list: asked per exchange (NSE, BSE) for today's date in India. Statuses: Requested, Queued, Ordered, Partially Executed, Freezed -> open; Executed -> filled; Cancelled, Expired,
    Partially Executed And Expired, Partially Executed And Cancelled -> cancelled; Rejected -> rejected. Anything else raises. A partly filled order keeps its fill in `filled_quantity` and shows
    `open` while it is still working, `cancelled` once the remainder was cancelled or expired (the fill stays on the order).
    Filled = quantity - `pending_quantity` - `cancelled_quantity`, both required (a missing one is an error, not 0); Executed is `filled` only when that equals the order quantity, else an error.
    Rows that are not product_type Cash and order_type Limit (the person's own orders) are skipped before their status or price is read.
  * `order_datetime` looks like `05-Feb-2025 09:26` with no zone; it is ASSUMED to be India time (UTC+05:30) and written as ISO-8601 with that offset.
  * `user_remark` (documented: no spaces or special characters) carries the client order id when it is alphanumeric, otherwise "A" + 19 hex characters of its sha256. It is also read back on the order list.
  * Stock code: Breeze `stock_code` is not the NSE symbol for many stocks. It is looked up in the security master (zip of text files; NSEScripMaster.txt, BSEScripMaster.txt) through an injectable loader,
    cached on disk under `.athena/broker/`. A symbol not in it is an error; a Breeze code not in it (on an order or holding) is an error.
  * Only delivery limit orders, validity `day`, long only, on NSE or BSE.
Limits (docs): 100 calls per minute, 5000 per day, and a shared 10 orders per second for place, cancel, modify and square-off. Orders must come from the static IP registered with ICICI Direct when the
API key was made (changeable once a week). The docs do not mention a separate switch for order placement; it is not confirmed whether the key needs one."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.brokers.live.base import LEDGER_DIR, BrokerApiError, LiveBroker, Transport, money, requests_transport
from athena.brokers.model import Funds, Holding, Order, OrderError, OrderRequest

BASE_URL = "https://api.icicidirect.com/breezeapi/api/v1/"
SECURITY_MASTER_URL = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
IST = timezone(timedelta(hours=5, minutes=30))
MASTER_FILES = {"NSE": "NSEScripMaster.txt", "BSE": "BSEScripMaster.txt"}
MASTER_MAX_AGE_DAYS = 7
SESSION_HELP = "the ICICI session token expired or is invalid (it lapses every day); log in again and run exchange_api_session to get a new ICICI_SESSION_TOKEN"

# Breeze status -> Athena status; anything else is an error.
_STATUS = {
    "REQUESTED": "open", "QUEUED": "open", "ORDERED": "open", "PARTIALLY EXECUTED": "open", "FREEZED": "open",
    "EXECUTED": "filled",
    "CANCELLED": "cancelled", "EXPIRED": "cancelled", "PARTIALLY EXECUTED AND EXPIRED": "cancelled", "PARTIALLY EXECUTED AND CANCELLED": "cancelled",
    "REJECTED": "rejected",
}

MasterLoader = Callable[[], Mapping[str, Mapping[str, str]]]  # -> {"NSE": {symbol: breeze stock_code}, "BSE": {...}}


def checksum(timestamp: str, body: str, secret_key: str) -> str:
    """The documented signature: SHA256 of timestamp + body + secret key, as lower-case hex."""
    return hashlib.sha256((timestamp + body + secret_key).encode("utf-8")).hexdigest()


def timestamp_of(moment: datetime) -> str:
    """UTC, to the second, `.000Z` (the SDK's `isoformat()[:19] + '.000Z'`)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def compact(body: Mapping[str, Any]) -> str:
    return json.dumps(body, separators=(",", ":"))


def signed_headers(app_key: str, secret_key: str, session_token: str, body: str, moment: datetime) -> dict[str, str]:
    stamp = timestamp_of(moment)
    return {"Content-Type": "application/json", "X-Checksum": "token " + checksum(stamp, body, secret_key), "X-Timestamp": stamp, "X-AppKey": app_key, "X-SessionToken": session_token}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def exchange_api_session(app_key: str, api_session: str, transport: Transport | None = None) -> str:
    """The daily step after the browser login: trade the `API_Session` code for the session token (ICICI_SESSION_TOKEN). Needs no secret key and no signature (the docs and the SDK send it plain)."""
    send = transport or requests_transport
    status, answer = send("GET", BASE_URL + "customerdetails", headers={"Content-Type": "application/json"}, params=None, json=None, data=compact({"SessionToken": api_session, "AppKey": app_key}))
    if status != 200:
        raise BrokerApiError(f"icici answered HTTP {status} to the session exchange", status)
    success = _success(answer)
    token = success.get("session_token") if isinstance(success, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise BrokerApiError("icici's answer has no session_token")
    try:
        decoded = base64.b64decode(token.encode("ascii"), validate=True).decode("ascii")
    except (ValueError, UnicodeError):
        raise BrokerApiError("icici's session_token is not the documented base64") from None
    if ":" not in decoded:
        raise BrokerApiError("icici's session_token is not the documented user:session form")
    return token.strip()


def _success(answer: Any) -> Any:
    """The envelope is {Success, Status, Error}; a non-200 Status or a non-null Error is an error."""
    if not isinstance(answer, dict) or "Status" not in answer:
        raise BrokerApiError("icici's answer is not in the documented {Success, Status, Error} shape")
    if answer.get("Status") != 200 or answer.get("Error"):
        raise BrokerApiError(f"icici refused the request: {str(answer.get('Error') or answer.get('Status'))[:200]}", int(answer["Status"]) if isinstance(answer.get("Status"), int) else 0)
    return answer.get("Success")


# --- the security master ---------------------------------------------------------------------------------------------------------------------
def parse_security_master(archive: bytes) -> dict[str, dict[str, str]]:
    """{"NSE": {NSE symbol: Breeze stock_code}, "BSE": {...}} from the zip. The header names are the SDK's (`ExchangeCode`, `ShortName`) with the stray spaces and quotes removed."""
    out: dict[str, dict[str, str]] = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile:
        raise BrokerApiError("the ICICI security master is not a zip file") from None
    with zf:
        for exchange, name in MASTER_FILES.items():
            members = [m for m in zf.namelist() if m.rsplit("/", 1)[-1].lower() == name.lower()]
            if not members:
                raise BrokerApiError(f"the ICICI security master has no {name}")
            reader = csv.reader(io.TextIOWrapper(zf.open(members[0]), encoding="utf-8", errors="replace"))
            header = [c.strip().strip('"').strip().lower() for c in next(reader, [])]
            if "exchangecode" not in header or "shortname" not in header:
                raise BrokerApiError(f"{name} has no ExchangeCode / ShortName columns")
            sym_at, code_at = header.index("exchangecode"), header.index("shortname")
            table: dict[str, str] = {}
            for row in reader:
                if len(row) <= max(sym_at, code_at):
                    continue
                symbol, code = row[sym_at].strip().strip('"').strip().upper(), row[code_at].strip().strip('"').strip().upper()
                if symbol and code:
                    table.setdefault(symbol, code)
            out[exchange] = table
    return out


def _fetch_master_bytes() -> bytes:  # pragma: no cover - network
    import requests

    try:
        reply = requests.get(SECURITY_MASTER_URL, timeout=60, allow_redirects=False)
    except requests.RequestException as exc:
        raise BrokerApiError(f"the security master download did not complete: {type(exc).__name__}") from None
    if reply.status_code != 200:
        raise BrokerApiError(f"the security master download answered HTTP {reply.status_code}", reply.status_code)
    return reply.content


def download_security_master() -> dict[str, dict[str, str]]:  # pragma: no cover - network; the default loader, never used by tests
    return parse_security_master(_fetch_master_bytes())


class SecurityMaster:
    """Symbol <-> Breeze stock_code, from a loader, kept on disk (`.athena/broker/icici-security-master.json`) for a week."""

    def __init__(self, loader: MasterLoader | None = None, cache_dir: Path | str = LEDGER_DIR, now: Callable[[], datetime] = _utcnow):
        self._loader = loader or download_security_master
        self._path = Path(cache_dir) / "icici-security-master.json"
        self._now = now
        self._data: dict[str, dict[str, str]] | None = None

    def _load(self) -> dict[str, dict[str, str]]:
        if self._data is not None:
            return self._data
        try:
            cached = json.loads(self._path.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(cached["fetched_at"])
            if self._now() - fetched < timedelta(days=MASTER_MAX_AGE_DAYS) and isinstance(cached["tables"], dict):
                self._data = {k: dict(v) for k, v in cached["tables"].items()}
                return self._data
        except (OSError, ValueError, KeyError, TypeError):
            pass
        tables = {k: dict(v) for k, v in self._loader().items()}
        if not tables.get("NSE") and not tables.get("BSE"):
            raise BrokerApiError("the ICICI security master came back empty")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"fetched_at": self._now().isoformat(), "tables": tables}), encoding="utf-8")
        self._data = tables
        return tables

    def stock_code(self, symbol: str, exchange: str) -> str:
        code = self._load().get(exchange, {}).get(symbol.upper())
        if not code:
            raise OrderError(f"{symbol} on {exchange} is not in the ICICI security master, so its Breeze stock_code is unknown")
        return code

    def symbol(self, stock_code: str, exchange: str) -> str:
        found = sorted(s for s, c in self._load().get(exchange, {}).items() if c == stock_code.upper())
        if len(found) != 1:
            raise BrokerApiError(f"icici stock_code {stock_code[:20]} on {exchange} {'matches several symbols' if found else 'is not in the security master'}")
        return found[0]


# --- small readers ---------------------------------------------------------------------------------------------------------------------------
def _whole(value: Any, what: str) -> int:
    number = money(value, what)
    if number != int(number) or number < 0:
        raise BrokerApiError(f"the broker's answer has no usable {what}")
    return int(number)


def _placed_at(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrokerApiError("the broker's answer has no usable order_datetime")
    text = value.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST).isoformat()
        except ValueError:
            continue
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise BrokerApiError("the broker's answer has no usable order_datetime") from None
    return (moment if moment.tzinfo else moment.replace(tzinfo=IST)).isoformat()


def remark_for(client_order_id: str) -> str:
    """`user_remark` allows no spaces or special characters."""
    if re.fullmatch(r"[A-Za-z0-9]{1,40}", client_order_id):
        return client_order_id
    return "A" + hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()[:19]


class IciciBroker(LiveBroker):
    name = "icici"
    SETTINGS = ("ICICI_APP_KEY", "ICICI_SECRET_KEY", "ICICI_SESSION_TOKEN")
    OPTIONAL_SETTINGS: tuple[str, ...] = ()

    def __init__(self, *args: Any, security_master: SecurityMaster | MasterLoader | None = None, clock: Callable[[], datetime] = _utcnow, **kw: Any):
        super().__init__(*args, **kw)
        self._clock = clock
        ledger_dir = self._ledger_path.parent
        self._master = security_master if isinstance(security_master, SecurityMaster) else SecurityMaster(security_master, ledger_dir, clock)
        self._exchange_of: dict[str, str] = {}

    # --- the one request path ----------------------------------------------------------------------------------------------------------------
    def _breeze(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Any:
        text = compact(body or {})
        headers = signed_headers(self.settings["ICICI_APP_KEY"], self.settings["ICICI_SECRET_KEY"], self.settings["ICICI_SESSION_TOKEN"], text, self._clock())
        try:
            answer = self._call(method, BASE_URL + path, headers=headers, data=text)
        except BrokerApiError as exc:
            if exc.status in (401, 403):
                raise BrokerApiError(f"icici answered HTTP {exc.status}: {SESSION_HELP}", exc.status) from None
            raise
        return _success(answer)

    def _window(self) -> tuple[str, str]:
        day = self._clock().astimezone(IST).strftime("%Y-%m-%d")
        return f"{day}T00:00:00.000Z", f"{day}T23:59:59.000Z"

    # --- reads -------------------------------------------------------------------------------------------------------------------------------
    def _funds(self) -> Funds:
        row = self._breeze("GET", "funds")
        if not isinstance(row, dict):
            raise BrokerApiError("the broker's answer has no funds")
        return Funds(cash=money(row.get("allocated_equity"), "allocated equity"), reserved=money(row.get("block_by_trade_equity"), "equity blocked by trades"))

    def _holdings(self) -> list[Holding]:
        rows = self._breeze("GET", "dematholdings")
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise BrokerApiError("the broker's answer has no holdings list")
        out = []
        start, end = "2000-01-01T00:00:00.000Z", self._window()[1]
        for row in rows:
            if not isinstance(row, dict):
                raise BrokerApiError("the broker's answer has an unreadable holding")
            quantity = _whole(row.get("quantity"), "holding quantity")
            if quantity == 0:
                continue
            code = row.get("stock_code")
            if not isinstance(code, str) or not code.strip():
                raise BrokerApiError("the broker's answer has a holding with no stock_code")
            symbol = self._master.symbol(code.strip(), "NSE")
            rows2 = self._breeze("GET", "portfolioholdings", {"exchange_code": "NSE", "from_date": start, "to_date": end, "stock_code": code.strip(), "portfolio_type": ""})
            match = [r for r in (rows2 or []) if isinstance(r, dict) and str(r.get("stock_code", "")).strip().upper() == code.strip().upper()] if isinstance(rows2, list) else []
            if len(match) != 1:
                raise BrokerApiError(f"icici gave no single average price for {symbol}")
            out.append(Holding(symbol, "NSE", quantity, money(match[0].get("average_price"), "holding average price")))
        return out

    def _orders(self) -> list[Order]:
        start, end = self._window()
        out: list[Order] = []
        for exchange in ("NSE", "BSE"):
            rows = self._breeze("GET", "order", {"exchange_code": exchange, "from_date": start, "to_date": end})
            if rows is None:
                continue
            if not isinstance(rows, list):
                raise BrokerApiError("the broker's answer has no order list")
            for row in rows:
                order = self._map(row, exchange)
                if order is None:
                    continue
                self._exchange_of[order.order_id] = exchange
                out.append(order)
        return out

    def _map(self, row: Any, exchange: str) -> Order | None:
        if not isinstance(row, dict):
            raise BrokerApiError("the broker's answer has an unreadable order")
        # An order the person placed by hand (margin or other product, market or other order type) is skipped before its status or price is read, so it can never break the list.
        product, order_type = row.get("product_type"), row.get("order_type")
        if not isinstance(product, str) or not isinstance(order_type, str) or not product.strip() or not order_type.strip():
            raise BrokerApiError("the broker's answer has an order with no product_type or order_type, so it cannot be told apart from a person's own order")
        if product.strip().lower() != "cash" or order_type.strip().lower() != "limit" or exchange not in ("NSE", "BSE"):
            return None
        raw = " ".join(str(row.get("status", "")).split()).upper()
        status = _STATUS.get(raw)
        if status is None:
            raise BrokerApiError(f"icici reported an order status this adapter does not know: {raw[:40] or 'none'}")
        order_id = str(row.get("order_id") or "").strip()
        if not order_id:
            raise BrokerApiError("the broker's answer has an order with no order_id")
        code = row.get("stock_code")
        if not isinstance(code, str) or not code.strip():
            raise BrokerApiError("the broker's answer has an order with no stock_code")
        action = str(row.get("action", "")).strip().upper()
        if action not in ("BUY", "SELL"):
            raise BrokerApiError(f"the broker's answer has an unknown order action: {action[:20] or 'none'}")
        quantity = _whole(row.get("quantity"), "order quantity")
        # The broker's own pending and cancelled quantities decide what is filled; a missing one is an error, never 0 (an open order must not read as filled).
        pending, cancelled = _whole(row.get("pending_quantity"), "pending quantity"), _whole(row.get("cancelled_quantity"), "cancelled quantity")
        filled = quantity - pending - cancelled
        if filled < 0 or filled > quantity:
            raise BrokerApiError("the broker's order quantities do not add up")
        if status == "filled" and filled != quantity:
            raise BrokerApiError(f"icici order {order_id} is executed but its filled quantity {filled} is not the order quantity {quantity}")
        average = money(row.get("average_price"), "order average price") if filled else 0.0
        price = money(row.get("price"), "order price")
        try:
            request = OrderRequest(self._master.symbol(code.strip(), exchange), action, quantity, price, exchange)
        except OrderError as exc:
            raise BrokerApiError(f"the broker's order cannot be read as a delivery limit order: {exc}") from None
        return Order(order_id=order_id, client_order_id=str(row.get("user_remark") or ""), request=request, status=status, filled_quantity=filled, average_price=average,
                     message=f"icici status: {raw.title()}", placed_at=_placed_at(row.get("order_datetime")))

    # --- writes ------------------------------------------------------------------------------------------------------------------------------
    def _place(self, request: OrderRequest, client_order_id: str) -> str:
        body = {
            "stock_code": self._master.stock_code(request.symbol, request.exchange),
            "exchange_code": request.exchange,
            "product": "cash",
            "action": request.side.lower(),
            "order_type": "limit",
            "quantity": str(request.quantity),
            "price": f"{request.limit_price:.2f}",
            "validity": "day",
            "user_remark": remark_for(client_order_id),
        }
        success = self._breeze("POST", "order", body)
        order_id = success.get("order_id") if isinstance(success, dict) else None
        if not isinstance(order_id, (str, int)) or not str(order_id).strip():
            raise BrokerApiError("icici's answer to the order has no order_id")
        return str(order_id)

    def _cancel(self, order_id: str) -> None:
        exchange = self._exchange_of.get(order_id)
        if exchange is None:
            self._orders()
            exchange = self._exchange_of.get(order_id)
        if exchange is None:
            raise BrokerApiError(f"icici has no order {order_id} today")
        self._breeze("DELETE", "order", {"exchange_code": exchange, "order_id": order_id})
