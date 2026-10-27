"""The order desk: preview an order, then confirm it with the token the preview gave, and nothing in between can place one.

    preview(request)   checks every limit, works out the charges, writes a line in the audit log, and, only if nothing is breached, issues a one-time token bound to this exact order
    confirm(token)     redeems the token, checks everything again against the book as it is now, and sends the order to the broker in the current mode

A single call can never trade. The reason text a person gives goes in the audit log and nowhere else; no model output reaches either call. In `live_delivery` mode the desk refuses while no live
adapter exists. A broker's error text is redacted and wrapped before it is shown or logged."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from athena.brokers import safety
from athena.brokers.audit import AuditLog, redact
from athena.brokers.model import Broker, Findings, Order, OrderRequest, Price
from athena.costs import model as costs

QuoteSource = Callable[[str, str], Price | None]


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
        out += [f"note: {n}" for n in self.notes]
        out.append(f"confirm with: python -m athena.brokers confirm {self.token}  (once, before {self.expires})" if self.ok else "refused: nothing was issued, so nothing can be confirmed")
        return out


class Desk:
    def __init__(
        self, broker: Broker, limits: safety.Limits, quote: QuoteSource, audit: AuditLog | None = None, tokens: safety.Tokens | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), kill_path: Path = safety.KILL_PATH, charges: dict[str, Any] | None = None,
    ):
        self.broker, self.limits, self._quote, self._clock, self._kill = broker, limits, quote, clock, kill_path
        self.audit = audit or AuditLog()
        self.tokens = tokens or safety.Tokens(clock=clock)
        self._charges = charges if charges is not None else costs.load()

    def _findings(self, request: OrderRequest) -> Findings:
        return safety.check(request, self.broker, self.limits, self._quote(request.symbol, request.exchange), self._clock(), self._kill)

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
        self.audit.append("preview", request=request.to_dict(), ok=findings.ok, breaches=[f"{r}: {t}" for r, _, t in findings.breaches], issued=bool(view.token))
        return view

    def confirm(self, token: str) -> Order:
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
        client_id = "ath-" + request.fingerprint()[:10] + "-" + token.strip()[:6].lower()
        try:
            order = self.broker.place(request, client_id)
        except Exception as exc:
            text = redact(f"{type(exc).__name__}: {exc}")
            self.audit.append("place_failed", request=request.to_dict(), error=text)
            raise BrokerError(text) from None
        self.audit.append("placed", request=request.to_dict(), order_id=order.order_id, client_order_id=client_id, status=order.status, message=order.message, average_price=order.average_price)
        return order

    def cancel(self, order_id: str) -> Order:
        # cancelling is always allowed, kill switch or not: it can only reduce what is out there
        try:
            order = self.broker.cancel(order_id)
        except KeyError as exc:
            raise Refused(str(exc).strip("'\"")) from None
        self.audit.append("cancelled", order_id=order_id, status=order.status)
        return order

    def kill(self) -> list[str]:
        """Switch trading off and cancel every open order. Returns the ids cancelled."""
        self._kill.parent.mkdir(parents=True, exist_ok=True)
        self._kill.write_text(self._clock().isoformat(), encoding="utf-8")
        cancelled = [o.order_id for o in self.broker.orders() if o.status == "open" and self.broker.cancel(o.order_id)]
        self.audit.append("kill", cancelled=cancelled)
        return cancelled

    def resume(self) -> None:
        self._kill.unlink(missing_ok=True)
        self.audit.append("resume")
