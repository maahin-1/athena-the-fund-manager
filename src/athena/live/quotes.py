"""Live quotes for the dashboard's display: the exchange's own quote snapshot, and today's 1-minute bars from Yahoo for the chart.

Both are free and keyless, and neither is an official feed: the exchange's quote endpoint is the one its website uses and may change, Yahoo's
bars can lag. Nothing here feeds an analyst or a verdict; those read the stored daily prices. A source that fails raises an `AthenaError`
and the panel says so."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

import threading

import requests

from athena.clock import utc_now
from athena.contracts import AthenaError

IST = timezone(timedelta(hours=5, minutes=30))
OPEN_AT, CLOSE_AT = time(9, 15), time(15, 30)
QUOTE_URL = "https://www.nseindia.com/api/NextApi/apiClient/GetQuoteApi"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
STALE_AFTER = timedelta(minutes=5)  # an exchange update older than this while the market is open is flagged
DEPTH = 5
QUOTE_TTL, MINUTES_TTL = 0.5, 15.0  # seconds a quote and the minute bars are remembered
SERIES = ("EQ", "SM", "BE", "BZ", "ST")  # the exchange's answer is empty unless the series is the stock's own; EQ first, as most are
MAX_TICKS = 6000


@dataclass(frozen=True)
class Quote:
    symbol: str
    last: float
    previous_close: float
    open: float | None
    high: float | None
    low: float | None
    volume: float | None
    value: float | None  # traded value, rupees
    year_high: float | None
    year_low: float | None
    price_band: str
    delivery_pct: float | None
    updated: datetime | None  # the exchange's own timestamp, IST
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]

    @property
    def change(self) -> float:
        return self.last - self.previous_close

    @property
    def change_pct(self) -> float:
        return self.change / self.previous_close * 100 if self.previous_close else 0.0


@dataclass(frozen=True)
class Minute:
    at: datetime  # IST
    close: float
    volume: float


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _side(book: Mapping[str, Any], name: str) -> tuple[tuple[float, float], ...]:
    levels = []
    for i in range(1, DEPTH + 1):
        price, quantity = _number(book.get(f"{name}Price{i}")), _number(book.get(f"{name}Quantity{i}"))
        if price and quantity:
            levels.append((price, quantity))
    return tuple(levels)


def parse_quote(symbol: str, body: Mapping[str, Any]) -> Quote:
    """A `Quote` from the exchange's snapshot, or an `AthenaError` when the answer holds no price."""
    try:
        data = body["equityResponse"][0]
    except (KeyError, IndexError, TypeError):
        raise AthenaError(f"the exchange gave no quote for {symbol}") from None
    meta, trade, price, book = data.get("metaData") or {}, data.get("tradeInfo") or {}, data.get("priceInfo") or {}, data.get("orderBook") or {}
    last, previous = _number(trade.get("lastPrice")), _number(meta.get("previousClose"))
    if not last or not previous:
        raise AthenaError(f"the exchange's quote for {symbol} has no price")
    updated = None
    try:
        updated = datetime.strptime(str(data.get("lastUpdateTime")), "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        pass
    return Quote(
        symbol, last, previous, _number(meta.get("open")), _number(meta.get("dayHigh")), _number(meta.get("dayLow")),
        _number(trade.get("totalTradedVolume")), _number(trade.get("totalTradedValue")), _number(price.get("yearHigh")),
        _number(price.get("yearLow")), str(price.get("priceBand") or ""), _number(trade.get("deliveryToTradedQuantity")), updated,
        _side(book, "buy"), _side(book, "sell"),
    )


def market_is_open(now: datetime) -> bool:
    """Weekday trading hours in India. It does not know exchange holidays: the quote's own update time shows those as stale."""
    local = now.astimezone(IST)
    return local.weekday() < 5 and OPEN_AT <= local.time() <= CLOSE_AT


def staleness(quote: Quote, now: datetime) -> str | None:
    """A sentence when the exchange's last update is old while the market should be open, or when the market is shut."""
    if quote.updated is None:
        return None
    age = now - quote.updated
    if market_is_open(now):
        return f"the exchange's last update is {int(age.total_seconds() // 60)} minutes old" if age > STALE_AFTER else None
    return f"market closed; last update {quote.updated:%d %b %H:%M} IST"


def yahoo_minutes(symbol: str) -> list[Minute]:
    import yfinance as yf

    try:
        frame = yf.Ticker(f"{symbol}.NS").history(period="1d", interval="1m")
    except Exception as exc:  # yfinance raises many kinds of error for one failed request
        raise AthenaError(f"Yahoo gave no minute bars for {symbol}: {type(exc).__name__}") from None
    if frame is None or frame.empty:
        raise AthenaError(f"Yahoo has no minute bars for {symbol} today")
    return [
        Minute(stamp.to_pydatetime().astimezone(IST), float(row["Close"]), float(row["Volume"]))
        for stamp, row in frame.iterrows() if row["Close"] == row["Close"]
    ]


class LiveQuotes:
    """Fetches a quote and the day's minute bars. The quote is remembered for half a second (the exchange's own answer changes about every five
    seconds, so polling faster shows nothing new); the minute bars are refetched in the background every fifteen (a bar completes once a
    minute), so a slow Yahoo never holds up a refresh. Requests share one kept-alive connection, which takes a quote from about 360 ms to
    about 40 ms. Every new exchange answer is kept as a tick so the chart can run on past the last minute bar."""

    def __init__(
        self,
        get: Callable[..., Any] | None = None,
        minutes: Callable[[str], list[Minute]] = yahoo_minutes,
        clock: Callable[[], datetime] = utc_now,
        quote_ttl: float = QUOTE_TTL,
        minutes_ttl: float = MINUTES_TTL,
        background: bool = True,
    ):
        if get is None:
            session = requests.Session()
            session.headers.update(HEADERS)
            get = session.get
        self._get, self._minutes, self._clock, self._background = get, minutes, clock, background
        self._quote_ttl, self._minutes_ttl = quote_ttl, minutes_ttl
        self._quotes: dict[str, tuple[datetime, Quote]] = {}
        self._series: dict[str, str] = {}  # the series that last gave a price for a symbol
        self._ticks: dict[str, list[tuple[datetime, float]]] = {}
        self._bars: dict[str, tuple[datetime, list[Minute] | AthenaError]] = {}
        self._refreshing: set[str] = set()
        self._lock = threading.Lock()

    def _read(self, symbol: str, series: str) -> Quote | None:
        try:
            response = self._get(
                QUOTE_URL, params={"functionName": "getSymbolData", "marketType": "N", "series": series, "symbol": symbol},
                headers=HEADERS, timeout=15,
            )
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise AthenaError(f"the exchange's quote could not be read: {type(exc).__name__}") from None
        try:
            return parse_quote(symbol, body)
        except AthenaError:
            return None

    def quote(self, symbol: str) -> Quote:
        now = self._clock()
        with self._lock:
            held = self._quotes.get(symbol)
            if held is not None and (now - held[0]).total_seconds() < self._quote_ttl:
                return held[1]
            known = self._series.get(symbol)
        quote = None
        for series in ([known] if known else []) + [s for s in SERIES if s != known]:
            quote = self._read(symbol, series)
            if quote is not None:
                with self._lock:
                    self._series[symbol] = series
                break
        if quote is None:
            raise AthenaError(f"the exchange gave no quote for {symbol} (tried series {', '.join(SERIES)})")
        with self._lock:
            self._quotes[symbol] = (now, quote)
            ticks = self._ticks.setdefault(symbol, [])
            stamp = quote.updated or now
            if not ticks or ticks[-1][1] != quote.last or ticks[-1][0] != stamp:
                ticks.append((stamp, quote.last))
                del ticks[:-MAX_TICKS]
        return quote

    def ticks(self, symbol: str) -> list[tuple[datetime, float]]:
        """Every distinct exchange answer seen for the symbol since this process started, oldest first."""
        with self._lock:
            return list(self._ticks.get(symbol, ()))

    def _load_bars(self, symbol: str) -> None:
        try:
            result: list[Minute] | AthenaError = self._minutes(symbol)
        except AthenaError as exc:
            result = exc
        with self._lock:
            self._bars[symbol] = (self._clock(), result)
            self._refreshing.discard(symbol)

    def minutes(self, symbol: str) -> list[Minute]:
        """Today's minute bars. The first call waits for them; after that the held bars are returned at once while a stale set is refreshed
        in the background. An `AthenaError` when the source has none."""
        now = self._clock()
        with self._lock:
            held = self._bars.get(symbol)
            stale = held is None or (now - held[0]).total_seconds() >= self._minutes_ttl
            start = stale and symbol not in self._refreshing
            if start:
                self._refreshing.add(symbol)
        if start:
            if held is None or not self._background:
                self._load_bars(symbol)
            else:
                threading.Thread(target=self._load_bars, args=(symbol,), daemon=True).start()
        with self._lock:
            held = self._bars.get(symbol)
        if held is None:
            return []
        if isinstance(held[1], AthenaError):
            raise held[1]
        return held[1]

    def now(self) -> datetime:
        return self._clock()
