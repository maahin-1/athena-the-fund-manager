"""What every live broker adapter shares: settings, one HTTP call path, a write-ahead ledger that keeps `place` idempotent, and error text that cannot leak a credential.

An adapter subclasses `LiveBroker` and fills in five methods that talk to one broker's API and map its answers to Athena's shapes (`Funds`, `Holding`, `Order`). Everything else is here, so the
five adapters cannot differ in the parts that matter for money:

  * the HTTP call has a timeout and never follows a redirect to another host with the credentials;
  * a broker's error body is redacted and cut before it is raised;
  * `place` writes its intent to the ledger BEFORE it sends, so a crash or a timeout between the send and the answer is never retried blindly: the next call for the same client order id says the
    outcome is unknown and points at the broker's own order list, instead of sending a second order;
  * an unknown or half-readable answer from the broker is an error, not a guess: an order is `filled` only when the broker says so.

Nothing here decides whether an order may be sent. That is the desk's job (`athena.brokers.desk`), which also refuses a live adapter unless the live-mode prerequisites are met."""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import requests

from athena.brokers.audit import redact
from athena.brokers.lock import exclusive
from athena.brokers.model import Funds, Holding, Order, OrderRequest
from athena.contracts import AthenaError
from athena.llm.envfile import DEFAULT_ENV_FILE, read_setting

LEDGER_DIR = Path(".athena") / "broker"
TIMEOUT_SECONDS = 15.0
Transport = Callable[..., "tuple[int, Any]"]  # transport(method, url, headers=, params=, json=, data=) -> (HTTP status, parsed JSON body or text)


class BrokerApiError(Exception):
    """The broker answered with an error, or something that is not an answer. `status` is the HTTP status (0 when there was none). The text is redacted."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(redact(message))
        self.status = status


class OutcomeUnknown(BrokerApiError):
    """An earlier attempt to place this client order id was sent and no answer was recorded. It may or may not be at the broker: look at its order list before doing anything."""


def load_settings(names: Sequence[str], env_file: Path | str = DEFAULT_ENV_FILE, environ: Mapping[str, str] | None = None, optional: Sequence[str] = ()) -> dict[str, str]:
    """The named settings from the environment or the settings file. A missing required one is an `AthenaError` naming it (never its value)."""
    values = {n: read_setting(n, env_file, environ).strip() for n in (*names, *optional)}
    missing = [n for n in names if not values[n]]
    if missing:
        raise AthenaError("missing in the settings file: " + ", ".join(missing))
    return values


def requests_transport(method: str, url: str, headers: Mapping[str, str] | None = None, params: Mapping[str, Any] | None = None, json: Any = None, data: Any = None) -> tuple[int, Any]:  # noqa: A002
    try:
        reply = requests.request(method, url, headers=dict(headers or {}), params=params, json=json, data=data, timeout=TIMEOUT_SECONDS, allow_redirects=False)
    except requests.RequestException as exc:
        raise BrokerApiError(f"the request did not complete: {type(exc).__name__}") from None
    try:
        body: Any = reply.json()
    except ValueError:
        body = reply.text[:600]
    return reply.status_code, body


def money(value: Any, what: str) -> float:
    """A number from a broker's answer. A missing or non-numeric field is an error: a guess about money is never made."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise BrokerApiError(f"the broker's answer has no usable {what}") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise BrokerApiError(f"the broker's answer has no usable {what}")
    return number


class LiveBroker(ABC):
    """Subclasses set `name`, `SETTINGS` (required setting names), `OPTIONAL_SETTINGS`, and implement the five `_` methods."""

    name = ""
    live = True
    SETTINGS: tuple[str, ...] = ()
    OPTIONAL_SETTINGS: tuple[str, ...] = ()

    def __init__(self, settings: Mapping[str, str] | None = None, transport: Transport | None = None, ledger_dir: Path | str = LEDGER_DIR, env_file: Path | str = DEFAULT_ENV_FILE):
        self.settings = dict(settings) if settings is not None else load_settings(self.SETTINGS, env_file, optional=self.OPTIONAL_SETTINGS)
        self._transport = transport or requests_transport
        self._ledger_path = Path(ledger_dir) / f"{self.name}-orders.json"

    def __repr__(self) -> str:  # a broker object may end up in a log: show no setting
        return f"{type(self).__name__}()"

    # --- one HTTP path -----------------------------------------------------------------------------------------------------------------------
    def _call(self, method: str, url: str, headers: Mapping[str, str] | None = None, params: Mapping[str, Any] | None = None, json: Any = None, data: Any = None, ok: Sequence[int] = (200, 201, 202)) -> Any:  # noqa: A002
        status, body = self._transport(method, url, headers=headers, params=params, json=json, data=data)
        if status not in ok:
            raise BrokerApiError(f"{self.name} answered HTTP {status}: {body if isinstance(body, str) else json_text(body)}", status)
        return body

    # --- what an adapter fills in ------------------------------------------------------------------------------------------------------------
    @abstractmethod
    def _funds(self) -> Funds: ...

    @abstractmethod
    def _holdings(self) -> list[Holding]: ...

    @abstractmethod
    def _orders(self) -> list[Order]:
        """Today's orders, mapped. An order the broker has not filled is `open`; one it rejected is `rejected` with its reason in `message`; `filled_quantity` and `average_price` are the broker's own."""

    @abstractmethod
    def _place(self, request: OrderRequest, client_order_id: str) -> str:
        """Send one delivery limit order, carrying `client_order_id` as the broker's tag where it has one. Returns the broker's order id. Raises `BrokerApiError` if the broker refuses it."""

    @abstractmethod
    def _cancel(self, order_id: str) -> None:
        """Ask the broker to cancel one order. Raises `BrokerApiError` if it refuses."""

    # --- the Broker shape the desk uses -----------------------------------------------------------------------------------------------------
    def funds(self) -> Funds:
        return self._funds()

    def holdings(self) -> list[Holding]:
        return self._holdings()

    def orders(self) -> list[Order]:
        return [self._whole_fills_only(o) for o in self._orders()]

    @staticmethod
    def _whole_fills_only(order: Order) -> Order:
        """`filled` means the whole quantity. A fill of less (a partial fill the broker calls complete, or a number that does not add up) stays open with a message, never a guess that it is done."""
        if order.status == "filled" and order.filled_quantity != order.request.quantity:
            order.status = "open"
            order.message = (order.message + "; " if order.message else "") + f"{order.filled_quantity} of {order.request.quantity} filled"
        return order

    def ping(self) -> str:
        """Prove the credentials work without trading: reads the funds. Returns a one-line summary with no secret in it."""
        funds = self._funds()
        return f"{self.name}: logged in, available Rs {funds.available:,.2f}"

    def place(self, request: OrderRequest, client_order_id: str) -> Order:
        with exclusive(self._ledger_path.with_suffix(".lock")):
            ledger = self._read_ledger()
            entry = ledger.get(client_order_id)
            fingerprint = request.fingerprint()
            if entry is None:
                for other_id, other in ledger.items():  # the same order asked for again (a new preview) after a send whose answer was lost: it may be at the broker already
                    if other.get("state") == "sent" and not other.get("order_id") and other.get("fingerprint") == fingerprint:
                        raise OutcomeUnknown(f"an identical order ({other_id}) was sent to {self.name} and no answer was recorded; look at {self.name}'s own order list, then run python -m athena.brokers resolve {other_id}")
            if entry is not None:
                if not entry.get("order_id"):
                    raise OutcomeUnknown(f"an earlier attempt to place {client_order_id} was sent and no answer was recorded; check {self.name}'s own order list before placing again")
                return self._order_by_id(entry["order_id"], request, client_order_id)
            ledger[client_order_id] = {"state": "sent", "order_id": "", "request": request.to_dict(), "fingerprint": fingerprint}
            self._write_ledger(ledger)  # intent first: if the next line never returns, a retry finds this and stops
            try:
                order_id = self._place(request, client_order_id)
            except BrokerApiError as exc:
                if 400 <= exc.status < 500:  # the broker answered and refused: nothing was placed, so a later identical order is not blocked by this one
                    ledger[client_order_id]["state"] = "refused"
                    self._write_ledger(ledger)
                raise
            if not str(order_id).strip():
                raise OutcomeUnknown(f"{self.name} accepted the request but gave no order id; check its order list for {client_order_id}")
            ledger[client_order_id] = {"state": "done", "order_id": str(order_id), "request": request.to_dict(), "fingerprint": fingerprint}
            self._write_ledger(ledger)
        return self._order_by_id(str(order_id), request, client_order_id)

    def cancel(self, order_id: str) -> Order:
        known = [o for o in self._orders() if o.order_id == str(order_id)]
        if not known:
            raise KeyError(f"no order {order_id} at {self.name}")
        self._cancel(str(order_id))
        after = [o for o in self._orders() if o.order_id == str(order_id)]
        return after[0] if after else known[0]

    # --- helpers -----------------------------------------------------------------------------------------------------------------------------
    def unresolved(self) -> dict[str, Any]:
        """Orders sent whose answer was never recorded. Each may be at the broker."""
        return {k: v for k, v in self._read_ledger().items() if v.get("state") == "sent" and not v.get("order_id")}

    def resolve(self, client_order_id: str) -> bool:
        """A person has looked at the broker's own order list and settled this entry (it is there, or it is not); it stops blocking an identical order."""
        with exclusive(self._ledger_path.with_suffix(".lock")):
            ledger = self._read_ledger()
            if client_order_id not in ledger:
                return False
            ledger[client_order_id]["state"] = "resolved"
            self._write_ledger(ledger)
        return True

    def _order_by_id(self, order_id: str, request: OrderRequest, client_order_id: str) -> Order:
        """The order as the broker reports it now; if its list does not show it yet, an open order carrying what was asked (never a fill that was not reported)."""
        try:
            listed = self._orders()
        except Exception:  # noqa: BLE001 - the order is placed; failing to read it back must not look like a failed placement (a retry would send a second order)
            listed = []
        for order in listed:
            if order.order_id == order_id:
                order.client_order_id = client_order_id
                return self._whole_fills_only(order)
        return Order(order_id=order_id, client_order_id=client_order_id, request=request, status="open", message="placed; not yet in the broker's order list")

    def _read_ledger(self) -> dict[str, Any]:
        try:
            return json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _write_ledger(self, data: dict[str, Any]) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self._ledger_path)


def json_text(body: Any) -> str:
    try:
        return json.dumps(body)[:400]
    except (TypeError, ValueError):
        return str(body)[:400]
