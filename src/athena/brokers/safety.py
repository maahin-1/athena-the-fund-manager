"""The limits an order must pass and the confirm token that lets it through.

The limits live in `.athena/broker.json`, a file the owner edits by hand. Nothing in this package, and no tool offered to a model, writes to it or changes the mode: a conversation can ask for a
preview, never for a looser limit. Every number has a conservative default, and the default mode is `paper`.

  mode                 read_only (no orders), paper (the paper broker only) or live_delivery (a real broker; none is built in this release, so it refuses)
  max_order_value      rupees one order may be worth
  max_orders_per_day   orders placed today (filled or open)
  max_deployed         rupees held in shares after a buy
  max_daily_loss       once today's realised loss reaches this, no new order is allowed today
  max_price_deviation  a limit more than this share away from the live price is refused (a slipped decimal point)
  allowlist            symbols a live order may name; empty means none (paper ignores it)
  trading hours        09:20 to 15:20 India time on weekdays; exchange holidays are not known here
The kill switch is a file, `.athena/broker/KILL`: while it exists every order is refused. `python -m athena.brokers kill` creates it, `resume` removes it."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from athena.brokers.model import Broker, Findings, OrderRequest, Price

LIMITS_PATH = Path(".athena") / "broker.json"
KILL_PATH = Path(".athena") / "broker" / "KILL"
TOKENS_PATH = Path(".athena") / "broker" / "tokens.json"
MODES = ("read_only", "paper", "live_delivery")
IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_SECONDS = 120
MAX_QUOTE_AGE = 10.0  # seconds: the exchange time on a quote may lag the clock by a few seconds, a minute-old price is not a live one


class LimitsError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    mode: str = "paper"
    max_order_value: float = 10_000.0
    max_orders_per_day: int = 3
    max_deployed: float = 25_000.0
    max_daily_loss: float = 1_000.0
    max_price_deviation: float = 0.05
    allowlist: tuple[str, ...] = ()
    open_time: str = "09:20"
    close_time: str = "15:20"
    enforce_hours: bool = True

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise LimitsError(f"mode must be one of {MODES}")
        for name in ("max_order_value", "max_deployed", "max_daily_loss", "max_price_deviation"):
            if not isinstance(getattr(self, name), (int, float)) or getattr(self, name) < 0:
                raise LimitsError(f"{name} must be a number that is not negative")
        if not isinstance(self.max_orders_per_day, int) or self.max_orders_per_day < 0:
            raise LimitsError("max_orders_per_day must be a whole number that is not negative")


def load_limits(path: Path | str = LIMITS_PATH) -> Limits:
    """The owner's file, or the defaults when there is none. A key it does not know is an error, so a misspelt limit is not silently ignored."""
    p = Path(path)
    if not p.exists():
        return Limits()
    data = json.loads(p.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Limits)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise LimitsError("unknown limit(s) in the file: " + ", ".join(unknown))
    if "allowlist" in data:
        data["allowlist"] = tuple(str(s).upper() for s in data["allowlist"])
    return Limits(**data)


def _clock_time(text: str) -> time:
    hour, minute = text.split(":")
    return time(int(hour), int(minute))


def check(request: OrderRequest, broker: Broker, limits: Limits, price: Price | None, now: datetime, kill_path: Path = KILL_PATH) -> Findings:
    """Every rule on one request, as findings. The request is refused when any rule is a breach."""
    f = Findings()
    f.add("kill switch", not kill_path.exists(), "the kill switch is on: no order is allowed" if kill_path.exists() else "off")
    f.add("mode", limits.mode != "read_only", "orders are off (mode read_only)" if limits.mode == "read_only" else f"mode {limits.mode}")
    f.add("order value", request.value <= limits.max_order_value, f"Rs {request.value:,.2f} against a limit of Rs {limits.max_order_value:,.2f} an order")
    today = now.astimezone(IST).date()
    placed = [o for o in broker.orders() if o.placed_at and datetime.fromisoformat(o.placed_at).astimezone(IST).date() == today]
    f.add("orders today", len(placed) < limits.max_orders_per_day, f"{len(placed)} placed today against a limit of {limits.max_orders_per_day}")
    lost = -sum(o.realised for o in placed if o.realised < 0)
    f.add("daily loss", lost < limits.max_daily_loss if limits.max_daily_loss > 0 else lost == 0, f"Rs {lost:,.2f} realised loss today against a limit of Rs {limits.max_daily_loss:,.2f}")
    if request.side == "BUY":
        deployed = sum(h.quantity * h.average_price for h in broker.holdings())
        open_buys = sum(o.request.value for o in broker.orders() if o.status == "open" and o.request.side == "BUY")
        after = deployed + open_buys + request.value
        f.add("deployed", after <= limits.max_deployed, f"Rs {after:,.2f} in shares after this buy against a limit of Rs {limits.max_deployed:,.2f}")
    if price is None:
        f.add("price check", False, "no live price for this symbol, so the limit price cannot be checked")
    elif price.source and price.source != "stream":
        f.add("price check", False, f"the price is from the {price.source}, not the live stream, so it may be delayed")
    elif price.age_seconds is not None and price.age_seconds > MAX_QUOTE_AGE:
        f.add("price check", False, f"the price is {price.age_seconds:.0f} seconds old (limit {MAX_QUOTE_AGE:.0f})")
    else:
        gap = abs(request.limit_price / price.last - 1)
        f.add("price check", gap <= limits.max_price_deviation, f"limit {request.limit_price:,.2f} is {gap:.1%} from the live price {price.last:,.2f} (limit {limits.max_price_deviation:.0%})")
    if limits.mode == "live_delivery":
        f.add("allowlist", request.symbol in limits.allowlist, "on the allow-list" if request.symbol in limits.allowlist else f"{request.symbol} is not on the allow-list")
        f.add("live broker", bool(getattr(broker, "live", False)), "a live adapter is connected" if getattr(broker, "live", False) else "no live broker adapter is built in this release")
    if limits.enforce_hours:
        local = now.astimezone(IST)
        inside = local.weekday() < 5 and _clock_time(limits.open_time) <= local.time().replace(second=0, microsecond=0) <= _clock_time(limits.close_time)
        f.add("trading hours", inside, f"{local:%a %H:%M} India time" + ("" if inside else f" is outside {limits.open_time} to {limits.close_time} on weekdays"))
    return f


class Tokens:
    """One-time confirm tokens. A token is bound to the exact order it was issued for, lasts `TOKEN_SECONDS`, and works once. Only a hash of it is kept."""

    def __init__(self, path: Path | str = TOKENS_PATH, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._path, self._clock = Path(path), clock

    def _load(self) -> dict[str, Any]:
        return json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self._path)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, request: OrderRequest, mode: str) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(12)
        expires = self._clock() + timedelta(seconds=TOKEN_SECONDS)
        data = {k: v for k, v in self._load().items() if datetime.fromisoformat(v["expires"]) > self._clock() - timedelta(hours=1)}  # old ones are dropped
        data[self._hash(token)] = {"fingerprint": request.fingerprint(), "request": request.to_dict(), "mode": mode, "expires": expires.isoformat(), "used": False}
        self._save(data)
        return token, expires

    def redeem(self, token: str) -> tuple[OrderRequest | None, str]:
        """`(the request, "")` once, then never again; `(None, why)` for an unknown, used or expired token or one whose order does not match its record."""
        data = self._load()
        row = data.get(self._hash(token.strip()))
        if row is None:
            return None, "unknown token"
        if row["used"]:
            return None, "this token was already used"
        if datetime.fromisoformat(row["expires"]) < self._clock():
            return None, "this token has expired: preview the order again"
        request = OrderRequest.from_dict(row["request"])
        if request.fingerprint() != row["fingerprint"]:
            return None, "the order does not match what the token was issued for"
        row["used"] = True
        self._save(data)
        return request, ""
