"""A live price stream from Angel One's SmartAPI WebSocket, for the dashboard's price panel. Read-only: it logs in and subscribes to prices,
and never touches an order, position or funds call. See docs/superpowers/specs/2026-10-13-phase-2j-live-stream-design.md.

The stream runs in its own thread and keeps the latest tick and the day's ticks for each watched symbol. Anything that goes wrong (no
settings, a refused login, a dropped socket) leaves the feed without a quote, and the caller falls back to the polled exchange page."""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from athena.brokers.angel_login import Session, login, settings
from athena.clock import utc_now
from athena.contracts import AthenaError
from athena.live.quotes import DEPTH, IST, MAX_TICKS, Quote
from athena.llm.envfile import DEFAULT_ENV_FILE

MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
MASTER_CACHE = Path(".athena") / "angel_master.json"
MASTER_MAX_AGE = timedelta(hours=20)
NSE_EXCHANGE_TYPE = 1
SNAP_QUOTE = 3
SUFFIXES = ("-EQ", "-BE", "-BZ", "-SM", "-ST")
MAX_WATCHED = 3
FRESH_SECONDS = 15.0
RETRY_AFTER = 60.0  # seconds before a failed start is tried again
SOURCE = "Angel One live stream"


def _price(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number / 100 if number else None  # the stream quotes prices in paise; zero means none


def _levels(rows: Any) -> tuple[tuple[float, float], ...]:
    out = []
    for row in (rows or [])[:DEPTH]:
        price, quantity = _price(row.get("price")), row.get("quantity")
        if price and quantity:
            out.append((price, float(quantity)))
    return tuple(out)


def _stamp(tick: Mapping[str, Any]) -> datetime | None:
    exchange_ms, trade_s = tick.get("exchange_timestamp"), tick.get("last_traded_timestamp")
    try:
        if exchange_ms and float(exchange_ms) > 1e11:
            return datetime.fromtimestamp(float(exchange_ms) / 1000, IST)
        if trade_s and float(trade_s) > 1e9:
            return datetime.fromtimestamp(float(trade_s), IST)
    except (OverflowError, OSError, ValueError):
        pass
    return None


def quote_from_tick(symbol: str, tick: Mapping[str, Any]) -> Quote | None:
    """A `Quote` from one snap-quote tick, or None when the tick holds no price."""
    last, previous = _price(tick.get("last_traded_price")), _price(tick.get("closed_price"))
    if not last or not previous:
        return None
    volume = tick.get("volume_trade_for_the_day")
    average = _price(tick.get("average_traded_price"))
    low_band, high_band = _price(tick.get("lower_circuit_limit")), _price(tick.get("upper_circuit_limit"))
    return Quote(
        symbol, last, previous, _price(tick.get("open_price_of_the_day")), _price(tick.get("high_price_of_the_day")),
        _price(tick.get("low_price_of_the_day")), float(volume) if volume else None,
        average * float(volume) if average and volume else None, _price(tick.get("52_week_high_price")), _price(tick.get("52_week_low_price")),
        f"{low_band:.2f}-{high_band:.2f}" if low_band and high_band else "", None, _stamp(tick),
        _levels(tick.get("best_5_buy_data")), _levels(tick.get("best_5_sell_data")), SOURCE,
    )


class Instruments:
    """Angel One's instrument list: the stream names a stock by a number, so a symbol is looked up once."""

    def __init__(self, load: Callable[[], Sequence[Mapping[str, Any]]] | None = None, cache: Path = MASTER_CACHE, clock: Callable[[], datetime] = utc_now):
        self._load, self._cache, self._clock = load or self._download, cache, clock
        self._tokens: dict[str, str] | None = None

    def _download(self) -> Sequence[Mapping[str, Any]]:
        try:
            if self._cache.exists() and self._clock().timestamp() - self._cache.stat().st_mtime < MASTER_MAX_AGE.total_seconds():
                return json.loads(self._cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        rows = requests.get(MASTER_URL, timeout=60).json()
        nse = [{"token": r["token"], "symbol": r["symbol"]} for r in rows if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).endswith(SUFFIXES)]
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps(nse), encoding="utf-8")
        except OSError:
            pass
        return nse

    def token(self, symbol: str) -> str | None:
        if self._tokens is None:
            self._tokens = {str(r["symbol"]): str(r["token"]) for r in self._load() if str(r.get("symbol", "")).endswith(SUFFIXES)}
        for suffix in SUFFIXES:
            token = self._tokens.get(symbol + suffix)
            if token:
                return token
        return None


class AngelFeed:
    """Watches symbols on Angel One's price stream. `watch` starts the stream on first use (the login and the socket run in a thread, so the
    caller never waits); `latest` gives the newest tick as a `Quote` when it arrived within `FRESH_SECONDS`, else None."""

    def __init__(
        self,
        credentials: Callable[[], dict[str, str]],
        instruments: Instruments | None = None,
        socket: Callable[..., Any] | None = None,
        log_in: Callable[..., Session] = login,
        clock: Callable[[], float] = time.time,
        threaded: bool = True,
    ):
        self._credentials, self._instruments = credentials, instruments or Instruments()
        self._socket, self._log_in, self._clock, self._threaded = socket, log_in, clock, threaded
        self._lock = threading.Lock()
        self._ws: Any = None
        self._starting = False
        self.failure: str | None = None  # why the stream is not running, for a status line (never a secret)
        self._failed_at = 0.0
        self._watched: list[str] = []
        self._by_token: dict[str, str] = {}
        self._latest: dict[str, tuple[float, Quote]] = {}
        self._ticks: dict[str, list[tuple[datetime, float]]] = {}
        self._subscribed: set[str] = set()

    @classmethod
    def from_settings(cls, env_file: Path | str = DEFAULT_ENV_FILE, **kwargs: Any) -> "AngelFeed | None":
        """A feed when all four Angel One settings are present, else None (the dashboard then just polls)."""
        try:
            settings(env_file)
        except AthenaError:
            return None
        return cls(lambda: settings(env_file), **kwargs)

    def watch(self, symbol: str) -> None:
        with self._lock:
            if self.failure is not None and self._clock() - self._failed_at > RETRY_AFTER:
                self.failure = None
            if symbol in self._watched:
                self._watched.remove(symbol)
            self._watched.append(symbol)
            dropped = self._watched[:-MAX_WATCHED]
            del self._watched[:-MAX_WATCHED]
            start = self._ws is None and not self._starting and self.failure is None
            if start:
                self._starting = True
            ws = self._ws
        for old in dropped:
            self._unsubscribe(old)
        if start:
            if self._threaded:
                threading.Thread(target=self._connect, daemon=True).start()
            else:
                self._connect()
        elif ws is not None:
            self._subscribe(symbol)

    def _subscribe(self, symbol: str) -> None:
        token = self._instruments.token(symbol)
        with self._lock:
            ws = self._ws
            if not token or ws is None or symbol in self._subscribed:
                return
            self._by_token[token] = symbol
            self._subscribed.add(symbol)
        try:
            ws.subscribe("athena", SNAP_QUOTE, [{"exchangeType": NSE_EXCHANGE_TYPE, "tokens": [token]}])
        except Exception:
            with self._lock:
                self._subscribed.discard(symbol)

    def _unsubscribe(self, symbol: str) -> None:
        token = self._instruments.token(symbol)
        with self._lock:
            ws = self._ws
            was = symbol in self._subscribed
            self._subscribed.discard(symbol)
            self._latest.pop(symbol, None)
        if token and ws is not None and was:
            try:
                ws.unsubscribe("athena", SNAP_QUOTE, [{"exchangeType": NSE_EXCHANGE_TYPE, "tokens": [token]}])
            except Exception:
                pass

    def _connect(self) -> None:
        try:
            values = self._credentials()
            session = self._log_in(values["ANGEL_API_KEY"], values["ANGEL_CLIENT_CODE"], values["ANGEL_PIN"], values["ANGEL_TOTP_SECRET"])
            if not session.feed_token:
                raise AthenaError("Angel One gave no feed token")
            factory = self._socket
            if factory is None:
                from SmartApi.smartWebSocketV2 import SmartWebSocketV2 as factory
            ws = factory(session.jwt_token, session.api_key, session.client_code, session.feed_token)
            ws.on_open = self._on_open
            ws.on_data = self._on_data
            ws.on_error = lambda *a: None
            with self._lock:
                self._ws = ws
        except Exception as exc:
            with self._lock:
                self.failure = str(exc)[:160] if isinstance(exc, AthenaError) else f"the stream could not start: {type(exc).__name__}"
                self._failed_at = self._clock()
                self._starting = False
            return
        with self._lock:
            self._starting = False
        try:
            ws.connect()  # blocks while the socket is open
        except Exception:
            pass

    def _on_open(self, *args: Any) -> None:
        with self._lock:
            watched = list(self._watched)
            self._subscribed.clear()
        for symbol in watched:
            self._subscribe(symbol)

    def _on_data(self, wsapp: Any, message: Any = None) -> None:
        tick = message if message is not None else wsapp
        if not isinstance(tick, Mapping):
            return
        with self._lock:
            symbol = self._by_token.get(str(tick.get("token", "")).strip('"'))
        if symbol is None:
            return
        quote = quote_from_tick(symbol, tick)
        if quote is None:
            return
        with self._lock:
            self._latest[symbol] = (self._clock(), quote)
            ticks = self._ticks.setdefault(symbol, [])
            stamp = quote.updated or datetime.fromtimestamp(self._clock(), IST)
            if not ticks or ticks[-1] != (stamp, quote.last):
                ticks.append((stamp, quote.last))
                del ticks[:-MAX_TICKS]

    def latest(self, symbol: str) -> Quote | None:
        with self._lock:
            held = self._latest.get(symbol)
        if held is None or self._clock() - held[0] > FRESH_SECONDS:
            return None
        return held[1]

    def ticks(self, symbol: str) -> list[tuple[datetime, float]]:
        with self._lock:
            return list(self._ticks.get(symbol, ()))

    def status(self) -> str:
        with self._lock:
            if self.failure:
                return f"stream off ({self.failure})"
            if self._ws is None:
                return "stream connecting"
            return "stream on"

