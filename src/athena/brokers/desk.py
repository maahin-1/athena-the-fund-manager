"""The order desk: preview an order, then confirm it with the token the preview gave, and nothing in between can place one.

    preview(request)   checks every limit, works out the charges, writes a line in the audit log, and, only if nothing is breached, issues a one-time token bound to this exact order
    confirm(token)     redeems the token, checks everything again against the book as it is now, and sends the order to the broker in the current mode

A single call can never trade. The reason text a person gives goes in the audit log and nowhere else; no model output reaches either call. In `live_delivery` mode the desk refuses while no live
adapter exists. A broker's error text is redacted and wrapped before it is shown or logged."""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena.brokers import human, safety
from athena.brokers.audit import AuditLog, redact
from athena.brokers.model import Broker, Findings, Order, OrderRequest, Price
from athena.brokers.lock import exclusive
from athena.costs import model as costs
from athena.ops.jobs import pid_alive
from athena.trading_calendar import TradingCalendar

QuoteSource = Callable[[str, str], Price | None]
SENDING_NAME = "SENDING"  # beside the KILL file: written by a confirmation before its last look at the kill switch, removed only when the send's outcome is known
SEND_WAIT_SECONDS = 20.0  # how long kill() waits for a send already in transit before calling its outcome unresolved
POLL_SECONDS = 0.05
CANCEL_WAIT_SECONDS = 10.0  # how long kill() keeps asking the broker's book whether a cancel it requested has happened, before calling the order still open or unresolved
CANCEL_POLL_SECONDS = 0.5  # one look at the book per this: a real broker rate-limits, and 20 looks are enough to see an ordinary cancel land
# The states a SENDING marker can be in. A marker is never removed while its outcome is unknown.
IN_TRANSIT, OUTCOME_UNKNOWN, PLACED_UNLOGGED = "sending", "outcome_unknown", "placed_unlogged"
STATE_TEXT = {
    IN_TRANSIT: "a send was in progress or never finished",
    OUTCOME_UNKNOWN: "the send failed after the order may have reached the broker",
    PLACED_UNLOGGED: "the order was placed but its audit line could not be written",
}


def _is_definite_refusal(exc: BaseException) -> bool:
    """Whether a failed send is known NOT to have placed an order: the broker answered with an HTTP 4xx (an adapter's `BrokerApiError.status`). Anything else (a timeout, a dropped connection, an
    answer with no order id, an unknown error) may have reached the broker."""
    status = getattr(exc, "status", None)
    return isinstance(status, int) and not isinstance(status, bool) and 400 <= status < 500


class Refused(Exception):
    """The desk will not send this order; `findings` says which rules it broke."""

    def __init__(self, message: str, findings: Findings | None = None):
        super().__init__(message)
        self.findings = findings or Findings()


class BrokerError(Exception):
    """The broker failed. The text has been redacted: it is untrusted and may echo a credential."""


@dataclass
class Preview:
    request: OrderRequest
    mode: str
    notional: float
    charges: float
    findings: Findings
    token: str = ""
    expires: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.findings.ok

    def lines(self) -> list[str]:
        r = self.request
        out = [f"{r.side} {r.quantity} {r.symbol} ({r.exchange}) at a limit of Rs {r.limit_price:,.2f}, delivery, mode {self.mode}", f"worth Rs {self.notional:,.2f}; estimated charges Rs {self.charges:,.2f}"]
        out += [f"  {'ok    ' if ok else 'BREACH'} {rule}: {text}" for rule, ok, text in self.findings.items]
        out += [f"  UNAVAIL {rule}: {text}" for rule, text in self.findings.unavailable]  # a protection that could not be measured is never listed as ok
        out += [f"note: {n}" for n in self.notes]
        out.append(f"confirm with: python -m athena.brokers confirm {self.token}  (once, before {self.expires})" if self.ok else "refused: nothing was issued, so nothing can be confirmed")
        return out


class Desk:
    def __init__(
        self, broker: Broker, limits: safety.Limits, quote: QuoteSource, audit: AuditLog | None = None, tokens: safety.Tokens | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), kill_path: Path = safety.KILL_PATH, charges: dict[str, Any] | None = None,
        human: Callable[[str, str], bool] | None = None, limits_pinned: Callable[[], bool] | None = None, calendar: TradingCalendar | None = None,
        send_wait: float = SEND_WAIT_SECONDS, poll: float = POLL_SECONDS, sleep: Callable[[float], None] = time.sleep,
        cancel_wait: float = CANCEL_WAIT_SECONDS, cancel_poll: float = CANCEL_POLL_SECONDS,
    ):
        """`human(phrase, summary)` asks a person at a console to type `phrase`; `limits_pinned()` says whether the limits file is the one a person pinned. A live broker needs both, and a keyed audit log.
        `calendar` is the exchange's holidays (see `safety.load_holidays`; None means unknown). `send_wait` is how long `kill()` waits for a send already in transit before calling it unresolved;
        `cancel_wait` and `cancel_poll` are how long, and how often, it looks at the broker's book to see a requested cancel happen."""
        self.broker, self.limits, self._quote, self._clock, self._kill = broker, limits, quote, clock, kill_path
        self.audit = audit or AuditLog()
        self._lock = Path(kill_path).parent / "desk.lock"
        self._sending = Path(kill_path).parent / SENDING_NAME
        self.tokens = tokens or safety.Tokens(clock=clock)
        self._charges = charges if charges is not None else costs.load()
        self._human, self._limits_pinned = human, limits_pinned
        self._calendar, self._send_wait, self._poll, self._sleep = calendar, send_wait, poll, sleep
        self._cancel_wait, self._cancel_poll = cancel_wait, cancel_poll

    def _kill_is_set(self) -> bool:
        return self._kill.exists()

    def _findings(self, request: OrderRequest) -> Findings:
        found = safety.check(request, self.broker, self.limits, self._quote(request.symbol, request.exchange), self._clock(), self._kill, self._calendar)
        if getattr(self.broker, "live", False):  # what a real order needs beyond the limits: none of it can be switched on by a tool
            pinned = bool(self._limits_pinned and self._limits_pinned())
            found.add("limits pinned", pinned, "the limits file is the one a person pinned" if pinned else "the limits file has not been pinned, or changed since: run python -m athena.brokers pin-limits at a console")
            key = getattr(self.audit, "key", None)
            keyed = isinstance(key, (bytes, bytearray)) and len(key) == 32
            intact = keyed and self.audit.verify(bytes(key))[0]
            found.add("signed audit log", bool(intact), "the audit log is signed and intact" if intact else "the audit log is not signed with a good key, or does not verify (python -m athena.brokers audit --verify)")
            found.add("person at the console", self._human is not None, "confirmation is typed by a person" if self._human else "no console to confirm at: a live order is confirmed by typing it at a terminal")
        if callable(getattr(self.broker, "unresolved", None)):  # a broker that keeps a ledger of sends whose answer was lost: any one of them is an order that may be out there, whatever this order is
            pending = self._unresolved_sends()
            found.add("no unresolved send", not pending, "the broker has no send whose outcome is unknown" if not pending else "; ".join(pending) + ": look at the broker's own order list, then run python -m athena.brokers resolve-send")
        return found

    def preview(self, request: OrderRequest) -> Preview:
        if len(request.reason.strip()) < 3:
            raise Refused("say why, in your own words: the reason is kept in the audit log")
        findings = self._findings(request)
        fee = costs.leg(self._charges, "delivery", request.side.lower(), request.value)["total"]
        notes = [] if self._charges.get("verified") else ["the charge rates have not been checked against a contract note (.athena/charges.json)"]
        view = Preview(request, self.limits.mode, request.value, round(fee, 2), findings, notes=notes)
        if findings.ok:
            view.token, expires = self.tokens.issue(request, self.limits.mode)
            view.expires = expires.astimezone(safety.IST).strftime("%H:%M:%S")
        self.audit.append("preview", request=request.to_dict(), ok=findings.ok, breaches=[f"{r}: {t}" for r, _, t in findings.breaches], issued=bool(view.token),
                          unavailable=[f"{r}: {t}" for r, t in findings.unavailable])
        return view

    def confirm(self, token: str) -> Order:
        with exclusive(self._lock):  # redeeming, checking, placing and logging are one step: a second confirm waits for this one
            return self._confirm(token)

    def _confirm(self, token: str) -> Order:
        request, why = self.tokens.redeem(token)
        if request is None:
            self.audit.append("confirm_refused", why=why)
            raise Refused(why)
        findings = self._findings(request)  # the book, the clock and the kill switch may have changed since the preview
        if not findings.ok:
            self.audit.append("confirm_refused", request=request.to_dict(), breaches=[f"{r}: {t}" for r, _, t in findings.breaches])
            raise Refused("an order rule is now broken: " + "; ".join(f"{r}: {t}" for r, _, t in findings.breaches), findings)
        if self.limits.mode == "live_delivery" and not getattr(self.broker, "live", False):
            self.audit.append("confirm_refused", request=request.to_dict(), why="no live adapter")
            raise Refused("no live broker adapter is built in this release")
        if self.limits.mode == "paper" and getattr(self.broker, "live", False):
            self.audit.append("confirm_refused", request=request.to_dict(), why="paper mode with a live broker")
            raise Refused("the mode is paper but the broker is live: change nothing; fix the settings file")
        if getattr(self.broker, "live", False):
            phrase = human.order_phrase(request.side, request.quantity, request.symbol, request.limit_price)
            summary = f"{request.side} {request.quantity} {request.symbol} ({request.exchange}) at a limit of Rs {request.limit_price:,.2f}, worth Rs {request.value:,.2f}, through {self.broker.name}"
            if not (self._human and self._human(phrase, summary)):
                self.audit.append("confirm_refused", request=request.to_dict(), why="no typed confirmation from a person")
                raise Refused("a live order is confirmed by typing it at a console; nothing was typed, so nothing was sent")
            findings = self._findings(request)  # the person may have taken a while: the kill switch, the price's age and the clock are looked at again, after the typing and before the send
            if not findings.ok:
                self.audit.append("confirm_refused", request=request.to_dict(), breaches=[f"{r}: {t}" for r, _, t in findings.breaches], why="rules broken while waiting for the person")
                raise Refused("an order rule is now broken: " + "; ".join(f"{r}: {t}" for r, _, t in findings.breaches), findings)
        client_id = "ath-" + request.fingerprint()[:10] + "-" + token.strip()[:6].lower()
        # The send is bracketed by a SENDING marker written BEFORE the last look at the kill switch. kill() writes the switch and THEN looks for the marker, so one of the two always sees the other:
        # either the confirmation finds the switch on and does not send, or kill() finds the marker and waits for the send it cannot otherwise see. (The desk lock is not what kill() waits on:
        # it is held while a person types, and a kill must not wait for that.)
        self._claim_send(request, client_id)
        release = True  # a send that never reached the broker leaves nothing to reconcile; from the moment `place` is called only a KNOWN outcome releases the marker
        try:
            if self._kill_is_set():
                self.audit.append("confirm_refused", request=request.to_dict(), why="the kill switch was set before the send")
                raise Refused("the kill switch is on: the order was not sent")
            self.audit.append("placing", request=request.to_dict(), broker=getattr(self.broker, "name", "?"))  # written before the send: if this cannot be written, nothing is sent
            if self._kill_is_set():  # the last look, immediately before the send: a kill that landed while the line above was written still stops it
                self.audit.append("send_aborted", request=request.to_dict(), why="the kill switch was set while the send was being prepared")
                raise Refused("the kill switch was set while the order was being sent: it was not sent")
            release = False
            try:
                order = self.broker.place(request, client_id)
            except Exception as exc:
                text = redact(f"{type(exc).__name__}: {exc}")
                if _is_definite_refusal(exc):
                    release = True  # the broker answered and refused: nothing was placed, so there is nothing to reconcile
                else:
                    self._update_marker(state=OUTCOME_UNKNOWN, error=text)  # a timeout, a lost answer, anything not a refusal: the order may be at the broker
                try:
                    self.audit.append("place_failed", request=request.to_dict(), error=text, outcome="refused" if release else "unknown")
                except Exception:  # noqa: BLE001 - the marker is the durable record; the error to the caller is the one that matters
                    pass
                raise BrokerError(text) from None
            try:
                self.audit.append("placed", request=request.to_dict(), order_id=order.order_id, client_order_id=client_id, status=order.status, message=order.message, average_price=order.average_price)
            except Exception as exc:  # noqa: BLE001 - the order exists; only its line in the log does not
                self._update_marker(state=PLACED_UNLOGGED, order_id=order.order_id, error=redact(f"{type(exc).__name__}: {exc}"))
                raise BrokerError(redact(
                    f"order {order.order_id} WAS placed through {getattr(self.broker, 'name', '?')} (status {order.status}) but its audit line could not be written ({type(exc).__name__}): "
                    "no further order is sent until a person runs python -m athena.brokers resolve-send"
                )) from None
            release = True
            return order
        finally:
            if release:
                self._release_send()

    # --- a send in transit ---
    def _read_sending(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self._sending.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"pid": 0, "unreadable": True}
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"pid": 0, "unreadable": True}

    def _claim_send(self, request: OrderRequest, client_id: str) -> None:
        """Write the SENDING marker (exclusively). One that is already there is a send that never finished, whether its process is gone or not: nothing new is sent until a person resolves it."""
        self._sending.parent.mkdir(parents=True, exist_ok=True)
        pending = self._unresolved_sends()  # the adapter's own ledger counts too, and an unreadable one cannot show that nothing is unknown
        if pending:
            self.audit.append("confirm_refused", request=request.to_dict(), why="the broker has an unresolved send", unresolved=pending)
            raise Refused("the broker has a send whose outcome is unresolved (" + "; ".join(pending) + "): look at the broker's own order list, then run python -m athena.brokers resolve-send")
        try:
            fd = os.open(self._sending, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            held = self._read_sending() or {}
            self.audit.append("confirm_refused", request=request.to_dict(), why="an earlier send is unresolved", earlier=held)
            raise Refused(
                f"an earlier send ({held.get('client_id', '?')}, pid {held.get('pid', '?')}) never finished and its outcome is unresolved ({STATE_TEXT.get(held.get('state', IN_TRANSIT), 'state unknown')}): "
                "look at the broker's own order list, then run python -m athena.brokers resolve-send"
            ) from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"state": IN_TRANSIT, "pid": os.getpid(), "client_id": client_id, "symbol": request.symbol, "side": request.side, "quantity": request.quantity, "since": self._clock().isoformat()}))

    def _update_marker(self, **changes: Any) -> None:
        """Rewrite the SENDING marker with `changes` (replace in one step). If it cannot be rewritten it stays as it was: still a marker, so still blocking."""
        held = self._read_sending()
        if held is None or held.get("unreadable"):
            return
        held.update(changes)
        tmp = self._sending.with_name(self._sending.name + ".tmp")
        try:
            tmp.write_text(json.dumps(held), encoding="utf-8")
            os.replace(tmp, self._sending)
        except OSError:
            tmp.unlink(missing_ok=True)

    def _release_send(self) -> None:
        self._sending.unlink(missing_ok=True)

    def _unresolved_sends(self) -> list[str]:
        """The adapter's sends whose answer was lost (empty for a broker that keeps no such ledger). A ledger that cannot be read is reported as unresolved: it cannot show that nothing is unknown."""
        unknown = getattr(self.broker, "unresolved", None)
        if not callable(unknown):
            return []
        try:
            return [f"{key} (broker answer lost)" for key in unknown()]
        except Exception as exc:  # noqa: BLE001
            return [f"the broker's unresolved sends could not be read ({type(exc).__name__})"]

    def resolve_send(self, note: str) -> None:
        """A person's decision that unresolved sends have been looked at (the broker's own order list) and can be forgotten. Nothing calls this by itself.

        It clears the desk's SENDING marker and, together with it, every entry in the adapter's own unresolved ledger (`broker.resolve`), and writes one `send_resolved` audit line naming both. A ledger
        that cannot be read is not cleared on a guess: nothing changes until it is repaired. The marker goes last, so a failure part-way leaves trading blocked, never open."""
        if len(note.strip()) < 3:
            raise Refused("say what you checked: the note is kept in the audit log")
        earlier = self._read_sending()
        keys: list[str] = []
        unknown = getattr(self.broker, "unresolved", None)
        if callable(unknown):
            try:
                keys = [str(k) for k in unknown()]
            except Exception as exc:  # noqa: BLE001
                raise Refused(f"the broker's unresolved sends could not be read ({type(exc).__name__}): nothing was resolved; repair the ledger first") from None
            resolver = getattr(self.broker, "resolve", None)
            if keys and not callable(resolver):
                raise Refused("the broker keeps unresolved sends but has no way to resolve them: nothing was resolved")
            for key in keys:
                resolver(key)
        self.audit.append("send_resolved", note=note, earlier=earlier, resolved_ledger=keys)
        self._sending.unlink(missing_ok=True)

    def cancel(self, order_id: str) -> Order:
        # cancelling is always allowed, kill switch or not: it can only reduce what is out there
        try:
            order = self.broker.cancel(order_id)
        except KeyError as exc:
            raise Refused(str(exc).strip("'\"")) from None
        self.audit.append("cancelled", order_id=order_id, status=order.status)
        return order

    def kill(self) -> "KillReport":
        """Switch trading off, wait for a send already in transit, and cancel every open order. Returns the ids cancelled, as a `KillReport` (a list) that also says what the sweep could not undo.

        The switch is written first and at once. Then the SENDING marker is looked at: a confirmation that already wrote it is polled for up to `send_wait` seconds, and when it finishes its order is
        found among the broker's orders, cancelled if still open or reported as `filled_in_transit` (a file cannot undo a fill). A send that does not finish in that time, one left by a process that
        died, and a live adapter's orders of unknown outcome are `unresolved`: trading stays off and a `BrokerError` says so. This never waits for the desk lock, which is held while a person types."""
        self._kill.parent.mkdir(parents=True, exist_ok=True)
        self._kill.write_text(self._clock().isoformat(), encoding="utf-8")
        report = KillReport()
        marker = self._read_sending()
        client_id = (marker or {}).get("client_id")
        if marker is not None:
            report.in_transit = marker.get("state", IN_TRANSIT) == IN_TRANSIT
            pid = int(marker.get("pid", 0) or 0)
            if report.in_transit and not marker.get("unreadable") and pid and pid_alive(pid):  # only a send still in flight is worth waiting for: one that already ended unknown will not change
                for _ in range(max(1, int(self._send_wait / self._poll))):
                    held = self._read_sending()
                    if held is None or held.get("state", IN_TRANSIT) != IN_TRANSIT:
                        break
                    self._sleep(self._poll)
            held = self._read_sending()
            if held is not None:
                what = STATE_TEXT.get(held.get("state", IN_TRANSIT), "the state of the send is unknown")
                extra = f", order {held['order_id']}" if held.get("order_id") else ""
                report.unresolved.append(f"{client_id or 'a send of unknown identity'} ({what}{extra})")
        try:
            orders = list(self.broker.orders())
        except Exception as exc:  # noqa: BLE001 - the switch is already on; say that the open orders could not even be listed
            self.audit.append("kill", cancelled=[], error=f"could not list orders: {type(exc).__name__}", in_transit=report.in_transit, unresolved=report.unresolved)
            raise BrokerError(redact(f"trading is off but the open orders could not be listed ({type(exc).__name__}): cancel them at your broker")) from None
        report.filled_in_transit = [o.order_id for o in orders if client_id and o.client_order_id == client_id and o.status == "filled"]
        self._cancel_open(report, [o for o in orders if o.status == "open"])
        report.unresolved.extend(self._unresolved_sends())  # a live adapter keeps the orders it sent whose answer was lost
        self.audit.append("kill", cancelled=list(report), failed=report.failed, filled_in_transit=report.filled_in_transit, filled_during_cancel=report.filled_during_cancel, partial=report.partial,
                          unresolved=report.unresolved, in_transit=report.in_transit)
        problems = []
        if report.failed:
            problems.append("these orders could not be cancelled, do it at your broker: " + "; ".join(report.failed))
        if report.unresolved:
            problems.append("a send was in progress or never finished, or a cancel could not be confirmed, and its outcome is unresolved (" + "; ".join(report.unresolved) + "): look at your broker's own order list, and a file cannot undo a fill")
        if problems:
            raise BrokerError(redact("trading is off; " + "; and ".join(problems + report.fill_notes())))
        return report

    def _cancel_open(self, report: "KillReport", open_orders: list[Order]) -> None:
        """Ask the broker to cancel each open order, and call one cancelled only when the broker's own state says so. A cancel that was merely accepted is looked for in the book a bounded number of
        times; what is then still open is `failed`, what cannot be found or read is `unresolved`, a fill that raced the cancel is `filled_during_cancel`, and a part-fill is `partial`. One order that will
        not cancel never stops the others being tried."""
        waiting: dict[str, Order] = {}  # cancel accepted, not yet confirmed by the book: order id -> the order as originally listed
        for order in open_orders:
            try:
                after = self.broker.cancel(order.order_id)
            except Exception as exc:  # noqa: BLE001
                report.failed.append(f"{order.order_id}: {type(exc).__name__}")
                continue
            if not report.settle(order.order_id, after):
                waiting[order.order_id] = order
        seen: dict[str, Order] | None = None
        read_error = ""
        rounds = max(1, int(self._cancel_wait / self._cancel_poll))
        for i in range(rounds):
            if not waiting:
                break
            if i:
                self._sleep(self._cancel_poll)
            try:
                seen = {o.order_id: o for o in self.broker.orders()}
                read_error = ""
            except Exception as exc:  # noqa: BLE001 - a rate limit or a dropped connection: look again, and say so if it never answers
                read_error = type(exc).__name__
                continue
            for order_id in list(waiting):
                if order_id in seen and report.settle(order_id, seen[order_id]):
                    del waiting[order_id]
        for order_id, order in waiting.items():
            there = (seen or {}).get(order_id)
            if seen is None:
                report.unresolved.append(f"{order_id} (cancel requested; could not be confirmed: the order book could not be read{f' ({read_error})' if read_error else ''})")
            elif read_error and there is None:
                report.unresolved.append(f"{order_id} (cancel requested; could not be confirmed: the order book could not be read ({read_error}))")
            elif there is None:
                report.unresolved.append(f"{order_id} (cancel requested; the order was not found at the broker afterwards)")
            elif there.status == "open":
                done = f", {there.filled_quantity} of {there.request.quantity} filled" if there.filled_quantity else ""
                report.failed.append(f"{order_id}: still open after the cancel request{done}")
            else:
                report.failed.append(f"{order_id}: {there.status}, not confirmed cancelled")

    def resume(self) -> None:
        held = self._read_sending()
        if held is not None:
            raise Refused(
                f"an unresolved send is on record ({held.get('client_id', '?')}; {STATE_TEXT.get(held.get('state', IN_TRANSIT), 'state unknown')}): "
                "look at the broker's own order list, then run python -m athena.brokers resolve-send before trading again"
            )
        pending = self._unresolved_sends()
        if pending:
            raise Refused("the broker has an unresolved send (" + "; ".join(pending) + "): look at the broker's own order list, then run python -m athena.brokers resolve-send before trading again")
        self._kill.unlink(missing_ok=True)
        self.audit.append("resume")


class KillReport(list):
    """The ids `kill()` confirmed cancelled (the broker's own state said so), with what it could not undo or confirm: `filled_in_transit` (an order that filled while the switch was being thrown),
    `filled_during_cancel` (one that filled while its cancel was in flight) and `partial` (a part-fill, whether the rest cancelled or not) are reported, not undone; `unresolved` (a send, or a
    cancel, whose outcome is not known), `failed` (an order that would not cancel, or is still open), and whether a send was `in_transit` when it began."""

    def __init__(self, ids: Iterable[str] = ()):
        super().__init__(ids)
        self.filled_in_transit: list[str] = []
        self.filled_during_cancel: list[str] = []
        self.partial: list[str] = []
        self.unresolved: list[str] = []
        self.failed: list[str] = []
        self.in_transit = False

    def settle(self, order_id: str, state: Any) -> bool:
        """File what the broker says an order is now. True when that is final here (cancelled, or filled); False when it is anything else and still needs confirming."""
        status = getattr(state, "status", None)
        if status == "cancelled":
            self.append(order_id)
            if getattr(state, "filled_quantity", 0):
                self.partial.append(f"{order_id}: {state.filled_quantity} of {state.request.quantity} filled")
            return True
        if status == "filled":
            self.filled_during_cancel.append(order_id)
            return True
        return False

    def fill_notes(self) -> list[str]:
        notes = []
        if self.filled_in_transit:
            notes.append(f"filled while the switch was being thrown: {', '.join(self.filled_in_transit)} (a file cannot undo a fill; sell at your broker if you want out)")
        if self.filled_during_cancel:
            notes.append(f"filled while its cancel was in flight: {', '.join(self.filled_during_cancel)} (a file cannot undo a fill; sell at your broker if you want out)")
        if self.partial:
            notes.append("part-filled: " + "; ".join(self.partial) + " (the filled part stays; a file cannot undo a fill)")
        return notes

    def message(self) -> str:
        return "; ".join(["trading is off; cancelled: " + (", ".join(self) or "nothing was open"), *self.fill_notes()])
